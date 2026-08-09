import logging
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler

import config
from models import Subscription, SubscriptionReminderSent
from services import chat_service, notification_service, recurrent_billing_service, subscription_service

logger = logging.getLogger(__name__)
_scheduler = None


def _reminder_sent(sub: Subscription, days_before: int) -> bool:
    return (
        SubscriptionReminderSent.select()
        .where(
            (SubscriptionReminderSent.subscription == sub)
            & (SubscriptionReminderSent.days_before == days_before)
        )
        .exists()
    )


def _mark_reminder_sent(sub: Subscription, days_before: int):
    SubscriptionReminderSent.create(
        subscription=sub,
        days_before=days_before,
        sent_at=datetime.utcnow(),
    )


def check_subscription_reminders():
    now = datetime.utcnow()
    for days in config.SUBSCRIPTION_REMINDER_DAYS:
        if days == 0:
            target_date = now.date()
            when = "сегодня"
        else:
            target_date = (now + timedelta(days=days)).date()
            when = f"через {days} дн."
        subs = Subscription.select().where(Subscription.status == "active")
        for sub in subs:
            if sub.ends_at.date() != target_date:
                continue
            if _reminder_sent(sub, days):
                continue
            notification_service.queue_notification(
                sub.user.vk_id,
                "subscription_reminder",
                {"when": when},
            )
            _mark_reminder_sent(sub, days)
    notification_service.process_pending()


def check_expired_subscriptions():
    now = datetime.utcnow()
    expired = Subscription.select().where(
        (Subscription.status == "active") & (Subscription.ends_at < now)
    )
    for sub in expired:
        subscription_service.expire_subscription(sub)
        user = sub.user
        chat_service.remove_from_all_chats(user)
        if not _reminder_sent(sub, -1):
            notification_service.queue_notification(
                user.vk_id,
                "subscription_reminder",
                {"when": "истекла — продлите подписку"},
            )
            _mark_reminder_sent(sub, -1)
    notification_service.process_pending()


def run_daily_backup():
    script = Path(__file__).resolve().parent.parent / "scripts" / "backup.sh"
    try:
        result = subprocess.run(
            ["bash", str(script)],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            logger.error("Backup failed: %s", result.stderr or result.stdout)
        else:
            logger.info("Daily backup completed")
    except Exception:
        logger.exception("Backup job error")


def process_recurrent_billing():
    recurrent_billing_service.process_charge_reminders()
    recurrent_billing_service.process_recurrent_charges()


def start_scheduler():
    global _scheduler
    if _scheduler:
        return
    _scheduler = BackgroundScheduler()
    _scheduler.add_job(check_subscription_reminders, "cron", hour=10, minute=0)
    _scheduler.add_job(check_expired_subscriptions, "cron", hour=3, minute=0)
    _scheduler.add_job(process_recurrent_billing, "cron", hour=9, minute=0)
    _scheduler.add_job(run_daily_backup, "cron", hour=2, minute=30)
    _scheduler.add_job(notification_service.process_pending, "interval", minutes=2)
    _scheduler.start()
    logger.info("Scheduler started")
