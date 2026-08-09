from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class PaymentCreateResult:
    payment_url: str | None
    external_id: str
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class PaymentNotification:
    external_id: str
    order_id: str | None
    status: str  # paid, failed, refunded
    rebill_id: str | None = None
    card_mask: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class ChargeResult:
    success: bool
    payment_id: str | None = None
    status: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


class PaymentProvider(ABC):
    name: str = "base"

    @abstractmethod
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
        pass

    @abstractmethod
    def verify_webhook(self, headers: dict, body: bytes) -> bool:
        pass

    @abstractmethod
    def parse_webhook(self, body: dict) -> PaymentNotification:
        pass

    def charge_recurrent(self, payment_id: str, rebill_id: str) -> ChargeResult:
        raise NotImplementedError

    def get_state(self, payment_id: str) -> dict[str, Any]:
        raise NotImplementedError

    def cancel_payment(self, payment_id: str, amount: int | None = None) -> dict[str, Any]:
        raise NotImplementedError
