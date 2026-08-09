"""Тексты ответов для врачей и администраторов."""

from __future__ import annotations

from models import Payment, Pet, Subscription, Ticket, User
from services import doctor_notify, subscription_catalog, ticket_service, urgency_service, vk_links

PAYMENT_STATUS_LABELS = {
    "pending": "ожидает оплаты",
    "paid": "оплачен",
    "failed": "не прошёл",
}

SUBSCRIPTION_STATUS_LABELS = {
    "active": "активна",
    "expired": "истекла",
    "cancelled": "отменена",
}


def payment_status_label(status: str) -> str:
    return PAYMENT_STATUS_LABELS.get(status, status)


def subscription_status_label(status: str) -> str:
    return SUBSCRIPTION_STATUS_LABELS.get(status, status)


def invalid_ticket_number() -> str:
    return "Укажите номер заявки цифрами.\nПример: /ticket 5 или /ticket #5"


def ticket_not_found() -> str:
    return "Заявка с таким номером не найдена."


def no_access() -> str:
    return "У вас нет прав для управления заявками."


def assign_fail(num: int, status: str) -> str:
    label = ticket_service.status_label(status)
    return (
        f"Не удалось взять заявку #{num}.\n"
        f"Текущий статус: «{label}»."
    )


def assign_partial(num: int) -> str:
    return (
        f"Заявка #{num} назначена на вас, "
        f"но не удалось обновить статус. Проверьте карточку: /ticket {num}"
    )


def assign_ok(num: int) -> str:
    return f"✅ Заявка #{num} назначена на вас. Клиент получил уведомление."


def complete_fail(num: int, status: str) -> str:
    label = ticket_service.status_label(status)
    return (
        f"Не удалось завершить заявку #{num}.\n"
        f"Текущий статус: «{label}»."
    )


def complete_ok(num: int) -> str:
    return f"✅ Заявка #{num} завершена. Клиент получил уведомление."


def status_change_ok(num: int, new_status: str) -> str:
    label = ticket_service.status_label(new_status)
    return f"✅ Заявка #{num}: статус изменён на «{label}»."


def status_change_fail(num: int, current_status: str) -> str:
    label = ticket_service.status_label(current_status)
    return (
        f"Не удалось сменить статус заявки #{num}.\n"
        f"Текущий статус: «{label}». Проверьте допустимый переход "
        f"или используйте /assign и /complete."
    )


def note_saved(num: int) -> str:
    return f"✅ Заключение по заявке #{num} сохранено и отправлено клиенту."


def note_missing_text(num: int) -> str:
    return (
        f"Добавьте текст заключения после номера заявки.\n"
        f"Пример: /note {num} Рекомендую наблюдение в течение суток."
    )


def note_ticket_not_found() -> str:
    return ticket_not_found()


def client_note(ticket_number: int, note_text: str) -> str:
    return (
        f"📋 По вашей заявке #{ticket_number} врач оставил заключение:\n\n"
        f"{note_text[:500]}"
    )


def format_ticket_row(ticket: Ticket) -> str:
    urgency = urgency_service.urgency_label(ticket.urgency)
    status = ticket_service.status_label(ticket.status)
    return f"#{ticket.number} · {urgency} · {status}"


def format_tickets_list(tickets: list[Ticket], status_filter: str | None = None) -> str:
    if not tickets:
        if status_filter:
            label = ticket_service.status_label(status_filter)
            return f"Заявок со статусом «{label}» нет."
        return "Заявок пока нет."

    header = "📋 Последние заявки"
    if status_filter:
        label = ticket_service.status_label(status_filter)
        header += f" · фильтр «{label}»"

    lines = [header, ""]
    lines.extend(format_ticket_row(t) for t in tickets)
    lines.extend(["", "Подробнее: /ticket <номер>"])
    return "\n".join(lines)


def format_patient_card(pet: Pet) -> str:
    user = pet.user
    owner_name = user.full_name if user else "—"
    owner_phone = user.phone if user else "—"
    owner_vk = vk_links.user_profile_url(user.vk_id, user.vk_screen_name) if user else "—"

    lines = [
        f"🐾 Питомец #{pet.id}: {pet.name}",
        f"Вид: {pet.species}",
        "",
        "Владелец:",
        f"· {owner_name}",
        f"· {owner_phone}",
        f"· {owner_vk}",
        "",
        doctor_notify.format_pet_block(pet),
    ]
    return "\n".join(lines)


def patient_not_found() -> str:
    return (
        "Питомец не найден.\n"
        "Укажите кличку или номер: /patient Барсик или /patient 12"
    )


def format_payments_list(payments: list[Payment]) -> str:
    if not payments:
        return "Платежей пока нет."

    lines = ["💳 Последние платежи", ""]
    for p in payments:
        tariff = subscription_catalog.plan_name(p.tariff)
        status = payment_status_label(p.status)
        lines.append(f"· {p.order_id}")
        lines.append(f"  {p.amount} ₽ · {tariff} · {status}")
    return "\n".join(lines)


def format_subscriptions_list(subs: list[Subscription]) -> str:
    if not subs:
        return "Подписок пока нет."

    lines = ["📅 Последние подписки", ""]
    for s in subs:
        plan = subscription_catalog.plan_name(s.plan)
        status = subscription_status_label(s.status)
        ends = s.ends_at.strftime("%d.%m.%Y") if s.ends_at else "—"
        trial = " · пробный период" if s.is_trial else ""
        lines.append(f"· Пользователь #{s.user_id}")
        lines.append(f"  {plan}{trial} · до {ends} · {status}")
    return "\n".join(lines)


def format_users_list(users: list[User]) -> str:
    if not users:
        return "Пользователей пока нет."

    lines = ["👥 Пользователи", ""]
    for u in users:
        name = u.full_name or "без имени"
        lines.append(f"· {name}")
        lines.append(f"  id {u.vk_id}")
    lines.extend(["", "Поиск: /search <имя, телефон или кличка>"])
    return "\n".join(lines)


def format_search_results(found: list[str]) -> str:
    if not found:
        return "Ничего не найдено. Попробуйте другой запрос."
    lines = ["🔍 Результаты поиска", ""]
    lines.extend(f"· {item}" for item in found[:15])
    return "\n".join(lines)


def search_empty_query() -> str:
    return (
        "Укажите, что искать.\n"
        "Примеры:\n"
        "· /search Иван\n"
        "· /search ticket 12"
    )


def format_search_ticket(ticket: Ticket) -> str:
    status = ticket_service.status_label(ticket.status)
    return f"Заявка #{ticket.number} · {status}"


def format_search_user(user: User) -> str:
    name = user.full_name or "без имени"
    return f"{name} · id {user.vk_id}"


def format_search_pet(pet: Pet) -> str:
    return f"Питомец #{pet.id} · {pet.name} ({pet.species})"
