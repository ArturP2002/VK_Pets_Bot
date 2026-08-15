"""Tests for dosage free-tier access and calculator gating."""
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


def test_free_dosage_limit(memory_db, tmp_path, monkeypatch):
    db = tmp_path / "formulary_test.db"
    monkeypatch.setattr("config.FORMULARY_DB", str(db))
    monkeypatch.setattr("config.FORMULARY_FREE_LIMIT_PER_24H", 2)

    user = User.create(vk_id=700001, created_at=datetime.utcnow())
    access = dosage_access.check_dosage_access(user)
    assert access.allowed
    assert access.apply_delay
    assert access.remaining_free == 2

    dosage_access.record_usage(user.vk_id, kind="dosage_hit", drug_id=1)
    dosage_access.record_usage(user.vk_id, kind="ask_ai")
    access = dosage_access.check_dosage_access(user)
    assert not access.allowed
    assert access.reason == "limit_exceeded"
    assert not dosage_access.can_ask_ai(user)


def test_dosage_subscription_no_delay(memory_db, tmp_path, monkeypatch):
    db = tmp_path / "formulary_test.db"
    monkeypatch.setattr("config.FORMULARY_DB", str(db))

    user = User.create(vk_id=700002, created_at=datetime.utcnow())
    now = datetime.utcnow()
    Subscription.create(
        user=user,
        plan="dosage",
        period_months=1,
        started_at=now,
        ends_at=now + timedelta(days=30),
        status="active",
        is_trial=False,
    )
    access = dosage_access.check_dosage_access(user)
    assert access.allowed
    assert access.has_subscription
    assert not access.apply_delay
    assert subscription_service.can_use_feature(user, "drug_dosage")
    assert not subscription_service.can_use_feature(user, "dose_calculator")


def test_trial_opens_both_modules(memory_db, tmp_path, monkeypatch):
    db = tmp_path / "formulary_test.db"
    monkeypatch.setattr("config.FORMULARY_DB", str(db))

    user = User.create(vk_id=700003, created_at=datetime.utcnow())
    assert subscription_service.start_trial(user)
    assert dosage_access.has_dosage_subscription(user)
    assert calculator_access.has_calculator_access(user)
    assert subscription_service.can_use_feature(user, "drug_dosage")
    assert subscription_service.can_use_feature(user, "dose_calculator")


def test_starter_does_not_open_modules(memory_db):
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
    assert not subscription_service.can_use_feature(user, "drug_dosage")
    assert not subscription_service.can_use_feature(user, "dose_calculator")
    assert not calculator_access.has_calculator_access(user)


def test_calculator_plan(memory_db):
    user = User.create(vk_id=700005, created_at=datetime.utcnow())
    now = datetime.utcnow()
    Subscription.create(
        user=user,
        plan="calculator",
        period_months=1,
        started_at=now,
        ends_at=now + timedelta(days=30),
        status="active",
        is_trial=False,
    )
    allowed, reason = calculator_access.check_calculator_access(user)
    assert allowed and reason == "ok"
    assert subscription_service.can_use_feature(user, "dose_calculator")
    assert not subscription_service.can_use_feature(user, "drug_dosage")


def test_addon_prices_in_config_and_catalog():
    assert config.TARIFF_PRICES["dosage"][1] == 200
    assert config.TARIFF_PRICES["calculator"][1] == 300
    assert min_monthly_price("dosage") == 200
    assert min_monthly_price("calculator") == 300
    assert "dosage" in PLANS and "calculator" in PLANS
    assert ADDON_PLAN_ORDER == ("dosage", "calculator")
    overview = format_catalog_overview()
    assert "Дозировки" in overview
    assert "200" in overview
    assert "Калькулятор" in overview
    assert "300" in overview
    assert "200" in plan_button_label("dosage")
    detail = format_plan_detail("dosage")
    assert "200" in detail


def test_addon_payment_coexists_with_starter(memory_db):
    user = User.create(vk_id=700006, created_at=datetime.utcnow())
    starter_pay = payment_service.start_payment(user, "starter", 1, recurrent=True)
    payment_service.complete_payment(order_id=starter_pay.order_id)
    starter = subscription_service.get_active_subscription(user)
    assert starter is not None
    assert starter.plan == "starter"
    starter_end = starter.ends_at

    dosage_pay = payment_service.start_payment(user, "dosage", 1, recurrent=True)
    payment_service.complete_payment(order_id=dosage_pay.order_id)

    starter_after = subscription_service.get_active_subscription(user)
    dosage = subscription_service.get_active_plan_subscription(user, "dosage")
    assert starter_after is not None
    assert starter_after.plan == "starter"
    assert starter_after.ends_at == starter_end
    assert dosage is not None
    assert dosage.plan == "dosage"
    assert dosage_pay.amount == 200
    assert subscription_service.can_use_feature(user, "planned_consultation")
    assert subscription_service.can_use_feature(user, "drug_dosage")
    assert not subscription_service.can_use_feature(user, "dose_calculator")

    calc_pay = payment_service.start_payment(user, "calculator", 1)
    payment_service.complete_payment(order_id=calc_pay.order_id)
    assert calc_pay.amount == 300
    assert subscription_service.get_active_plan_subscription(user, "calculator")
    assert subscription_service.can_use_feature(user, "dose_calculator")
    # Still three concurrent actives: starter + dosage + calculator
    assert len(subscription_service.list_active_subscriptions(user)) == 3


def test_addon_renewal_stacks_same_plan_only(memory_db):
    user = User.create(vk_id=700007, created_at=datetime.utcnow())
    pay1 = payment_service.start_payment(user, "dosage", 1)
    payment_service.complete_payment(order_id=pay1.order_id)
    first = subscription_service.get_active_plan_subscription(user, "dosage")
    first_end = first.ends_at

    pay2 = payment_service.start_payment(user, "dosage", 1)
    payment_service.complete_payment(order_id=pay2.order_id)
    second = subscription_service.get_active_plan_subscription(user, "dosage")
    assert second.ends_at > first_end
    assert first.id != second.id
    assert Subscription.get_by_id(first.id).status == "expired"
