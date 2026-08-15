"""Application configuration from environment."""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
(DATA_DIR / "uploads").mkdir(parents=True, exist_ok=True)

DB_PATH = os.getenv("DB_PATH", str(DATA_DIR / "VK_Pets_DB.db"))

# Formulary / dosage modules (separate DB from tickets)
FORMULARY_DB = os.getenv("FORMULARY_DB", str(DATA_DIR / "formulary.db"))
FORMULARY_CHROMA_DIR = os.getenv(
    "FORMULARY_CHROMA_DIR", str(DATA_DIR / "chroma_formulary")
)
FORMULARY_CHROMA_COLLECTION = os.getenv(
    "FORMULARY_CHROMA_COLLECTION", "formulary_chunks"
)
FORMULARY_EMBEDDING_MODEL = os.getenv(
    "FORMULARY_EMBEDDING_MODEL", "paraphrase-multilingual-MiniLM-L12-v2"
)
FORMULARY_SEARCH_MIN_SCORE = float(os.getenv("FORMULARY_SEARCH_MIN_SCORE", "70"))
FORMULARY_RAG_TOP_K = int(os.getenv("FORMULARY_RAG_TOP_K", "6") or 6)
FORMULARY_DOSAGE_DELAY_SEC = int(os.getenv("FORMULARY_DOSAGE_DELAY_SEC", "30") or 30)
FORMULARY_FREE_LIMIT_PER_24H = int(os.getenv("FORMULARY_FREE_LIMIT_PER_24H", "2") or 2)

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-5")
CLAUDE_MAX_TOKENS = int(os.getenv("CLAUDE_MAX_TOKENS", "2048") or 2048)

VK_GROUP_TOKEN = os.getenv("VK_GROUP_TOKEN", "")
VK_GROUP_ID = int(os.getenv("VK_GROUP_ID", "0") or 0)
VK_DOCTOR_CHAT_ID = int(os.getenv("VK_DOCTOR_CHAT_ID", "0") or 0)
VK_DOCTOR_CHAT_TITLE = os.getenv("VK_DOCTOR_CHAT_TITLE", "Чат врачей")
VK_DOCTOR_PROFILE_ID = int(os.getenv("VK_DOCTOR_PROFILE_ID", "1118186286") or 0)
VK_DOCTOR_PROFILE_URL = os.getenv("VK_DOCTOR_PROFILE_URL", "")

VK_CHAT_URGENCY_RED_TITLE = os.getenv("VK_CHAT_URGENCY_RED_TITLE", "")
VK_CHAT_URGENCY_YELLOW_TITLE = os.getenv("VK_CHAT_URGENCY_YELLOW_TITLE", "")
VK_CHAT_URGENCY_GREEN_TITLE = os.getenv("VK_CHAT_URGENCY_GREEN_TITLE", "")
VK_CHAT_URGENCY_RED_ID = int(os.getenv("VK_CHAT_URGENCY_RED_ID", "0") or 0)
VK_CHAT_URGENCY_YELLOW_ID = int(os.getenv("VK_CHAT_URGENCY_YELLOW_ID", "0") or 0)
VK_CHAT_URGENCY_GREEN_ID = int(os.getenv("VK_CHAT_URGENCY_GREEN_ID", "0") or 0)

def _parse_ids(value: str) -> list[int]:
    if not value:
        return []
    return [int(x.strip()) for x in value.split(",") if x.strip().isdigit()]


VK_ADMIN_IDS = _parse_ids(os.getenv("VK_ADMIN_IDS", ""))
VK_DOCTOR_IDS = _parse_ids(os.getenv("VK_DOCTOR_IDS", ""))

PAYMENT_PROVIDER = os.getenv("PAYMENT_PROVIDER", "mock").lower()
TBANK_TERMINAL_KEY = os.getenv("TBANK_TERMINAL_KEY", "")
TBANK_PASSWORD = os.getenv("TBANK_PASSWORD", "")
WEBHOOK_BASE_URL = os.getenv("WEBHOOK_BASE_URL", "http://localhost:5000")
WEBHOOK_HOST = os.getenv("WEBHOOK_HOST", "0.0.0.0")
WEBHOOK_PORT = int(os.getenv("WEBHOOK_PORT", "5000"))
# Deep link after payment SuccessURL/FailURL (browser → back to VK bot)
VK_BOT_RETURN_URL = os.getenv("VK_BOT_RETURN_URL", "").strip()
if not VK_BOT_RETURN_URL and VK_GROUP_ID:
    VK_BOT_RETURN_URL = f"https://vk.me/club{VK_GROUP_ID}"
elif not VK_BOT_RETURN_URL:
    VK_BOT_RETURN_URL = "https://vk.com"

TRIAL_DAYS = int(os.getenv("TRIAL_DAYS", "5"))

SERVICE_INFO_TEXT = os.getenv(
    "SERVICE_INFO_TEXT",
    (
        "🐾 Миссия бренда ExoCare+\n"
        "ExoCare+ обеспечивает специализированную помощь экзотическим животным доступной 24/7.\n"
        "Мы верим, что владелец особенного питомца не должен оставаться один на один "
        "с тревогой посреди ночи в поисках специалиста «хоть где-то». "
        "Наша миссия — быть тем самым специалистом, который всегда на связи: "
        "закреплённый врач, понятная оценка срочности и прямой путь в проверенную клинику, "
        "если нужна очная помощь.\n"
        "ExoCare+ продаёт не консультации. Мы продаём спокойствие владельца "
        "и уверенность, что его питомец под присмотром.\n\n"
        "💳 Тарифы\n\n"
        "🌱 Стартовый\n"
        "• 999 ₽ месяц\n"
        "• 2 699 ₽ 3 месяца\n"
        "• 5 399 ₽ 6 месяцев\n"
        "Это вход в комьюнити ExoCare+ 🐹 — закрытый чат владельцев экзотических животных, "
        "где можно задавать базовые вопросы по кормлению, содержанию, "
        "профилактическим обработкам и вакцинации. Отвечаем в течение 12–24 часов.\n"
        "Экстренные консультации и вопросы по лечению в этот тариф не входят: "
        "если питомцу нужна медицинская помощь, можно оформить подписку уровнем выше "
        "или обратиться за разовой консультацией за 2 500 ₽.\n\n"
        "⚡ Базовый\n"
        "• 2 999 ₽ месяц\n"
        "• 8 099 ₽ 3 месяца\n"
        "• 16 199 ₽ 6 месяцев\n"
        "Помимо комьюнити, открывается главное — работа с чат-ботом ExoCare assistant 🤖. "
        "Без ограничений по числу обращений доступна и 🚨 экстренная помощь, "
        "и полноценные 💬 консультации сразу по нескольким животным: "
        "коррекция терапии, назначение диагностики, расшифровка анализов, "
        "первичные и повторные приёмы. Отвечает дежурный специалист, "
        "среднее время ответа — до 30 минут. "
        "В экстренной ситуации поможем найти подходящую клинику в вашем городе.\n\n"
        "👑 Премиум\n"
        "• 6 999 ₽ месяц\n"
        "• 18 899 ₽ 3 месяца\n"
        "• 37 799 ₽ 6 месяцев\n"
        "Всё из Базового тарифа плюс самый заботливый уровень сервиса. "
        "За питомцем закрепляется постоянный врач 🩺, который знает его историю "
        "и не начинает диагностику с нуля при каждом обращении. "
        "Работает Priority Support 24/7 — приоритетная очередь, ускоренный ответ "
        "и возможность созвониться с врачом голосом 📞. "
        "В экстренной ситуации подключается полная Emergency Coordination: "
        "свяжемся с клиникой-партнёром, поможем организовать приём, "
        "передадим историю болезни и напрямую скоординируем врачей. "
        "При необходимости рассматривается и экстренный выезд экзотолога в клинику.\n\n"
        "Во всех тарифах отдельно от подписки оплачиваются только услуги самой "
        "клиники-партнёра — диагностика, стационар, манипуляции, очный приём и лечение. "
        "ExoCare+ берёт на себя всё остальное: маршрутизацию, координацию "
        "и заботу на каждом шаге 🐾"
    ),
)
CONTACTS_TEXT = os.getenv(
    "CONTACTS_TEXT",
    (
        "Контакты поддержки ExoCare:\n"
        "Email: support@exocare.example\n"
        "Телефон: +7 (000) 000-00-00\n\n"
        "По вопросам возврата, отмены подписки и автоплатежей обращайтесь по указанным контактам."
    ),
)
REFUND_POLICY_TEXT = os.getenv(
    "REFUND_POLICY_TEXT",
    (
        "Возврат средств возможен в случаях, предусмотренных офертой и законодательством РФ. "
        "Для обращения напишите на email поддержки или позвоните по телефону из раздела «Контакты»."
    ),
)
CANCEL_INSTRUCTIONS_TEXT = os.getenv(
    "CANCEL_INSTRUCTIONS_TEXT",
    (
        "Отменить автопродление подписки: кнопка «Отменить автопродление» в главном меню "
        "или раздел «Моя подписка». Доступ сохранится до конца оплаченного периода."
    ),
)

TBANK_NOTIFICATION_URL = os.getenv("TBANK_NOTIFICATION_URL", "")
TBANK_OPERATION_INITIATOR_TYPE = os.getenv("TBANK_OPERATION_INITIATOR_TYPE", "2")
TBANK_SEND_RECEIPT = os.getenv("TBANK_SEND_RECEIPT", "false").lower() in ("1", "true", "yes")
TBANK_TAXATION = os.getenv("TBANK_TAXATION", "usn_income")
# Recurrent=Y в Init ломает официальные тест-кейсы DEMO; включать только на боевом терминале.
_tb_recurrent_raw = os.getenv("TBANK_ENABLE_RECURRENT", "").strip().lower()
if _tb_recurrent_raw in ("1", "true", "yes"):
    TBANK_ENABLE_RECURRENT = True
elif _tb_recurrent_raw in ("0", "false", "no"):
    TBANK_ENABLE_RECURRENT = False
else:
    TBANK_ENABLE_RECURRENT = not str(TBANK_TERMINAL_KEY).upper().endswith("DEMO")

SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587") or 587)
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM = os.getenv("SMTP_FROM", "noreply@exocare.example")

# Tariff prices: plan -> months -> rubles
TARIFF_PRICES = {
    "starter": {1: 999, 3: 2699, 6: 5399},
    "basic": {1: 2999, 3: 8099, 6: 16199},
    "premium": {1: 6999, 3: 18899, 6: 37799},
    # Separate SKUs — do not unlock via starter/basic/premium
    "dosage": {1: int(os.getenv("DOSAGE_PLAN_PRICE", "200") or 200)},
    "calculator": {1: int(os.getenv("CALCULATOR_PLAN_PRICE", "300") or 300)},
}
ONE_TIME_CONSULTATION_PRICE = 2500
ONE_TIME_BUTTON_LABEL = os.getenv("ONE_TIME_BUTTON_LABEL", "Разовая консультация")

LEGAL_DOC_TYPES = [
    ("offer", "Договор публичной оферты"),
    ("privacy", "Политика конфиденциальности"),
    ("personal_data", "Согласие на обработку персональных данных"),
    ("order", "Приказ"),
]

# Локальные файлы из BotDocs (подстрока в имени файла → doc_type)
BOT_DOCS_DIR = BASE_DIR / "BotDocs"
LEGAL_BOT_DOC_MATCHERS = {
    "offer": "оферты",
    "privacy": "конфиденциальности",
    "personal_data": "персональных",
    "order": "приказ",
}

# Опциональные внешние ссылки (если заданы — показываются вместе с вложениями)
LEGAL_ONBOARDING_PACKAGE_URL = os.getenv("LEGAL_ONBOARDING_PACKAGE_URL", "")
LEGAL_CHECKOUT_PACKAGE_URL = os.getenv("LEGAL_CHECKOUT_PACKAGE_URL", "")
PARTNERS_CATALOG_URL = os.getenv("PARTNERS_CATALOG_URL", "")

ONBOARDING_LEGAL_DOC_TYPES = [doc_type for doc_type, _ in LEGAL_DOC_TYPES]

CHECKOUT_CONSENT_KEYS = {
    "subscription": ["offer", "privacy", "personal_data", "order"],
    "one_time": ["offer", "privacy", "personal_data", "order"],
}

SPECIES_CHAT_ENV = {
    "рептилия": "VK_CHAT_REPTILE",
    "птица": "VK_CHAT_BIRD",
    "грызун": "VK_CHAT_RODENT",
    "другое": "VK_CHAT_OTHER",
}

SUBSCRIPTION_REMINDER_DAYS = [7, 3, 1, 0]
