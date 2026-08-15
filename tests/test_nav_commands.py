from bot.handlers.common import is_main_menu_command, is_start_command, normalize_nav_text


def test_normalize_nav_text_nbsp():
    assert normalize_nav_text("Главное\u00a0меню") == "главное меню"
    assert normalize_nav_text("  Главное   меню  ") == "главное меню"


def test_is_main_menu_command():
    assert is_main_menu_command("Главное меню")
    assert is_main_menu_command("меню")
    assert is_main_menu_command("Главное\u00a0меню")
    assert not is_main_menu_command("Моя подписка")


def test_is_start_command():
    assert is_start_command("Начать")
    assert is_start_command("/start")
    assert is_start_command("START")
