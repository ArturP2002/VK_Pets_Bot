from bot.keyboards import inline_grid_keyboard, ticket_list_keyboard


class _Ticket:
    def __init__(self, number: int, status: str):
        self.number = number
        self.status = status


def test_ticket_list_keyboard_ten_items():
    tickets = [_Ticket(i, "waiting_doctor") for i in range(1, 11)]
    kb = ticket_list_keyboard(tickets)
    assert kb
    assert "ticket_view" in kb


def test_doctor_ticket_keyboard_import():
    from bot.keyboards import doctor_ticket_keyboard

    kb = doctor_ticket_keyboard(5)
    assert "doc_assign" in kb
    assert "doc_complete" in kb


def test_inline_grid_keyboard_twelve_buttons():
    buttons = [(f"#{i}", {"cmd": "x", "n": i}) for i in range(1, 13)]
    kb = inline_grid_keyboard(buttons)
    assert kb.count('"action":') == 12


def test_one_time_button_label():
    from services.subscription_catalog import one_time_button_label

    assert one_time_button_label() == "Разовая консультация"


def test_main_menu_has_dosage_and_calculator():
    from bot.keyboards import main_menu_keyboard

    kb = main_menu_keyboard()
    assert "Дозировки препаратов (для врачей)" in kb
    assert "Калькулятор дозы (для владельцев)" in kb
    assert kb.index("Экстренная помощь") < kb.index("Дозировки препаратов")
    assert kb.index("Дозировки препаратов") < kb.index("Калькулятор дозы")
    assert kb.index("Калькулятор дозы") < kb.index("Консультация")


def test_dosage_upsell_keyboard():
    from bot.keyboards import dosage_upsell_keyboard

    kb = dosage_upsell_keyboard()
    assert "dosage" in kb
    assert "sub_plan" in kb


def test_subscription_plans_keyboard_includes_addons():
    from bot.keyboards import subscription_plans_keyboard

    kb = subscription_plans_keyboard()
    # Payload is JSON-encoded inside the keyboard JSON (escaped quotes).
    assert "dosage" in kb
    assert "calculator" in kb
    assert "200" in kb
    assert "300" in kb


def test_calculator_upsell_keyboard():
    from bot.keyboards import calculator_upsell_keyboard

    kb = calculator_upsell_keyboard()
    assert "calculator" in kb
    assert "300" in kb
