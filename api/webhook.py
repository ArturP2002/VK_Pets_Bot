import json
import logging

from flask import Flask, jsonify, request

import config
from db import init_db
from integrations.payment.factory import get_payment_provider
from services import chat_service, notification_service, payment_service

logger = logging.getLogger(__name__)

app = Flask(__name__)

_PAY_RESULT_HTML = """
<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ExoCare</title>
<style>
  body {{ font-family: system-ui, sans-serif; max-width: 28rem; margin: 2rem auto; padding: 0 1rem; line-height: 1.5; }}
  a.btn {{ display: inline-block; margin-top: 1rem; padding: 0.75rem 1.25rem; background: #0077ff; color: #fff; text-decoration: none; border-radius: 8px; }}
</style>
</head>
<body>
<p>{message}</p>
<p><a class="btn" href="{bot_url}">Открыть бота во ВКонтакте</a></p>
<p>Если кнопка не открылась — вернитесь в сообщество VK и откройте диалог с ботом.</p>
</body>
</html>
"""


def _pay_result_page(message: str):
    html = _PAY_RESULT_HTML.format(message=message, bot_url=config.VK_BOT_RETURN_URL)
    return html, 200, {"Content-Type": "text/html; charset=utf-8"}


def _notify_payment_success(payment):
    if not payment or payment.status != "paid":
        return
    user = payment.user
    if payment.tariff == "one_time":
        from bot.handlers.subscription import begin_one_time_consultation_flow

        begin_one_time_consultation_flow(user.vk_id, user.vk_id, after_payment=True)
        return
    else:
        from services import subscription_service

        if payment.tariff in subscription_service.ADDON_PLANS:
            sub = subscription_service.get_active_plan_subscription(
                user, payment.tariff
            )
        else:
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
        if not payment.is_recurrent_charge:
            notification_service.queue_charge_notification(user, payment, is_first=True)
        else:
            notification_service.queue_charge_notification(user, payment, is_first=False)
    notification_service.process_pending()


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


@app.route("/pay/success", methods=["GET"])
def pay_success():
    return _pay_result_page("Оплата прошла успешно.")


@app.route("/pay/fail", methods=["GET"])
def pay_fail():
    return _pay_result_page("Оплата не выполнена.")


@app.route("/pay/stub/<order_id>", methods=["GET"])
def pay_stub(order_id: str):
    return _pay_result_page(f"Тестовая оплата (заказ {order_id}).")


@app.route("/webhook/tbank", methods=["POST"])
def webhook_tbank():
    provider = get_payment_provider()
    body = request.get_data()
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return jsonify({"error": "invalid json"}), 400
    verify_data = dict(data)
    if not provider.verify_webhook(dict(request.headers), body):
        logger.warning(
            "T-Bank webhook signature invalid for OrderId=%s Status=%s PaymentId=%s",
            data.get("OrderId"),
            data.get("Status"),
            data.get("PaymentId"),
        )
        return jsonify({"error": "invalid signature"}), 403
    notification = provider.parse_webhook(verify_data)
    logger.info(
        "T-Bank webhook OK OrderId=%s status=%s PaymentId=%s",
        notification.order_id,
        notification.status,
        notification.external_id,
    )
    if notification.status == "paid":
        payment, newly_completed = payment_service.complete_payment(
            order_id=notification.order_id,
            external_id=notification.external_id,
            rebill_id=notification.rebill_id,
            card_mask=notification.card_mask,
        )
        if newly_completed:
            _notify_payment_success(payment)
        else:
            logger.info(
                "Skip duplicate payment notify OrderId=%s (already completed)",
                notification.order_id,
            )
    elif notification.status == "refunded":
        payment = payment_service.get_payment_by_order(notification.order_id or "")
        if not payment and notification.external_id:
            from models import Payment

            payment = Payment.get_or_none(Payment.external_id == notification.external_id)
        if payment:
            payment_service.refund_payment(payment)
    elif notification.status == "failed":
        payment = payment_service.get_payment_by_order(notification.order_id or "")
        if payment and payment.status == "pending":
            payment_service.fail_payment(payment)
    # T-Bank требует HTTP 200 и тело "OK" (plain text, без JSON/тегов).
    return "OK", 200, {"Content-Type": "text/plain; charset=utf-8"}


@app.route("/webhook/mock", methods=["POST"])
def webhook_mock():
    data = request.get_json(force=True, silent=True) or {}
    order_id = data.get("order_id")
    status = data.get("status", "paid")
    if status == "paid":
        payment, newly_completed = payment_service.complete_payment(
            order_id=order_id,
            rebill_id=data.get("rebill_id"),
            card_mask=data.get("card_mask"),
        )
        if newly_completed:
            _notify_payment_success(payment)
    else:
        payment = payment_service.get_payment_by_order(order_id)
        if payment:
            payment_service.fail_payment(payment)
    return jsonify({"ok": True})


def run_webhook_server():
    init_db()
    app.run(host=config.WEBHOOK_HOST, port=config.WEBHOOK_PORT)
