from services.urgency_service import classify_urgency, urgency_label


def test_red_bleeding():
    assert classify_urgency({"complaints": "сильное кровотечение"}) == "red"


def test_green_vaccination():
    assert classify_urgency({"complaints": "вопрос по вакцинации"}) == "green"


def test_worsening():
    assert classify_urgency({"complaints": "вялость"}, worsening=True) in ("yellow", "red")


def test_label():
    assert "СРОЧНО" in urgency_label("red")
