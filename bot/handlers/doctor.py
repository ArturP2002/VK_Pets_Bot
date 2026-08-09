from models import Payment, Pet, Subscription, Ticket, User
from services import doctor_notify, rbac, staff_messages, ticket_service, vk_links
from services.audit import log_action
from bot.keyboards import back_menu_keyboard, doctor_link_keyboard, doctor_ticket_keyboard, patient_link_keyboard
from integrations import vk


def _cmd_ticket_number(parts: list[str]) -> int | None:
    if len(parts) < 2:
        return None
    return ticket_service.parse_ticket_number(parts[1])


def _notify_client_assigned(ticket: Ticket, doctor_vk_id: int):
    doctor = doctor_notify.get_doctor_for_assign(doctor_vk_id)
    doctor_name = doctor.full_name if doctor else "Дежурный специалист"
    phone = doctor.phone if doctor and doctor.phone else "—"
    vk_url = vk_links.default_doctor_profile_url()
    user = ticket.user
    if not user:
        return
    sub = None
    from services import subscription_service

    sub = subscription_service.get_active_subscription(user)
    if sub and sub.plan == "premium" and not sub.is_trial:
        user.assigned_doctor_vk_id = doctor_vk_id
        user.save()
    kb = doctor_link_keyboard(vk_url)
    msg = (
        f"По заявке #{ticket.number} назначен врач: {doctor_name}\n"
        f"Телефон: {phone}\n"
        f"Врач свяжется с вами в личных сообщениях ВКонтакте или по телефону."
    )
    if sub and sub.plan == "premium" and not sub.is_trial:
        msg += "\n\n⭐ Премиум: при необходимости врач может связаться по телефону."
    vk.send_message(user.vk_id, msg, kb)


def _notify_doctor_assigned(ticket: Ticket, peer_id: int):
    user = ticket.user
    if not user:
        vk.send_message(peer_id, f"✅ Заявка #{ticket.number} назначена на вас.")
        return

    patient_url = vk_links.user_profile_url(user.vk_id, user.vk_screen_name)
    kb = patient_link_keyboard(patient_url)
    msg = (
        f"✅ Заявка #{ticket.number} назначена на вас.\n\n"
        f"Пациент: {user.full_name or '—'}\n"
        f"Телефон: {user.phone or '—'}\n"
        f"Свяжитесь с пациентом в личных сообщениях ВКонтакте или по телефону."
    )
    vk.send_message(peer_id, msg, kb)


def _notify_client_completed(ticket: Ticket):
    user = ticket.user
    if not user:
        return
    vk.send_message(
        user.vk_id,
        f"Заявка #{ticket.number} завершена.\nСпасибо за обращение в ExoCare!",
        back_menu_keyboard(),
    )


def _do_assign(peer_id: int, vk_user_id: int, num: int) -> str:
    if not rbac.can_manage_tickets(vk_user_id, peer_id):
        vk.send_message(peer_id, staff_messages.no_access())
        return staff_messages.no_access()

    ticket = ticket_service.get_ticket_by_number(num)
    if not ticket:
        vk.send_message(peer_id, staff_messages.ticket_not_found())
        return staff_messages.ticket_not_found()

    if not ticket_service.assign_doctor(ticket, vk_user_id):
        msg = staff_messages.assign_fail(num, ticket.status)
        vk.send_message(peer_id, msg)
        return msg

    if ticket.status != "in_progress":
        if not ticket_service.change_status(ticket, "in_progress", vk_user_id):
            msg = staff_messages.assign_partial(num)
            vk.send_message(peer_id, msg)
            return msg

    log_action(vk_user_id, "ticket_assign", "ticket", ticket.id, str(num))
    _notify_client_assigned(ticket, vk_user_id)
    _notify_doctor_assigned(ticket, peer_id)
    return staff_messages.assign_ok(num)


def handle_assign_callback(peer_id: int, vk_user_id: int, num: int) -> str:
    return _do_assign(peer_id, vk_user_id, num)


def handle_complete_callback(peer_id: int, vk_user_id: int, num: int) -> str:
    if not rbac.can_manage_tickets(vk_user_id, peer_id):
        vk.send_message(peer_id, staff_messages.no_access())
        return staff_messages.no_access()

    ticket = ticket_service.get_ticket_by_number(num)
    if not ticket:
        vk.send_message(peer_id, staff_messages.ticket_not_found())
        return staff_messages.ticket_not_found()

    if not ticket_service.change_status(ticket, "completed", vk_user_id):
        msg = staff_messages.complete_fail(num, ticket.status)
        vk.send_message(peer_id, msg)
        return msg

    log_action(vk_user_id, "ticket_complete", "ticket", ticket.id, str(num))
    _notify_client_completed(ticket)
    msg = staff_messages.complete_ok(num)
    vk.send_message(peer_id, msg)
    return msg


def handle_doctor_command(peer_id: int, vk_user_id: int, text: str) -> bool:
    if not text.startswith("/"):
        return False
    is_admin_cmd = text.startswith(("/payments", "/subs", "/users", "/search", "/tickets", "/patient"))
    if not rbac.can_manage_tickets(vk_user_id, peer_id) and not (
        is_admin_cmd and rbac.is_admin(vk_user_id)
    ):
        return rbac.is_admin(vk_user_id) and text.startswith("/")

    parts = text.strip().split(maxsplit=2)
    cmd = parts[0].lower()

    if cmd == "/ticket" and len(parts) >= 2:
        num = _cmd_ticket_number(parts)
        if num is None:
            vk.send_message(peer_id, staff_messages.invalid_ticket_number())
            return True
        ticket = ticket_service.get_ticket_by_number(num)
        if ticket:
            vk.send_message(peer_id, doctor_notify.format_ticket_card(ticket), doctor_ticket_keyboard(num))
        else:
            vk.send_message(peer_id, staff_messages.ticket_not_found())
        return True

    if cmd == "/assign" and len(parts) >= 2:
        num = _cmd_ticket_number(parts)
        if num is None:
            vk.send_message(peer_id, staff_messages.invalid_ticket_number())
            return True
        _do_assign(peer_id, vk_user_id, num)
        return True

    if cmd == "/status" and len(parts) >= 3:
        num = _cmd_ticket_number(parts)
        if num is None:
            vk.send_message(peer_id, staff_messages.invalid_ticket_number())
            return True
        new_status = parts[2]
        ticket = ticket_service.get_ticket_by_number(num)
        if not ticket:
            vk.send_message(peer_id, staff_messages.ticket_not_found())
            return True
        if ticket_service.change_status(ticket, new_status, vk_user_id):
            log_action(vk_user_id, "ticket_status", "ticket", ticket.id, new_status)
            vk.send_message(peer_id, staff_messages.status_change_ok(num, new_status))
        else:
            vk.send_message(peer_id, staff_messages.status_change_fail(num, ticket.status))
        return True

    if cmd == "/note" and len(parts) >= 2:
        num = _cmd_ticket_number(parts)
        if num is None:
            vk.send_message(peer_id, staff_messages.invalid_ticket_number())
            return True
        prefix = f"/note {num} "
        note_text = text[len(prefix) :].strip() if text.startswith(prefix) else ""
        ticket = ticket_service.get_ticket_by_number(num)
        if not ticket:
            vk.send_message(peer_id, staff_messages.note_ticket_not_found())
            return True
        if not note_text:
            vk.send_message(peer_id, staff_messages.note_missing_text(num))
            return True
        ticket_service.add_note(ticket, vk_user_id, note_text)
        log_action(vk_user_id, "ticket_note", "ticket", ticket.id, str(num))
        vk.send_message(
            ticket.user.vk_id,
            staff_messages.client_note(ticket.number, note_text),
            back_menu_keyboard(),
        )
        vk.send_message(peer_id, staff_messages.note_saved(num))
        return True

    if cmd == "/complete" and len(parts) >= 2:
        num = _cmd_ticket_number(parts)
        if num is None:
            vk.send_message(peer_id, staff_messages.invalid_ticket_number())
            return True
        handle_complete_callback(peer_id, vk_user_id, num)
        return True

    if cmd == "/tickets" and rbac.is_admin(vk_user_id):
        status_filter = parts[1] if len(parts) > 1 else None
        q = Ticket.select().order_by(Ticket.created_at.desc()).limit(15)
        if status_filter:
            q = q.where(Ticket.status == status_filter)
        tickets = list(q)
        vk.send_message(peer_id, staff_messages.format_tickets_list(tickets, status_filter))
        log_action(vk_user_id, "admin_tickets", "ticket", details=status_filter)
        return True

    if cmd == "/patient" and rbac.is_admin(vk_user_id):
        q = parts[1] if len(parts) > 1 else ""
        if not q:
            vk.send_message(peer_id, staff_messages.patient_not_found())
            return True
        pet = None
        if q.isdigit():
            pet = Pet.get_or_none(Pet.id == int(q))
        else:
            pet = Pet.select().where(Pet.name.contains(q)).first()
        if not pet:
            vk.send_message(peer_id, staff_messages.patient_not_found())
            return True
        vk.send_message(peer_id, staff_messages.format_patient_card(pet))
        return True

    if cmd == "/payments" and rbac.is_admin(vk_user_id):
        payments = list(Payment.select().order_by(Payment.created_at.desc()).limit(10))
        vk.send_message(peer_id, staff_messages.format_payments_list(payments))
        log_action(vk_user_id, "admin_payments")
        return True

    if cmd == "/subs" and rbac.is_admin(vk_user_id):
        subs = list(Subscription.select().order_by(Subscription.ends_at.desc()).limit(10))
        vk.send_message(peer_id, staff_messages.format_subscriptions_list(subs))
        log_action(vk_user_id, "admin_subs")
        return True

    if cmd == "/users" and rbac.is_admin(vk_user_id):
        users = list(User.select().limit(20))
        vk.send_message(peer_id, staff_messages.format_users_list(users))
        return True

    if cmd.startswith("/search") and rbac.is_admin(vk_user_id):
        q = text.split(maxsplit=2)
        query = q[2] if len(q) > 2 and q[1] == "ticket" else text[7:].strip().lower()
        if not query:
            vk.send_message(peer_id, staff_messages.search_empty_query())
            return True
        found = []
        if len(q) > 2 and q[1] == "ticket":
            if query.isdigit():
                t = Ticket.get_or_none(Ticket.number == int(query))
                if t:
                    found.append(staff_messages.format_search_ticket(t))
            else:
                for t in Ticket.select().where(Ticket.status == query).limit(10):
                    found.append(staff_messages.format_search_ticket(t))
        else:
            for u in User.select():
                if query in (u.full_name or "").lower() or query in (u.phone or ""):
                    found.append(staff_messages.format_search_user(u))
            for p in Pet.select():
                if query in (p.name or "").lower():
                    found.append(staff_messages.format_search_pet(p))
        vk.send_message(peer_id, staff_messages.format_search_results(found))
        return True
    return False
