from datetime import datetime

from models import User
from integrations.payment.mock import MockPaymentProvider
from integrations.payment.tbank import TBankPaymentProvider
from services import payment_service


def test_mock_provider():
    p = MockPaymentProvider()
    r = p.create_payment("ord1", 999, "test", 1)
    assert r.external_id.startswith("mock_")


def test_tbank_parse():
    p = TBankPaymentProvider()
    n = p.parse_webhook(
        {
            "PaymentId": "1",
            "OrderId": "ord1",
            "Status": "CONFIRMED",
            "RebillId": "12345",
            "Pan": "430000******0777",
        }
    )
    assert n.status == "paid"
    assert n.rebill_id == "12345"
    assert n.card_mask == "430000******0777"


def test_tbank_token():
    p = TBankPaymentProvider()
    p.terminal_key = "TestTerminal"
    p.password = "test_password"
    token = p._token({"TerminalKey": "TestTerminal", "Amount": 100000, "OrderId": "1"})
    assert len(token) == 64


def test_tbank_token_bool_success_lowercase():
    """Webhook JSON parses Success as bool; token must use lowercase true/false."""
    p = TBankPaymentProvider()
    p.password = "11111111111"
    token_bool = p._token(
        {
            "TerminalKey": "1234567890DEMO",
            "OrderId": "000000",
            "Success": True,
            "Status": "AUTHORIZED",
            "PaymentId": "0000000",
            "ErrorCode": "0",
            "Amount": "1111",
            "CardId": "000000",
            "Pan": "200000******0000",
            "ExpDate": "1111",
            "RebillId": "000000",
        }
    )
    token_str = p._token(
        {
            "TerminalKey": "1234567890DEMO",
            "OrderId": "000000",
            "Success": "true",
            "Status": "AUTHORIZED",
            "PaymentId": "0000000",
            "ErrorCode": "0",
            "Amount": "1111",
            "CardId": "000000",
            "Pan": "200000******0000",
            "ExpDate": "1111",
            "RebillId": "000000",
        }
    )
    assert token_bool == token_str
    assert token_bool == "1c0964277d0213349243065a0d5b838b8e90d2d25f740d0f2767836e710e80c8"


def test_tbank_init_skips_recurrent_on_demo(monkeypatch):
    """T-Bank test-cases fail if Init includes Recurrent=Y on DEMO terminal."""
    import config
    from integrations.payment import tbank as tbank_mod

    monkeypatch.setattr(config, "TBANK_TERMINAL_KEY", "123DEMO")
    monkeypatch.setattr(config, "TBANK_PASSWORD", "secret")
    monkeypatch.setattr(config, "TBANK_ENABLE_RECURRENT", False)
    monkeypatch.setattr(config, "TBANK_SEND_RECEIPT", False)
    monkeypatch.setattr(config, "WEBHOOK_BASE_URL", "https://example.test")

    captured = {}

    def fake_post(url, payload):
        captured["payload"] = payload
        return {
            "Success": True,
            "PaymentURL": "https://pay.example/x",
            "PaymentId": "99",
        }

    p = tbank_mod.TBankPaymentProvider()
    monkeypatch.setattr(p, "_post", fake_post)
    result = p.create_payment("ord1", 500, "test", 1, recurrent=True)
    assert result.payment_url == "https://pay.example/x"
    assert "Recurrent" not in captured["payload"]
    assert "CustomerKey" not in captured["payload"]
    assert "OperationInitiatorType" not in captured["payload"]


def test_tbank_init_sends_recurrent_when_enabled(monkeypatch):
    import config
    from integrations.payment import tbank as tbank_mod

    monkeypatch.setattr(config, "TBANK_TERMINAL_KEY", "ProdTerminal")
    monkeypatch.setattr(config, "TBANK_PASSWORD", "secret")
    monkeypatch.setattr(config, "TBANK_ENABLE_RECURRENT", True)
    monkeypatch.setattr(config, "TBANK_SEND_RECEIPT", False)
    monkeypatch.setattr(config, "TBANK_OPERATION_INITIATOR_TYPE", "2")
    monkeypatch.setattr(config, "WEBHOOK_BASE_URL", "https://example.test")

    captured = {}

    def fake_post(url, payload):
        captured["payload"] = payload
        return {"Success": True, "PaymentURL": "https://pay.example/x", "PaymentId": "99"}

    p = tbank_mod.TBankPaymentProvider()
    monkeypatch.setattr(p, "_post", fake_post)
    p.create_payment("ord1", 500, "test", 42, recurrent=True, customer_key="vk_42")
    assert captured["payload"]["Recurrent"] == "Y"
    assert captured["payload"]["CustomerKey"] == "vk_42"
    assert captured["payload"]["OperationInitiatorType"] == "2"


def test_start_payment_recurrent(memory_db):
    from datetime import datetime

    from models import User

    user = User.create(vk_id=999004, created_at=datetime.utcnow())
    pay = payment_service.start_payment(user, "starter", 1, recurrent=True)
    assert pay.status == "pending"
    meta = payment_service.payment_meta(pay)
    assert meta.get("recurrent") is True


def test_complete_payment(memory_db):
    user = User.create(vk_id=999003, created_at=datetime.utcnow())
    pay = payment_service.start_payment(user, "starter", 1)
    done = payment_service.complete_payment(order_id=pay.order_id)
    assert done.status == "paid"
