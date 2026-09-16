# ExoCare VK Bot (MVP)

VK-бот для круглосуточной онлайн-помощи владельцам экзотических животных.

## Стек

- Python 3.12+
- [vk_api](https://github.com/python273/vk_api) (Long Poll)
- Peewee + **PostgreSQL** (основная БД бота; SQLite — fallback через `DB_PATH`)
- Отдельный SQLite `data/formulary.db` — справочник дозировок (FTS5)
- Flask (webhook оплаты)
- APScheduler (напоминания подписки)

## Запуск

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Заполните VK_GROUP_TOKEN, VK_GROUP_ID, VK_DOCTOR_CHAT_ID, VK_ADMIN_IDS, VK_DOCTOR_IDS
# И DATABASE_URL=postgresql://user:pass@localhost:5432/exocare

python main.py seed   # БД + начальные данные
python main.py        # Бот
python main.py webhook  # Webhook Т-Банка / mock (порт WEBHOOK_PORT)
```

### Миграция данных SQLite → PostgreSQL

```bash
# 1. Создайте БД в Postgres, пропишите DATABASE_URL в .env
# 2. Скопируйте данные из старого файла:
python main.py migrate-pg --sqlite data/VK_Pets_DB.db
# 3. Перезапустите бота (он подхватит DATABASE_URL)
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

После правок справочника на сервере (`/opt/exocare`):

1. Бэкап `data/formulary.db`, `data/chroma_formulary/` и основной sqlite (`bash scripts/backup.sh`).
2. `git pull` нужной ветки. Файл `.env` не затирать.
3. `source .venv/bin/activate && pip install -r requirements.txt`
4. PDF/docx должны лежать в `knowledge_base/` (они не в git). Для перевода имён нужен ключ LLM в `.env`.
5. Пересобрать справочник: `python -m scripts.formulary` (или скопировать уже собранные `data/formulary.db` и `data/chroma_formulary/`).
6. `python -m scripts.formulary validate`
7. `sudo systemctl restart exocare-bot` (webhook — только если менялся платёжный код).
8. Проверка: `systemctl status exocare-bot` и `journalctl -u exocare-bot -n 100 --no-pager`.

## Структура

```
main.py, config.py, db.py
models/, services/, bot/, integrations/, api/, scheduler/, migrations/
```
