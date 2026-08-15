"""Тексты и метаданные тарифов подписки."""
from __future__ import annotations

import config
from models import Payment, RecurrentAgreement, Subscription, User

PLAN_ORDER = ("starter", "basic", "premium")
ADDON_PLAN_ORDER = ("dosage", "calculator")

PLANS: dict[str, dict] = {
    "starter": {
        "name": "Стартовый",
        "tagline": "Базовые вопросы по кормлению и содержанию",
        "features": [
            "базовые вопросы по кормлению и содержанию животных (SLA 12–24 ч)",
            "1 питомец в профиле",
            "Чат по виду животного (кролик, хищники, грызун и др.)",
            "Не входит: экстренная помощь 24/7",
        ],
    },
    "basic": {
        "name": "Базовый",
        "tagline": "Экстренная помощь и несколько питомцев",
        "features": [
            "Всё из тарифа «Стартовый»",
            "Экстренная помощь 24/7 (SLA 4–6 ч)",
            "Несколько питомцев в профиле",
            "Заявки на лечение и сопровождение (дежурным специалистом)",
            "Скидка 10% в партнёрских клиниках (тестовый режим)",
            "неограниченное количество обращений",
        ],
    },
    "premium": {
        "name": "Премиум",
        "tagline": "Максимальный уровень поддержки",
        "features": [
            "Всё из тарифа «Базовый»",
            "Приоритетная обработка заявок (SLA до 1 часа)",
            "Закреплённый врач и расширенное сопровождение",
            "Возможность связи по телефону при необходимости",
            "хранение истории болезни животного",
            "возможность вызова врача в клинику (по согласованию с врачом)",
            "Скидка 10% в партнёрских клиниках (тестовый режим)",
        ],
    },
    "dosage": {
        "name": "Дозировки",
        "tagline": "Справочник препаратов без лимита",
        "features": [
            "Неограниченный поиск дозировок препаратов",
            "Без задержки выдачи ответа",
            "Уточняющие вопросы по карточке препарата",
            "Кнопка «Спросить ИИ» при отсутствии в базе",
            "Не включает калькулятор дозы и консультации врача",
        ],
    },
    "calculator": {
        "name": "Калькулятор дозы",
        "tagline": "Расчёт дозы по весу и форме выпуска",
        "features": [
            "Свободный текст → расчёт таблеток/мл",
            "Подстановка мг/кг из справочника при необходимости",
            "Проверка min–max по справочнику",
            "Не включает модуль дозировок и консультации врача",
        ],
    },
}

ONE_TIME_PLAN = {
    "name": "Разовая консультация",
    "tagline": "Одна консультация без подписки",
    "features": [
        "Одна консультация по выбранному питомцу",
        "Подходит, если нужна разовая помощь",
        "Экстренная помощь не входит — только выбранный тип заявки",
    ],
}


def plan_name(plan_id: str) -> str:
    if plan_id == "one_time":
        return ONE_TIME_PLAN["name"]
    return PLANS.get(plan_id, {}).get("name", plan_id)


def min_monthly_price(plan_id: str) -> int | None:
    prices = config.TARIFF_PRICES.get(plan_id, {})
    return min(prices.values()) if prices else None


def plan_button_label(plan_id: str) -> str:
    name = plan_name(plan_id)
    price = min_monthly_price(plan_id)
    if price is not None:
        return f"{name} — от {price} ₽/мес"
    return name


def one_time_button_label() -> str:
    return config.ONE_TIME_BUTTON_LABEL


def period_label(months: int) -> str:
    if months == 1:
        return "1 месяц"
    if months in (2, 3, 4):
        return f"{months} месяца"
    return f"{months} месяцев"


def recurrent_charge_label(months: int, amount: int) -> str:
    return f"{amount} ₽ каждые {period_label(months)}"


def _format_prices_block(plan_id: str) -> str:
    prices = config.TARIFF_PRICES.get(plan_id, {})
    if not prices:
        return ""
    lines = ["Стоимость:"]
    for months in sorted(prices):
        total = prices[months]
        per_month = total // months
        discount = ""
        monthly_base = prices.get(1, total)
        if months > 1 and monthly_base * months > total:
            saved = monthly_base * months - total
            discount = f" (экономия {saved} ₽)"
        lines.append(
            f"• {months} мес — {total} ₽ ({per_month} ₽/мес){discount} "
            f"[автосписание: {recurrent_charge_label(months, total)}]"
        )
    return "\n".join(lines)


def format_catalog_overview() -> str:
    lines = [
        "📋 Тарифы ExoCare",
        "",
        "Все цены указаны в рублях РФ. При оформлении подписки включается автоматическое продление.",
        "Пробный период 5 дней — в главном меню.",
        "",
    ]
    for plan_id in PLAN_ORDER:
        plan = PLANS[plan_id]
        price = min_monthly_price(plan_id)
        lines.append(f"▸ {plan['name']} — от {price} ₽/мес")
        lines.append(f"  {plan['tagline']}")
        for feature in plan["features"][:2]:
            lines.append(f"  • {feature}")
        lines.append("")
    lines.append(f"▸ {ONE_TIME_PLAN['name']} — {config.ONE_TIME_CONSULTATION_PRICE} ₽")
    lines.append(f"  {ONE_TIME_PLAN['tagline']}")
    lines.append("")
    lines.append("Дополнительно (отдельно от консультаций):")
    for plan_id in ADDON_PLAN_ORDER:
        plan = PLANS[plan_id]
        price = min_monthly_price(plan_id)
        lines.append(f"▸ {plan['name']} — {price} ₽/мес")
        lines.append(f"  {plan['tagline']}")
    return "\n".join(lines)


def format_plan_detail(plan_id: str) -> str:
    if plan_id == "one_time":
        lines = [
            f"💳 {ONE_TIME_PLAN['name']}",
            ONE_TIME_PLAN["tagline"],
            "",
            f"Стоимость: {config.ONE_TIME_CONSULTATION_PRICE} ₽",
            "",
            "Включено:",
        ]
        lines.extend(f"• {f}" for f in ONE_TIME_PLAN["features"])
        return "\n".join(lines)

    plan = PLANS.get(plan_id)
    if not plan:
        return f"Тариф «{plan_id}» не найден."

    lines = [
        f"📦 {plan['name']}",
        plan["tagline"],
        "",
        "Включено:",
    ]
    lines.extend(f"• {f}" for f in plan["features"])
    prices = _format_prices_block(plan_id)
    if prices:
        lines.extend(
            [
                "",
                prices,
                "",
                "⚠️ Оформляется подписка с автоматическим продлением.",
                "С карты будет списываться выбранная сумма с указанной периодичностью без дополнительного подтверждения.",
                "",
                "Выберите период подписки:",
            ]
        )
    return "\n".join(lines)


def format_active_subscription(sub: Subscription) -> str:
    if sub.is_trial:
        plan_title = f"Пробный период ({PLANS['starter']['name']})"
        plan_id = "starter"
    else:
        plan_id = sub.plan
        plan_title = plan_name(plan_id)

    lines = [
        "✅ Активная подписка",
        "",
        f"Тариф: {plan_title}",
        f"Действует до: {sub.ends_at.strftime('%d.%m.%Y')}",
        f"Статус: {sub.status}",
    ]
    if sub.period_months and not sub.is_trial:
        lines.append(f"Период оплаты: {sub.period_months} мес.")

    if not sub.is_trial and sub.auto_renew:
        agreement = sub.recurrent_agreement
        if agreement:
            lines.append(f"Автопродление: включено")
            lines.append(f"Следующее списание: {recurrent_charge_label(agreement.period_months, agreement.amount)}")
            if agreement.next_charge_at:
                lines.append(f"Дата списания: {agreement.next_charge_at.strftime('%d.%m.%Y')}")
            if agreement.card_mask:
                lines.append(f"Карта: {agreement.card_mask}")
        else:
            lines.append("Автопродление: включено")
    elif not sub.is_trial:
        lines.append("Автопродление: отключено")

    plan = PLANS.get(plan_id)
    if plan:
        lines.extend(["", "Ваши возможности:"])
        lines.extend(f"• {f}" for f in plan["features"])

    lines.extend(["", "Ниже можно оформить другой тариф или продлить подписку."])
    return "\n".join(lines)


def format_active_subscriptions(subs: list[Subscription]) -> str:
    """Render one or more concurrent subscriptions (consultation + addons)."""
    if not subs:
        return format_no_subscription()
    if len(subs) == 1:
        return format_active_subscription(subs[0])

    blocks: list[str] = ["✅ Активные подписки", ""]
    for i, sub in enumerate(subs):
        if sub.is_trial:
            title = f"Пробный период ({PLANS['starter']['name']})"
        else:
            title = plan_name(sub.plan)
        renew = ""
        if not sub.is_trial:
            renew = " · автопродление" if sub.auto_renew else " · без автопродления"
        blocks.append(
            f"▸ {title}: до {sub.ends_at.strftime('%d.%m.%Y')} [{sub.status}]{renew}"
        )
        plan = PLANS.get("starter" if sub.is_trial else sub.plan)
        if plan:
            for feature in plan["features"][:2]:
                blocks.append(f"  • {feature}")
        if i + 1 < len(subs):
            blocks.append("")
    blocks.extend(["", "Ниже можно оформить другой тариф или продлить подписку."])
    return "\n".join(blocks)


def format_no_subscription() -> str:
    return (
        "У вас нет активной подписки.\n"
        "Оформите тариф или активируйте пробный период 5 дней в главном меню.\n\n"
        + format_catalog_overview()
    )


def format_payment_summary(payment: Payment) -> str:
    if payment.tariff == "one_time":
        return (
            f"💳 {ONE_TIME_PLAN['name']}\n\n"
            f"Сумма к оплате: {payment.amount} ₽\n"
            f"Заказ: {payment.order_id}\n"
            f"Способ оплаты: {payment.provider}"
        )

    plan_id = payment.tariff
    months = payment.period_months or 1
    prices = config.TARIFF_PRICES.get(plan_id, {})
    per_month = prices.get(months, payment.amount) // months if months else payment.amount

    return (
        f"💳 Оформление подписки с автопродлением\n\n"
        f"Тариф: {plan_name(plan_id)}\n"
        f"Период: {period_label(months)}\n"
        f"Сумма списания: {payment.amount} ₽ ({per_month} ₽/мес)\n"
        f"Периодичность автосписаний: {recurrent_charge_label(months, payment.amount)}\n"
        f"Валюта: RUB (рубли РФ)\n"
        f"Заказ: {payment.order_id}\n"
        f"Способ оплаты: {payment.provider}\n\n"
        f"{config.CANCEL_INSTRUCTIONS_TEXT}"
    )


def format_activation_success(sub: Subscription | None, payment: Payment) -> str:
    if payment.tariff == "one_time":
        return (
            "✅ Разовая консультация оплачена.\n\n"
            "Сейчас оформим заявку — ответьте на несколько вопросов о питомце."
        )

    plan_id = payment.tariff
    ends = sub.ends_at.strftime("%d.%m.%Y") if sub else "—"
    months = payment.period_months or 1
    plan = PLANS.get(plan_id, {})

    lines = [
        "✅ Подписка активирована!",
        "",
        f"Тариф: {plan_name(plan_id)}",
        f"Период: {period_label(months)}",
        f"Действует до: {ends}",
        "",
        "Карта привязана. Дальнейшие списания будут происходить автоматически "
        f"({recurrent_charge_label(months, payment.amount)}).",
        config.CANCEL_INSTRUCTIONS_TEXT,
    ]
    if plan.get("features"):
        lines.extend(["", "Теперь доступно:"])
        lines.extend(f"• {f}" for f in plan["features"])
    if plan_id in ("dosage", "calculator"):
        lines.append("\nМодуль доступен из главного меню.")
    else:
        lines.append("\nМожете создавать заявки через главное меню.")
    return "\n".join(lines)


def format_payment_history(payments: list[Payment]) -> str:
    if not payments:
        return "💳 История оплат: пока нет."
    lines = ["💳 История оплат:"]
    for p in payments:
        dt = (p.paid_at or p.created_at).strftime("%d.%m.%Y")
        name = plan_name(p.tariff)
        kind = " (автосписание)" if p.is_recurrent_charge else ""
        lines.append(f"• {dt} — {name}, {p.amount} ₽{kind} [{p.status}]")
    return "\n".join(lines)


def format_checkout_consent(plan_id: str, months: int, amount: int) -> str:
    contacts_hint = "По возврату и отмене подписки — раздел «Контакты» в главном меню."
    if plan_id == "one_time":
        return (
            f"💳 Подтверждение оплаты\n\n"
            f"Услуга: {ONE_TIME_PLAN['name']}\n"
            f"Сумма к оплате: {amount} ₽\n"
            f"Валюта: RUB (рубли РФ)\n\n"
            f"{config.REFUND_POLICY_TEXT}\n"
            f"{contacts_hint}\n\n"
            "Ознакомьтесь с документами во вложении, затем нажмите зелёную кнопку "
            "«принять правила оплаты exo care и перейти к оплате»."
        )

    return (
        f"💳 Подтверждение подписки с автопродлением\n\n"
        f"Тариф: {plan_name(plan_id)}\n"
        f"Сумма списания: {amount} ₽\n"
        f"Периодичность: {recurrent_charge_label(months, amount)}\n"
        f"Валюта: RUB (рубли РФ)\n\n"
        "Платежи будут списываться безакцептно с привязанной карты "
        f"каждые {period_label(months)} до отмены автопродления.\n\n"
        f"{config.REFUND_POLICY_TEXT}\n"
        f"{contacts_hint}\n\n"
        "Ознакомьтесь с документами во вложении, затем нажмите зелёную кнопку "
        "«принять правила оплаты exo care и перейти к оплате»."
    )


def format_cancel_auto_renew_confirm(agreement: RecurrentAgreement | None) -> str:
    if not agreement:
        return "У вас нет активного автопродления."
    return (
        "Отменить автопродление подписки?\n\n"
        f"Тариф: {plan_name(agreement.plan)}\n"
        f"Списание: {recurrent_charge_label(agreement.period_months, agreement.amount)}\n\n"
        "Доступ к сервису сохранится до конца оплаченного периода.\n"
        f"{config.REFUND_POLICY_TEXT}"
    )


def format_cancel_auto_renew_success(ends_at: str) -> str:
    return (
        f"✅ Автопродление отключено.\n\n"
        f"Доступ сохранится до: {ends_at}\n\n"
        f"По возврату и вопросам: {config.CONTACTS_TEXT}"
    )


def format_subscription_history(user: User) -> str:
    subs = list(
        Subscription.select()
        .where(Subscription.user == user)
        .order_by(Subscription.started_at.desc())
        .limit(10)
    )
    if not subs:
        return "📜 История подписок: пока нет."
    lines = ["📜 История подписок:"]
    for s in subs:
        if s.is_trial:
            title = f"Пробный ({PLANS['starter']['name']})"
        else:
            title = plan_name(s.plan)
        lines.append(
            f"• {title}: {s.started_at.strftime('%d.%m.%Y')} — "
            f"{s.ends_at.strftime('%d.%m.%Y')} [{s.status}]"
        )
    return "\n".join(lines)
