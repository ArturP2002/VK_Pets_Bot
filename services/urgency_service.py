import json

RED_KEYWORDS = [
    "кровотечение", "судорог", "дыхание", "сознан", "дефекац",
    "мочеиспуск", "не ходит в туалет", "отказ от еды", "рвот",
    "болезнен", "скрежет", "неподвиж",
]
YELLOW_KEYWORDS = [
    "вялост", "диаре", "рвота", "кровь в моче", "слюнотеч",
]
GREEN_KEYWORDS = [
    "кормлен", "вакцин", "профилакт", "планов",
]


def _text_blob(symptoms: dict) -> str:
    return json.dumps(symptoms, ensure_ascii=False).lower()


def classify_urgency(symptoms: dict, worsening: bool = False) -> str:
    text = _text_blob(symptoms)
    if worsening:
        if any(k in text for k in RED_KEYWORDS):
            return "red"
        return "yellow"
    if any(k in text for k in RED_KEYWORDS):
        return "red"
    if any(k in text for k in YELLOW_KEYWORDS):
        return "yellow"
    if any(k in text for k in GREEN_KEYWORDS):
        return "green"
    complaint = (symptoms.get("complaints") or "").lower()
    if any(k in complaint for k in RED_KEYWORDS):
        return "red"
    if any(k in complaint for k in YELLOW_KEYWORDS):
        return "yellow"
    return "green"


def urgency_label(level: str) -> str:
    return {
        "red": "🔴 СРОЧНО",
        "yellow": "🟡 СРЕДНЯЯ СРОЧНОСТЬ",
        "green": "🟢 ПЛАНОВО",
    }.get(level, "🟢 ПЛАНОВО")
