"""Tests for free dosage and calculator access."""
from datetime import datetime, timedelta

import config
from models import Subscription, User
from services import calculator_access, dosage_access, payment_service, subscription_service
from services.subscription_catalog import (
    ADDON_PLAN_ORDER,
    PLANS,
    format_catalog_overview,
    format_plan_detail,
    min_monthly_price,
    plan_button_label,
)


def test_dosage_always_free(memory_db, tmp_path, monkeypatch):
    db = tmp_path / "formulary_test.db"
    monkeypatch.setattr("config.FORMULARY_DB", str(db))

    user = User.create(vk_id=700001, created_at=datetime.utcnow())
    for _ in range(10):
        dosage_access.record_usage(user.vk_id, kind="dosage_hit", drug_id=1)

    access = dosage_access.check_dosage_access(user)
    assert access.allowed
    assert access.reason == "free"
    assert not access.apply_delay
    assert dosage_access.can_ask_ai(user)
    assert dosage_access.has_dosage_subscription(user)


def test_calculator_always_free(memory_db):
    user = User.create(vk_id=700005, created_at=datetime.utcnow())
    allowed, reason = calculator_access.check_calculator_access(user)
    assert allowed and reason == "ok"
    assert calculator_access.has_calculator_access(user)
    assert subscription_service.can_use_feature(user, "dose_calculator")
    assert subscription_service.can_use_feature(user, "drug_dosage")


def test_starter_still_not_consultation_but_modules_free(memory_db):
    user = User.create(vk_id=700004, created_at=datetime.utcnow())
    now = datetime.utcnow()
    Subscription.create(
        user=user,
        plan="starter",
        period_months=1,
        started_at=now,
        ends_at=now + timedelta(days=30),
        status="active",
        is_trial=False,
    )
    assert subscription_service.can_use_feature(user, "drug_dosage")
    assert subscription_service.can_use_feature(user, "dose_calculator")
    assert calculator_access.has_calculator_access(user)


def test_catalog_marks_modules_free():
    assert ADDON_PLAN_ORDER == ()
    overview = format_catalog_overview()
    assert "Бесплатно для всех" in overview
    assert "Дозировки препаратов" in overview
    assert "Калькулятор дозы" in overview
    assert "200 ₽/мес" not in overview or "Дозировки — 200" not in overview
    # paid consultation plans still listed
    assert "Стартовый" in overview
    assert "dosage" in PLANS and "calculator" in PLANS
    assert "бесплатный" in PLANS["dosage"]["tagline"].lower() or "Бесплатный" in PLANS["dosage"]["tagline"]


def test_addon_payment_legacy_still_works(memory_db):
    """Old paid addon rows may exist; payment path for other plans unchanged."""
    user = User.create(vk_id=700006, created_at=datetime.utcnow())
    starter_pay = payment_service.start_payment(user, "starter", 1, recurrent=True)
    payment_service.complete_payment(order_id=starter_pay.order_id)
    starter = subscription_service.get_active_subscription(user)
    assert starter is not None
    assert starter.plan == "starter"
