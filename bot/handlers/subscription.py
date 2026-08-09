import config
from bot import keyboards, states
from bot.keyboards import back_menu_keyboard
from integrations import vk
from services import chat_service, legal_service, notification_service, payment_service
from services import bot_docs, recurrent_billing_service, session, subscription_service, user_service
from services.audit import log_action
from services.subscription_catalog import (
    format_activation_success,
    format_active_subscription,
    format_cancel_auto_renew_confirm,
    format_cancel_auto_renew_success,
    format_checkout_consent,
    format_no_subscription,
    format_payment_history,
    format_payment_summary,
    format_plan_detail,
    format_subscription_history,
)


def show_subscription(peer_id: int, user):
    sub = subscription_service.get_active_subscription(user)
    parts = []
    if sub:
        parts.append(format_active_subscription(sub))
    else:
        parts.append(format_no_subscription())
    parts.append(format_payment_history(payment_service.list_user_payments(user)))
    parts.append(format_subscription_history(user))
    show_cancel = bool(sub and sub.auto_renew and not sub.is_trial)
    vk.send_message(
        peer_id,
        "\n\n".join(parts),
        keyboards.subscription_manage_keyboard(show_cancel=show_cancel),
    )


def start_trial_flow(peer_id: int, user):
    if user.trial_used:
        vk.send_message(peer_id, "Пробный период уже использован.", back_menu_keyboard())
        return
    sub = subscription_service.start_trial(user)
    if sub:
        chat_service.sync_chats_for_user(user)
        log_action(user.vk_id, "trial_started", "subscription", sub.id)
        notification_service.queue_notification(
            user.vk_id,
            "subscription_activated",
            {
                "plan": "пробный",
                "ends_at": sub.ends_at.strftime("%d.%m.%Y"),
                "amount": "0",
            },
        )
        vk.send_message(
            peer_id,
            (
                f"✅ Пробный период {config.TRIAL_DAYS} дней активирован.\n\n"
                f"Действует до: {sub.ends_at.strftime('%d.%m.%Y')}\n"
                "Доступны возможности тарифа «Стартовый»."
            ),
            back_menu_keyboard(),
        )
    else:
        vk.send_message(peer_id, "Не удалось активировать пробный период.", back_menu_keyboard())


def begin_one_time_consultation_flow(peer_id: int, vk_user_id: int, *, after_payment: bool = False):
    """Оплата разовой консультации или переход к заявке, если оплата уже есть."""
    from bot.handlers import tickets

    user = user_service.get_or_create_user(vk_user_id)
    if payment_service.get_available_one_time_payment(user):
        if after_payment:
            vk.send_message(peer_id, "✅ Разовая консультация оплачена.")
        tickets.start_ticket_flow(peer_id, vk_user_id, "consultation")
        return
    if after_payment:
        vk.send_message(
            peer_id,
            "✅ Оплата прошла, но кредит разовой консультации не найден. Обратитесь в поддержку.",
            back_menu_keyboard(),
        )
        return
    _start_checkout_consent(peer_id, vk_user_id, "one_time", 0)


def handle_sub_plan(peer_id: int, vk_user_id: int, plan: str):
    if plan == "one_time":
        begin_one_time_consultation_flow(peer_id, vk_user_id)
        return
    session.set_state(vk_user_id, states.SUB_CHOOSE_PERIOD, {"plan": plan})
    vk.send_message(
        peer_id,
        format_plan_detail(plan),
        keyboards.subscription_period_keyboard(plan),
    )


def handle_sub_period(peer_id: int, vk_user_id: int, plan: str, months: int):
    _start_checkout_consent(peer_id, vk_user_id, plan, int(months))


def _start_checkout_consent(peer_id: int, vk_user_id: int, plan: str, months: int):
    if plan == "one_time":
        amount = config.ONE_TIME_CONSULTATION_PRICE
    else:
        amount = config.TARIFF_PRICES.get(plan, {}).get(months, 0)
    doc_keys = legal_service.checkout_consent_keys(plan)
    session.set_state(
        vk_user_id,
        states.SUB_CHECKOUT_CONSENT,
        {
            "plan": plan,
            "months": months,
            "amount": amount,
            "doc_keys": doc_keys,
        },
    )
    attachments = bot_docs.upload_legal_attachments(peer_id)
    vk.send_message_attachments(
        peer_id,
        format_checkout_consent(plan, months, amount),
        attachments,
        keyboard=keyboards.checkout_consent_keyboard(config.LEGAL_CHECKOUT_PACKAGE_URL),
        keyboard_message="Подтвердите согласие и оплату:",
    )


def handle_sub_consent_pay(peer_id: int, vk_user_id: int) -> bool:
    payload = session.get_payload(vk_user_id)
    if session.get_state(vk_user_id) != states.SUB_CHECKOUT_CONSENT:
        return False
    doc_keys = payload.get("doc_keys", [])
    if not doc_keys:
        plan = payload.get("plan", "")
        doc_keys = legal_service.checkout_consent_keys(plan)
    plan = payload.get("plan", "")
    months = int(payload.get("months", 0))
    amount = int(payload.get("amount", 0))
    user = user_service.get_or_create_user(vk_user_id)
    payment = payment_service.start_payment(user, plan, months, recurrent=(plan != "one_time"))
    legal_service.save_checkout_consent(user, plan, months, amount, list(doc_keys), payment=payment)
    session.set_state(vk_user_id, states.PAYMENT_PENDING, {"order_id": payment.order_id})
    _send_payment_ui(peer_id, vk_user_id, payment)
    return True


def _send_payment_ui(peer_id: int, vk_user_id: int, payment):
    msg = format_payment_summary(payment)
    kb = None
    if config.PAYMENT_PROVIDER == "mock":
        kb = keyboards.mock_payment_keyboard(payment.order_id)
    else:
        url = payment_service.get_payment_url(payment)
        if url:
            kb = keyboards.payment_link_keyboard(url)
    vk.send_message(peer_id, msg, kb)


def handle_pay_ok(peer_id: int, vk_user_id: int, order_id: str):
    payment, newly_completed = payment_service.complete_payment(order_id=order_id)
    if payment and newly_completed:
        user = payment.user
        sub = None
        if payment.tariff != "one_time":
            if config.PAYMENT_PROVIDER == "mock":
                recurrent_billing_service.create_mock_agreement(payment)
            sub = subscription_service.get_active_subscription(user)
            chat_service.sync_chats_for_user(user)
            notification_service.queue_notification(
                user.vk_id,
                "subscription_activated",
                {
                    "plan": payment.tariff,
                    "ends_at": sub.ends_at.strftime("%d.%m.%Y") if sub else "—",
                    "amount": str(payment.amount),
                },
            )
            notification_service.queue_charge_notification(user, payment, is_first=True)
            vk.send_message(
                peer_id,
                format_activation_success(sub, payment),
                back_menu_keyboard(),
            )
        else:
            begin_one_time_consultation_flow(peer_id, vk_user_id, after_payment=True)
    elif payment and not newly_completed:
        vk.send_message(peer_id, "Эта оплата уже обработана.", back_menu_keyboard())
    session.clear_state(vk_user_id)


def handle_pay_fail(peer_id: int, vk_user_id: int, order_id: str):
    payment = payment_service.get_payment_by_order(order_id)
    if payment:
        payment_service.fail_payment(payment)
    vk.send_message(
        peer_id,
        "Оплата не прошла. Выберите тариф и попробуйте снова.",
        keyboards.subscription_plans_keyboard(),
    )
    session.clear_state(vk_user_id)


def start_cancel_auto_renew(peer_id: int, user):
    agreement = recurrent_billing_service.get_active_agreement(user)
    if not agreement:
        vk.send_message(
            peer_id,
            "У вас нет активного автопродления.",
            back_menu_keyboard(),
        )
        return
    session.set_state(user.vk_id, states.CANCEL_AUTO_RENEW, {"agreement_id": agreement.id})
    vk.send_message(
        peer_id,
        format_cancel_auto_renew_confirm(agreement),
        keyboards.yes_no_keyboard("cancel_auto_renew"),
    )


def handle_cancel_auto_renew(peer_id: int, vk_user_id: int, answer: str):
    if answer != "yes":
        session.clear_state(vk_user_id)
        vk.send_message(peer_id, "Отмена автопродления не выполнена.", back_menu_keyboard())
        return
    user = user_service.get_or_create_user(vk_user_id)
    agreement = recurrent_billing_service.cancel_agreement(user)
    session.clear_state(vk_user_id)
    if not agreement:
        vk.send_message(peer_id, "Активное автопродление не найдено.", back_menu_keyboard())
        return
    sub = subscription_service.get_active_subscription(user)
    ends = sub.ends_at.strftime("%d.%m.%Y") if sub else "—"
    notification_service.queue_notification(
        user.vk_id,
        "auto_renew_cancelled",
        {"ends_at": ends},
    )
    vk.send_message(peer_id, format_cancel_auto_renew_success(ends), back_menu_keyboard())


def handle_sub_cancel_auto_renew(peer_id: int, vk_user_id: int):
    user = user_service.get_or_create_user(vk_user_id)
    start_cancel_auto_renew(peer_id, user)
