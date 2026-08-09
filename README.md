# ExoCare VK Bot (MVP)

VK-бот для круглосуточной онлайн-помощи владельцам экзотических животных.

## Стек

- Python 3.12+
- [vk_api](https://github.com/python273/vk_api) (Long Poll)
- Peewee + SQLite
- Flask (webhook оплаты)
- APScheduler (напоминания подписки)

## Запуск

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Заполните VK_GROUP_TOKEN, VK_GROUP_ID, VK_DOCTOR_CHAT_ID, VK_ADMIN_IDS, VK_DOCTOR_IDS

python main.py seed   # БД + начальные данные
python main.py        # Бот
python main.py webhook  # Webhook Т-Банка / mock (порт WEBHOOK_PORT)
```

## Режим оплаты

- `PAYMENT_PROVIDER=mock` — тестовые кнопки «Оплатить успешно / не прошла»
- `PAYMENT_PROVIDER=tbank` — боевой Т-эквайринг (ключи в `.env`, HTTPS webhook)

## Команды врачей (VK)

| Команда | Описание |
|---------|----------|
| `/ticket 123` | Карточка заявки |
| `/assign 123` | Взять заявку, уведомить клиента (телефон + VK) |
| `/status 123 in_progress` | Смена статуса |
| `/note 123 текст` | Заключение в историю |
| `/complete 123` | Завершить |

Консультация с клиентом — **в личке VK и по телефону**, не через бота.

## Тесты

```bash
pip install pytest
pytest tests/ -v
```

## Бэкап

```bash
bash scripts/backup.sh
```

## Деплой

Примеры unit-файлов: `deploy/exocare-bot.service`, `deploy/exocare-webhook.service`.

Nginx проксирует HTTPS на `WEBHOOK_PORT` для `/webhook/tbank`.

## Структура

```
main.py, config.py, db.py
models/, services/, bot/, integrations/, api/, scheduler/, migrations/
```
