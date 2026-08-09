from bot import keyboards, states
from bot.keyboards import back_menu_keyboard, yes_no_keyboard
from integrations import vk
from services import doctor_notify, pet_service, session, subscription_service
from services import ticket_service, urgency_service, user_service
from services.audit import log_action


def _prompt_attach(peer_id: int, vk_user_id: int, data: dict):
    session.set_state(
        vk_user_id,
        states.TICKET_ATTACH,
        {**data, "pending_attachments": data.get("pending_attachments", [])},
    )
    vk.send_message(
        peer_id,
        "Пришлите фото или видео (можно несколько сообщений) или напишите «пропустить».",
    )


def _prompt_confirm(peer_id: int, vk_user_id: int, data: dict):
    symptoms = data.get("symptoms", {})
    level = urgency_service.classify_urgency(symptoms, data.get("worsening", False))
    if data.get("ticket_type") == "emergency":
        level = "red"
    label = urgency_service.urgency_label(level)
    session.set_state(vk_user_id, states.TICKET_CONFIRM, data)
    vk.send_message(
        peer_id,
        f"Срочность: {label}\nПодтвердите создание заявки (да/нет):",
        yes_no_keyboard("ticket_confirm"),
    )


def start_ticket_flow(peer_id: int, vk_user_id: int, ticket_type: str):
    user = user_service.get_or_create_user(vk_user_id)
    allowed, error = ticket_service.can_start_ticket(user, ticket_type)
    if not allowed:
        if error == "emergency_upsell":
            vk.send_message(
                peer_id,
                "Экстренная помощь доступна на тарифах «Базовый» и «Премиум».\n"
                "Оформите подходящий тариф или разовую консультацию:",
                keyboards.emergency_upsell_keyboard(),
            )
            return
        if error == "emergency_upsell_one_time":
            vk.send_message(
                peer_id,
                "Разовая консультация не включает экстренную помощь.\n"
                "Оформите тариф «Базовый» / «Премиум»:",
                keyboards.emergency_upsell_keyboard(),
            )
            return
        vk.send_message(peer_id, error, back_menu_keyboard())
        return

    pets = pet_service.list_pets(user)
    if not pets:
        vk.send_message(peer_id, "Сначала добавьте питомца.", back_menu_keyboard())
        return
    if len(pets) > 1:
        session.set_state(vk_user_id, states.IDLE, {"ticket_type": ticket_type, "flow": "ticket_pet"})
        vk.send_message(peer_id, "Выберите питомца для заявки:", keyboards.pet_picker_keyboard(pets, "ticket_pet"))
        return
    _begin_ticket(peer_id, vk_user_id, ticket_type, pets[0].id)


def begin_ticket_for_pet(peer_id: int, vk_user_id: int, ticket_type: str, pet_id: int):
    _begin_ticket(peer_id, vk_user_id, ticket_type, pet_id)


def _begin_ticket(peer_id: int, vk_user_id: int, ticket_type: str, pet_id: int):
    user = user_service.get_or_create_user(vk_user_id)
    session.set_active_pet(vk_user_id, pet_id)

    if ticket_type == "emergency":
        session.set_state(
            vk_user_id,
            states.TICKET_EMERGENCY,
            {"ticket_type": ticket_type, "pet_id": pet_id, "symptoms": {}, "worsening": False, "pending_attachments": []},
        )
        vk.send_message(
            peer_id,
            "🚨 Экстренная помощь\n\nКратко опишите, что случилось:",
        )
        return

    prior = ticket_service.list_user_tickets(user)
    prior_same = [t for t in prior if t.pet_id == pet_id]
    session.set_state(
        vk_user_id,
        states.TICKET_WORSENING if prior_same else states.TICKET_COMPLAINTS,
        {"ticket_type": ticket_type, "pet_id": pet_id, "symptoms": {}, "worsening": False, "pending_attachments": []},
    )
    if prior_same:
        vk.send_message(peer_id, "Есть ли ухудшение состояния?", yes_no_keyboard("ticket_worsening"))
    else:
        vk.send_message(peer_id, "Опишите жалобы и симптомы:")


def handle_ticket_worsening(peer_id: int, vk_user_id: int, answer: str):
    worsening = answer == "yes"
    session.update_payload(vk_user_id, worsening=worsening)
    session.set_state(vk_user_id, states.TICKET_COMPLAINTS, session.get_payload(vk_user_id))
    vk.send_message(peer_id, "Опишите жалобы и симптомы:")


def handle_ticket_attachments(peer_id: int, vk_user_id: int, attachment_strings: list[str]) -> bool:
    s = session.get_session(vk_user_id)
    if s.state != states.TICKET_ATTACH:
        return False
    data = session.get_payload(vk_user_id)
    pending = data.get("pending_attachments", [])
    added = len(attachment_strings)
    pending.extend(attachment_strings)
    session.update_payload(vk_user_id, pending_attachments=pending)
    vk.send_message(
        peer_id,
        f"Добавлено: +{added}. Всего файлов: {len(pending)}. Ещё или «пропустить».",
    )
    return True


def handle_ticket_message(peer_id: int, vk_user_id: int, text: str) -> bool:
    s = session.get_session(vk_user_id)
    if not s.state.startswith("ticket_"):
        return False
    data = session.get_payload(vk_user_id)
    symptoms = data.get("symptoms", {})
    state = s.state
    low = text.strip().lower()

    if state == states.TICKET_ATTACH:
        if low in ("пропустить", "skip", "нет", "-"):
            _prompt_confirm(peer_id, vk_user_id, data)
        else:
            vk.send_message(peer_id, "Отправьте фото/видео или напишите «пропустить».")
        return True

    if state == states.TICKET_EMERGENCY:
        symptoms["complaints"] = text.strip()
        data = {**data, "symptoms": symptoms}
        _prompt_attach(peer_id, vk_user_id, data)
        return True

    steps = [
        (states.TICKET_COMPLAINTS, "complaints", states.TICKET_STARTED, "Когда началось?"),
        (states.TICKET_STARTED, "started_at", states.TICKET_ACTIVITY, "Активность:"),
        (states.TICKET_ACTIVITY, "activity", states.TICKET_APPETITE, "Аппетит:"),
        (states.TICKET_APPETITE, "appetite", states.TICKET_STOOL, "Стул:"),
        (states.TICKET_STOOL, "stool", states.TICKET_URINE, "Мочеиспускание:"),
        (states.TICKET_URINE, "urine", states.TICKET_TEMP, "Температура (или —):"),
    ]
    for st, field, next_st, prompt in steps:
        if state == st:
            symptoms[field] = text.strip()
            data = {**data, "symptoms": symptoms}
            if next_st == states.TICKET_TEMP:
                session.set_state(vk_user_id, next_st, data)
                vk.send_message(peer_id, prompt)
            else:
                session.set_state(vk_user_id, next_st, data)
                vk.send_message(peer_id, prompt)
            return True
    if state == states.TICKET_TEMP:
        symptoms["temperature"] = text.strip()
        data = {**data, "symptoms": symptoms}
        _prompt_attach(peer_id, vk_user_id, data)
        return True
    return False


def confirm_ticket(peer_id: int, vk_user_id: int, answer: str):
    if answer != "yes":
        session.clear_state(vk_user_id)
        vk.send_message(peer_id, "Заявка отменена.", back_menu_keyboard())
        return
    data = session.get_payload(vk_user_id)
    user = user_service.get_or_create_user(vk_user_id)
    pet = pet_service.get_pet(user, data["pet_id"])
    ticket = ticket_service.create_ticket(
        user,
        pet,
        data.get("symptoms", {}),
        ticket_type=data.get("ticket_type", "consultation"),
        worsening=data.get("worsening", False),
    )
    ticket_service.save_pending_attachments(ticket, data.get("pending_attachments", []))
    if not subscription_service.get_active_subscription(user):
        from services import payment_service
        one_time = payment_service.get_available_one_time_payment(user)
        if one_time:
            payment_service.consume_one_time(one_time, ticket.id)
    session.clear_state(vk_user_id)
    notified = doctor_notify.notify_new_ticket(ticket)
    log_action(user.vk_id, "ticket_created", "ticket", ticket.id, str(ticket.number))
    msg = f"Заявка #{ticket.number} создана. {urgency_service.urgency_label(ticket.urgency)}"
    if ticket.ticket_type == "emergency":
        msg = f"🚨 Экстренная заявка #{ticket.number} отправлена врачам."
    elif notified:
        msg += "\nВрачи получили уведомление."
    vk.send_message(peer_id, msg, back_menu_keyboard())


def show_tickets(peer_id: int, user):
    tickets = ticket_service.list_user_tickets(user)
    if not tickets:
        vk.send_message(peer_id, "Заявок пока нет.", back_menu_keyboard())
        return

    open_tickets = ticket_service.list_open_user_tickets(user, limit=10)
    lines = ["Ваши заявки:"]
    for t in tickets[:10]:
        lines.append(
            f"#{t.number} {urgency_service.urgency_label(t.urgency)} — {ticket_service.status_label(t.status)}"
        )
    if open_tickets:
        lines.append("")
        lines.append("Нажмите кнопку для подробностей по активной заявке:")
        kb = keyboards.ticket_list_keyboard(open_tickets)
    else:
        lines.append("")
        lines.append("Активных заявок нет.")
        kb = back_menu_keyboard()
    vk.send_message(peer_id, "\n".join(lines), kb)


def show_ticket_detail(peer_id: int, user, number: int):
    ticket = ticket_service.get_user_ticket(user, number)
    if not ticket:
        vk.send_message(peer_id, "Заявка не найдена.", back_menu_keyboard())
        return
    vk.send_message(peer_id, doctor_notify.format_ticket_card(ticket, for_user=True), back_menu_keyboard())
