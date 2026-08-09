import config
from integrations.payment.base import PaymentProvider
from integrations.payment.mock import MockPaymentProvider
from integrations.payment.tbank import TBankPaymentProvider


def get_payment_provider() -> PaymentProvider:
    if config.PAYMENT_PROVIDER == "tbank":
        return TBankPaymentProvider()
    return MockPaymentProvider()
