import logging

import vk_api
from vk_api.bot_longpoll import VkBotEventType, VkBotLongPoll

import config
from bot.router import route_event, route_message
from db import init_db
from integrations import vk
from migrations.seed import run_seed
from services import notification_service

logger = logging.getLogger(__name__)


def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


def run_bot():
    setup_logging()
    init_db()
    run_seed()
    session, api = vk.init_vk()
    if not session or not api:
        logger.error("VK not configured. Set VK_GROUP_TOKEN and VK_GROUP_ID.")
        return

    notification_service.set_vk_sender(
        lambda uid, msg, kb=None: vk.send_message(uid, msg, kb)
    )

    vk.ensure_longpoll_events()

    longpoll = VkBotLongPoll(session, config.VK_GROUP_ID, wait=25)
    logger.info("ExoCare bot started (group %s)", config.VK_GROUP_ID)

    from services.doctor_notify import log_doctor_destinations_status
    log_doctor_destinations_status()

    from scheduler.jobs import start_scheduler
    start_scheduler()

    for event in longpoll.listen():
        try:
            if event.type == VkBotEventType.MESSAGE_NEW:
                msg = event.message
                peer_id = msg["peer_id"]
                from_id = msg["from_id"]
                text = msg.get("text", "")
                logger.info(
                    "MESSAGE_NEW from_id=%s peer_id=%s text=%r",
                    from_id,
                    peer_id,
                    (text or "")[:80],
                )
                attachments = vk.prepare_outgoing_attachments(msg, peer_id=peer_id)
                if from_id < 0:
                    continue
                route_message(
                    peer_id,
                    from_id,
                    text,
                    attachments if attachments else None,
                    payload_raw=msg.get("payload"),
                )
            elif event.type == VkBotEventType.MESSAGE_EVENT:
                ev = event.obj
                peer_id = ev["peer_id"]
                user_id = ev["user_id"]
                logger.info(
                    "MESSAGE_EVENT user_id=%s peer_id=%s payload=%r",
                    user_id,
                    peer_id,
                    str(ev.get("payload"))[:120],
                )
                snackbar = None
                try:
                    snackbar = route_event(peer_id, user_id, ev.get("payload"), dict(ev))
                except Exception:
                    logger.exception("Callback event handling error")
                finally:
                    vk.answer_message_event(
                        ev["event_id"], user_id, peer_id, text=snackbar
                    )
            else:
                logger.debug("Event type=%s", event.type)
        except Exception:
            logger.exception("Event handling error")
