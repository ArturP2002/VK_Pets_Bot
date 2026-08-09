"""T-Bank acquiring — Init, Charge, GetState, webhooks."""
import hashlib
import json
import logging
from typing import Any

import requests

import config
from integrations.payment.base import (
    ChargeResult,
    PaymentCreateResult,
    PaymentNotification,
    PaymentProvider,
)
from services.subscription_catalog import plan_name

logger = logging.getLogger(__name__)

TBANK_INIT_URL = "https://securepay.tinkoff.ru/v2/Init"
TBANK_CHARGE_URL = "https://securepay.tinkoff.ru/v2/Charge"
TBANK_GET_STATE_URL = "https://securepay.tinkoff.ru/v2/GetState"
TBANK_CANCEL_URL = "https://securepay.tinkoff.ru/v2/Cancel"

PAID_STATUSES = ("CONFIRMED", "AUTHORIZED")
FAILED_STATUSES = ("REJECTED", "CANCELED", "DEADLINE_EXPIRED")
REFUND_STATUSES = ("REFUNDED", "PARTIAL_REFUNDED", "REVERSED")


class TBankPaymentProvider(PaymentProvider):
    name = "tbank"

    def __init__(self):
        self.terminal_key = config.TBANK_TERMINAL_KEY
        self.password = config.TBANK_PASSWORD

    def _notification_url(self) -> str:
        if config.TBANK_NOTIFICATION_URL:
            return config.TBANK_NOTIFICATION_URL
        return f"{config.WEBHOOK_BASE_URL.rstrip('/')}/webhook/tbank"

    @staticmethod
    def _token_value(value: Any) -> str:
        # JSON bool → Python True/False; банк считает подпись по "true"/"false".
        if isinstance(value, bool):
            return "true" if value else "false"
        return str(value)

    def _token(self, params: dict[str, Any]) -> str:
        data = {k: v for k, v in params.items() if k != "Token" and not isinstance(v, (dict, list))}
        data["Password"] = self.password
        concat = "".join(self._token_value(data[k]) for k in sorted(data))
        return hashlib.sha256(concat.encode()).hexdigest()

    def _post(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        payload = dict(payload)
        payload["Token"] = self._token(payload)
        try:
            resp = requests.post(url, json=payload, timeout=30)
            return resp.json()
        except requests.RequestException as e:
            logger.exception("T-Bank request failed %s: %s", url, e)
            return {"Success": False, "Message": str(e)}

    def _build_receipt(self, amount: int, description: str) -> dict[str, Any]:
        return {
            "Email": "",
            "Taxation": config.TBANK_TAXATION,
            "Items": [
                {
                    "Name": description[:128],
                    "Price": amount * 100,
                    "Quantity": 1,
                    "Amount": amount * 100,
                    "Tax": "none",
                    "PaymentMethod": "full_payment",
                    "PaymentObject": "service",
                }
            ],
        }

    def create_payment(
        self,
        order_id: str,
        amount: int,
        description: str,
        user_id: int,
        *,
        recurrent: bool = False,
        customer_key: str | None = None,
        email: str | None = None,
        phone: str | None = None,
    ) -> PaymentCreateResult:
        if not self.terminal_key or not self.password:
            logger.warning("T-Bank credentials missing; returning stub URL")
            return PaymentCreateResult(
                payment_url=f"{config.WEBHOOK_BASE_URL}/pay/stub/{order_id}",
                external_id=f"tbank_stub_{order_id}",
            )
        payload: dict[str, Any] = {
            "TerminalKey": self.terminal_key,
            "Amount": amount * 100,
            "OrderId": order_id,
            "Description": description[:250],
            "SuccessURL": f"{config.WEBHOOK_BASE_URL.rstrip('/')}/pay/success",
            "FailURL": f"{config.WEBHOOK_BASE_URL.rstrip('/')}/pay/fail",
            "NotificationURL": self._notification_url(),
        }
        data_block: dict[str, str] = {}
        if email:
            data_block["Email"] = email
            payload["Receipt"] = self._build_receipt(amount, description)
            payload["Receipt"]["Email"] = email
        if phone:
            data_block["Phone"] = phone
        if data_block:
            payload["DATA"] = data_block
        # T-Bank: Recurrent=Y в Init не даёт пройти тест-кейсы на DEMO-терминале.
        # Включать только при TBANK_ENABLE_RECURRENT=true (боевой терминал).
        if recurrent and config.TBANK_ENABLE_RECURRENT:
            payload["Recurrent"] = "Y"
            payload["CustomerKey"] = customer_key or f"vk_{user_id}"
            payload["OperationInitiatorType"] = config.TBANK_OPERATION_INITIATOR_TYPE
        elif config.TBANK_SEND_RECEIPT:
            payload["Receipt"] = self._build_receipt(amount, description)
        elif recurrent and not config.TBANK_ENABLE_RECURRENT:
            logger.info(
                "Skipping Recurrent=Y for order %s (TBANK_ENABLE_RECURRENT=false / DEMO terminal)",
                order_id,
            )
        data = self._post(TBANK_INIT_URL, payload)
        if data.get("Success"):
            return PaymentCreateResult(
                payment_url=data.get("PaymentURL"),
                external_id=str(data.get("PaymentId", order_id)),
                raw=data,
            )
        logger.error("T-Bank Init failed: %s", data)
        return PaymentCreateResult(payment_url=None, external_id=f"tbank_fail_{order_id}", raw=data)

    def charge_recurrent(self, payment_id: str, rebill_id: str) -> ChargeResult:
        if not self.terminal_key or not self.password:
            return ChargeResult(success=True, payment_id=payment_id, status="CONFIRMED")
        payload = {
            "TerminalKey": self.terminal_key,
            "PaymentId": payment_id,
            "RebillId": rebill_id,
        }
        data = self._post(TBANK_CHARGE_URL, payload)
        status = data.get("Status")
        return ChargeResult(
            success=bool(data.get("Success")),
            payment_id=str(data.get("PaymentId", payment_id)),
            status=status,
            raw=data,
        )

    def get_state(self, payment_id: str) -> dict[str, Any]:
        if not self.terminal_key or not self.password:
            return {"Success": True, "Status": "CONFIRMED", "PaymentId": payment_id}
        payload = {
            "TerminalKey": self.terminal_key,
            "PaymentId": payment_id,
        }
        return self._post(TBANK_GET_STATE_URL, payload)

    def cancel_payment(self, payment_id: str, amount: int | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "TerminalKey": self.terminal_key,
            "PaymentId": payment_id,
        }
        if amount is not None:
            payload["Amount"] = amount * 100
        return self._post(TBANK_CANCEL_URL, payload)

    def verify_webhook(self, headers: dict, body: bytes) -> bool:
        if not self.password:
            return False
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            return False
        token = data.pop("Token", None)
        expected = self._token(data)
        return token == expected

    def parse_webhook(self, body: dict) -> PaymentNotification:
        status_raw = body.get("Status", "")
        if status_raw in PAID_STATUSES:
            status = "paid"
        elif status_raw in REFUND_STATUSES:
            status = "refunded"
        else:
            status = "failed"
        rebill_id = body.get("RebillId")
        if rebill_id is not None:
            rebill_id = str(rebill_id)
        return PaymentNotification(
            external_id=str(body.get("PaymentId", "")),
            order_id=body.get("OrderId"),
            status=status,
            rebill_id=rebill_id,
            card_mask=body.get("Pan"),
            raw=body,
        )


def tariff_description(tariff: str, period_months: int) -> str:
    if tariff == "one_time":
        return "ExoCare разовая консультация"
    return f"ExoCare {plan_name(tariff)} {period_months} мес."
