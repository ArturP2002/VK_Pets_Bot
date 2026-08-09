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
