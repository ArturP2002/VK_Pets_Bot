import json
import logging
from datetime import datetime

import config
from models import Notification, Payment, RecurrentAgreement, User
from services import email_service
from services.subscription_catalog import recurrent_charge_label

logger = logging.getLogger(__name__)

_vk_send = None


def set_vk_sender(func):
    global _vk_send
    _vk_send = func


def queue_notification(vk_user_id: int, template: str, payload: dict | None = None):
    Notification.create(
        vk_user_id=vk_user_id,
        template=template,
        payload=json.dumps(payload or {}, ensure_ascii=False),
        status="pending",
        created_at=datetime.utcnow(),
    )


def _cancel_footer() -> str:
    return f"\n\n{config.CANCEL_INSTRUCTIONS_TEXT}\n{config.CONTACTS_TEXT}"


def send_vk(vk_user_id: int, message: str, keyboard: str | None = None) -> bool:
    if _vk_send:
        try:
            _vk_send(vk_user_id, message, keyboard)
            return True
        except Exception as e:
            logger.exception("VK send failed: %s", e)
    return False


def queue_charge_notification(user: User, payment: Payment, is_first: bool = False):
    if not user.email:
        return
    if is_first:
        subject = "ExoCare: подписка оформлена"
        body = (
            f"Подписка активирована.\n"
            f"Сумма: {payment.amount} ₽\n"
            f"Периодичность: {recurrent_charge_label(payment.period_months, payment.amount)}"
            f"{_cancel_footer()}"
        )
    else:
        subject = "ExoCare: успешное автосписание"
        body = (
            f"С вашей карты списано {payment.amount} ₽.\n"
            f"Периодичность: {recurrent_charge_label(payment.period_months, payment.amount)}"
            f"{_cancel_footer()}"
        )
    queue_notification(
        user.vk_id,
        "email_dispatch",
        {"to": user.email, "subject": subject, "body": body},
    )


def queue_charge_reminder_email(user: User, agreement: RecurrentAgreement):
    if not user.email:
        return
    subject = "ExoCare: напоминание о предстоящем списании"
    body = (
        f"Завтра ({agreement.next_charge_at.strftime('%d.%m.%Y')}) "
        f"будет списано {agreement.amount} ₽.\n"
        f"Периодичность: {recurrent_charge_label(agreement.period_months, agreement.amount)}"
        f"{_cancel_footer()}"
    )
    queue_notification(
        user.vk_id,
        "email_dispatch",
        {"to": user.email, "subject": subject, "body": body},
    )


def process_pending(limit: int = 50):
    pending = (
        Notification.select()
        .where(Notification.status == "pending")
        .order_by(Notification.created_at)
        .limit(limit)
    )
    templates = {
        "ticket_created": "Заявка #{number} создана. Срочность: {urgency}.",
        "doctor_assigned": (
            "Врач {doctor_name} назначен на заявку #{number}.\n"
            "Телефон: {phone}\n"
            "Врач свяжется с вами в личных сообщениях VK или по телефону.\n"
            "VK: {vk_url}"
        ),
        "note_added": "По заявке #{number} добавлено заключение:\n{text}",
        "subscription_activated": (
            "Подписка {plan} активирована до {ends_at}.\n"
            "Сумма: {amount} ₽."
            + _cancel_footer()
        ),
        "subscription_reminder": "Подписка заканчивается {when}. Продлите в разделе «Моя подписка».",
        "charge_success": (
            "Успешное автосписание: {amount} ₽.\n"
            "Периодичность: {periodicity}."
            + _cancel_footer()
        ),
        "charge_reminder": (
            "Напоминание: {charge_date} будет списано {amount} ₽.\n"
            "Периодичность: {periodicity}."
            + _cancel_footer()
        ),
        "charge_failed": (
            "Автосписание {amount} ₽ не прошло. Доступ сохранится до {ends_at}.\n"
            f"{config.CONTACTS_TEXT}"
        ),
        "auto_renew_cancelled": (
            "Автопродление отключено. Доступ до {ends_at}.\n"
            f"{config.CONTACTS_TEXT}"
        ),
    }
    for n in pending:
        try:
            payload = json.loads(n.payload or "{}")
            if n.template == "email_dispatch":
                sent = email_service.send_email(
                    payload.get("to", ""),
                    payload.get("subject", "ExoCare"),
                    payload.get("body", ""),
                )
                n.status = "sent" if sent else "failed"
                n.sent_at = datetime.utcnow() if sent else None
                if not sent:
                    n.attempts += 1
                n.save()
                continue
            tpl = templates.get(n.template, n.template)
            safe_payload = {
                "number": "",
                "urgency": "",
                "doctor_name": "",
                "phone": "",
                "vk_url": "",
                "text": "",
                "plan": "",
                "ends_at": "",
                "when": "",
                "amount": "",
                "periodicity": "",
                "charge_date": "",
                **payload,
            }
            msg = tpl.format(**safe_payload) if "{" in tpl else tpl
            if send_vk(n.vk_user_id, msg):
                n.status = "sent"
                n.sent_at = datetime.utcnow()
                if n.template in (
                    "subscription_activated",
                    "charge_success",
                    "charge_reminder",
                    "auto_renew_cancelled",
                ):
                    user = User.get_or_none(User.vk_id == n.vk_user_id)
                    if user and user.email and n.template != "email_dispatch":
                        email_service.send_email(user.email, "ExoCare", msg)
            else:
                n.attempts += 1
                if n.attempts >= 5:
                    n.status = "failed"
            n.save()
        except Exception:
            n.attempts += 1
            n.save()
