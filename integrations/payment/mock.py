import uuid

from integrations.payment.base import (
    ChargeResult,
    PaymentCreateResult,
    PaymentNotification,
    PaymentProvider,
)


class MockPaymentProvider(PaymentProvider):
    name = "mock"

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
        return PaymentCreateResult(
            payment_url=None,
            external_id=f"mock_{order_id}_{uuid.uuid4().hex[:8]}",
            raw={"recurrent": recurrent, "customer_key": customer_key or f"vk_{user_id}"},
        )

    def verify_webhook(self, headers: dict, body: bytes) -> bool:
        return True

    def parse_webhook(self, body: dict) -> PaymentNotification:
        return PaymentNotification(
            external_id=body.get("external_id", ""),
            order_id=body.get("order_id"),
            status=body.get("status", "paid"),
            rebill_id=body.get("rebill_id"),
            card_mask=body.get("card_mask", "430000******0777"),
            raw=body,
        )

    def charge_recurrent(self, payment_id: str, rebill_id: str) -> ChargeResult:
        return ChargeResult(success=True, payment_id=payment_id, status="CONFIRMED")

    def get_state(self, payment_id: str) -> dict:
        return {"Success": True, "Status": "CONFIRMED", "PaymentId": payment_id}
