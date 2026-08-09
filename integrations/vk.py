import json
import logging
import os
import shutil
import tempfile
import time
import unicodedata
from pathlib import Path

import requests
import vk_api
from vk_api.keyboard import VkKeyboard, VkKeyboardColor
from vk_api.upload import VkUpload

import config

logger = logging.getLogger(__name__)

_vk_session = None
_vk_api = None
_resolved_doctor_peer_id: int | None = None
_resolved_urgency_peer_ids: dict[str, int | None] = {}


def init_vk():
    global _vk_session, _vk_api
    if not config.VK_GROUP_TOKEN:
        logger.warning("VK_GROUP_TOKEN not set")
        return None, None
    _vk_session = vk_api.VkApi(token=config.VK_GROUP_TOKEN)
    _vk_api = _vk_session.get_api()
    return _vk_session, _vk_api


def ensure_longpoll_events() -> bool:
    """Enable Bots Long Poll and required event types for the community token."""
    api = get_api()
    if not api or not config.VK_GROUP_ID:
        return False
    try:
        api.groups.setLongPollSettings(
            group_id=config.VK_GROUP_ID,
            enabled=1,
            api_version="5.199",
            message_new=1,
            message_event=1,
            message_reply=0,
            message_allow=0,
            message_deny=0,
        )
        settings = api.groups.getLongPollSettings(group_id=config.VK_GROUP_ID)
        events = settings.get("events") or {}
        logger.info(
            "Long Poll settings: enabled=%s message_new=%s message_event=%s",
            settings.get("is_enabled"),
            events.get("message_new"),
            events.get("message_event"),
        )
        if not settings.get("is_enabled"):
            logger.error("Long Poll API выключен в настройках сообщества.")
            return False
        if not events.get("message_new"):
            logger.error("Событие message_new выключено — бот не будет получать сообщения.")
            return False
        return True
    except Exception as e:
        logger.error("Не удалось настроить Long Poll: %s", e)
        return False


def get_api():
    if _vk_api is None:
        init_vk()
    return _vk_api


def get_session():
    if _vk_session is None:
        init_vk()
    return _vk_session


def send_message(peer_id: int, message: str, keyboard: str | None = None, attachment: str | None = None):
    api = get_api()
    if not api:
        return
    params = {
        "peer_id": peer_id,
        "message": message,
        "random_id": vk_api.utils.get_random_id(),
    }
    if keyboard:
        params["keyboard"] = keyboard
    if attachment:
        params["attachment"] = attachment
    api.messages.send(**params)


def send_message_attachments(
    peer_id: int,
    message: str,
    attachments: list[str],
    keyboard: str | None = None,
    batch_size: int = 10,
    keyboard_message: str | None = None,
):
    """Send message with attachments, splitting into batches of 10 (VK limit)."""
    if not attachments:
        send_message(peer_id, message, keyboard=keyboard)
        return
    chunks = [attachments[i : i + batch_size] for i in range(0, len(attachments), batch_size)]
    first_keyboard = keyboard if len(chunks) == 1 else None
    send_message(peer_id, message, keyboard=first_keyboard, attachment=",".join(chunks[0]))
    for chunk in chunks[1:]:
        send_message(peer_id, "📎", attachment=",".join(chunk))
    if keyboard and len(chunks) > 1:
        send_message(
            peer_id,
            keyboard_message or "Действия по заявке:",
            keyboard=keyboard,
        )


def upload_document_message(
    path: str,
    peer_id: int,
    title: str | None = None,
    retries: int = 2,
) -> str | None:
    """Upload a local file as a VK message document and return attachment string.

    Copies the file to a short ASCII temp name before upload: VK upload servers
    sometimes return an empty body for long/Unicode (macOS NFD) filenames,
    which surfaces as JSONDecodeError: Expecting value: line 1 column 1.
    """
    session = get_session()
    if not session:
        return None
    if not os.path.isfile(path):
        logger.warning("Document not found: %s", path)
        return None

    display_title = unicodedata.normalize("NFC", title or os.path.basename(path))
    suffix = Path(path).suffix.lower() or ".docx"
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp_path = tmp.name
        shutil.copy2(path, tmp_path)
        upload = VkUpload(session)
        last_error = None
        for attempt in range(1, retries + 1):
            try:
                saved = upload.document_message(
                    tmp_path,
                    title=display_title[:64],
                    peer_id=peer_id,
                )
                doc = saved.get("doc") if isinstance(saved, dict) else None
                if not doc and isinstance(saved, list) and saved:
                    doc = saved[0]
                if not doc:
                    logger.warning("Unexpected docs.save response: %s", saved)
                    return None
                return _attachment_string("doc", doc)
            except Exception as e:
                last_error = e
                logger.warning(
                    "Document upload failed for %s (attempt %s/%s): %s",
                    path,
                    attempt,
                    retries,
                    e,
                )
                if attempt < retries:
                    time.sleep(0.8 * attempt)
        logger.warning("Document upload gave up for %s: %s", path, last_error)
        return None
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def edit_message(
    peer_id: int,
    conversation_message_id: int,
    message: str,
    keyboard: str | None = None,
):
    api = get_api()
    if not api:
        return
    params = {
        "peer_id": peer_id,
        "conversation_message_id": conversation_message_id,
        "message": message,
    }
    if keyboard:
        params["keyboard"] = keyboard
    api.messages.edit(**params)


def answer_message_event(
    event_id: str,
    user_id: int,
    peer_id: int,
    text: str | None = None,
) -> bool:
    api = get_api()
    if not api:
        return False
    params = {
        "event_id": event_id,
        "user_id": user_id,
        "peer_id": peer_id,
    }
    if text:
        params["event_data"] = json.dumps(
            {"type": "show_snackbar", "text": text[:90]},
            ensure_ascii=False,
        )
    try:
        api.messages.sendMessageEventAnswer(**params)
        return True
    except Exception as e:
        logger.warning("sendMessageEventAnswer failed: %s", e)
        return False


def chat_peer_id(chat_id: int) -> int:
    """Accept short chat_id (58) or full peer_id from vk.com/im/convo/2000000058."""
    if chat_id >= 2000000000:
        return chat_id
    return 2000000000 + chat_id


def is_chat_peer_id(peer_id: int) -> bool:
    return peer_id >= 2000000000


def _conversation_accessible(peer_id: int) -> bool:
    api = get_api()
    if not api:
        return False
    try:
        result = api.messages.getConversationsById(peer_ids=peer_id)
        return bool(result.get("items"))
    except vk_api.exceptions.ApiError:
        return False


def find_chat_peer_id_by_title(title: str) -> int | None:
    """Find chat peer_id visible to the community token (not the user's browser URL)."""
    api = get_api()
    if not api or not title:
        return None
    offset = 0
    while offset < 500:
        result = api.messages.getConversations(count=200, offset=offset, extended=0)
        items = result.get("items", [])
        if not items:
            break
        for item in items:
            conv = item.get("conversation", {})
            peer = conv.get("peer", {})
            if peer.get("type") != "chat":
                continue
            chat_settings = conv.get("chat_settings") or {}
            if chat_settings.get("title") == title:
                return peer.get("id")
        if len(items) < 200:
            break
        offset += 200
    return None


def resolve_doctor_chat_peer_id() -> int | None:
    """Resolve doctor chat peer_id for the community token."""
    global _resolved_doctor_peer_id
    if _resolved_doctor_peer_id is not None:
        return _resolved_doctor_peer_id

    title = (config.VK_DOCTOR_CHAT_TITLE or "").strip()
    if title:
        found = find_chat_peer_id_by_title(title)
        if found:
            _resolved_doctor_peer_id = found
            if config.VK_DOCTOR_CHAT_ID and chat_peer_id(config.VK_DOCTOR_CHAT_ID) != found:
                logger.warning(
                    "VK_DOCTOR_CHAT_ID=%s не совпадает с беседой '%s' (peer_id=%s). "
                    "Используется peer_id сообщества. Удалите VK_DOCTOR_CHAT_ID или поставьте %s.",
                    config.VK_DOCTOR_CHAT_ID,
                    title,
                    found,
                    found - 2000000000 if found >= 2000000000 else found,
                )
            return found

    if config.VK_DOCTOR_CHAT_ID:
        peer_id = chat_peer_id(config.VK_DOCTOR_CHAT_ID)
        if _conversation_accessible(peer_id):
            _resolved_doctor_peer_id = peer_id
            return peer_id

    return None


def resolve_urgency_chat_peer_id(urgency: str) -> int | None:
    """Resolve staff chat peer_id by ticket urgency (red/yellow/green)."""
    global _resolved_urgency_peer_ids
    level = (urgency or "green").lower()
    if level not in ("red", "yellow", "green"):
        level = "green"
    if level in _resolved_urgency_peer_ids:
        return _resolved_urgency_peer_ids[level]

    title_map = {
        "red": config.VK_CHAT_URGENCY_RED_TITLE,
        "yellow": config.VK_CHAT_URGENCY_YELLOW_TITLE,
        "green": config.VK_CHAT_URGENCY_GREEN_TITLE,
    }
    id_map = {
        "red": config.VK_CHAT_URGENCY_RED_ID,
        "yellow": config.VK_CHAT_URGENCY_YELLOW_ID,
        "green": config.VK_CHAT_URGENCY_GREEN_ID,
    }
    title = (title_map.get(level) or "").strip()
    if title:
        found = find_chat_peer_id_by_title(title)
        if found:
            _resolved_urgency_peer_ids[level] = found
            chat_id = id_map.get(level) or 0
            if chat_id and chat_peer_id(chat_id) != found:
                logger.warning(
                    "VK_CHAT_URGENCY_%s_ID=%s не совпадает с беседой '%s' (peer_id=%s). "
                    "Используется peer_id сообщества.",
                    level.upper(),
                    chat_id,
                    title,
                    found,
                )
            return found

    chat_id = id_map.get(level) or 0
    if chat_id:
        peer_id = chat_peer_id(chat_id)
        if _conversation_accessible(peer_id):
            _resolved_urgency_peer_ids[level] = peer_id
            return peer_id

    _resolved_urgency_peer_ids[level] = None
    return None


def resolve_ticket_chat_peer_id(urgency: str) -> int | None:
    """Chat for new tickets: urgency-specific, then fallback to doctor chat."""
    peer = resolve_urgency_chat_peer_id(urgency)
    if peer:
        return peer
    return resolve_doctor_chat_peer_id()


def is_staff_chat(peer_id: int | None) -> bool:
    if not peer_id:
        return False
    if resolve_doctor_chat_peer_id() == peer_id:
        return True
    for level in ("red", "yellow", "green"):
        if resolve_urgency_chat_peer_id(level) == peer_id:
            return True
    return False


def check_chat_access(chat_id: int | None = None) -> tuple[bool, str | None]:
    """Return (ok, error_hint). Uses community-visible peer_id."""
    peer_id = resolve_doctor_chat_peer_id() if chat_id is None else chat_peer_id(chat_id)
    if not peer_id:
        return False, (
            f"Беседа '{config.VK_DOCTOR_CHAT_TITLE}' не найдена среди диалогов сообщества. "
            "Добавьте сообщество в чат и напишите от его имени любое сообщение в беседу."
        )
    if _conversation_accessible(peer_id):
        return True, None
    return False, (
        f"Нет доступа к peer_id={peer_id}. "
        "Проверьте, что сообщество добавлено в беседу, или задайте VK_DOCTOR_CHAT_TITLE."
    )


def send_to_chat(chat_id: int, message: str, keyboard: str | None = None):
    peer_id = resolve_doctor_chat_peer_id() or chat_peer_id(chat_id)
    send_message(peer_id, message, keyboard)


def fetch_screen_name(vk_id: int) -> str | None:
    api = get_api()
    if not api:
        return None
    try:
        users = api.users.get(user_ids=vk_id, fields="domain")
        if users:
            return users[0].get("domain") or None
    except Exception as e:
        logger.warning("users.get failed: %s", e)
    return None


def add_user_to_chat(chat_id: int, user_id: int) -> bool:
    api = get_api()
    if not api:
        return False
    try:
        api.messages.addChatUser(chat_id=chat_id, user_id=user_id)
        return True
    except Exception as e:
        logger.warning("addChatUser failed: %s", e)
        return False


def remove_user_from_chat(chat_id: int, user_id: int) -> bool:
    api = get_api()
    if not api:
        return False
    try:
        api.messages.removeChatUser(chat_id=chat_id, member_id=user_id)
        return True
    except Exception as e:
        logger.warning("removeChatUser failed: %s", e)
        return False


def _attachment_string(att_type: str, item: dict) -> str:
    owner_id = item["owner_id"]
    item_id = item["id"]
    access_key = item.get("access_key")
    base = f"{att_type}{owner_id}_{item_id}"
    if access_key:
        return f"{base}_{access_key}"
    return base


def _largest_photo_url(photo: dict) -> str | None:
    sizes = photo.get("sizes") or []
    if not sizes:
        return None
    best = max(sizes, key=lambda s: s.get("width", 0) * s.get("height", 0))
    return best.get("url")


def _is_user_owned_attachment(attachment: str) -> bool:
    for prefix in ("photo", "video", "doc"):
        if attachment.startswith(prefix):
            owner_part = attachment[len(prefix) :].split("_", 1)[0]
            try:
                return int(owner_part) > 0
            except ValueError:
                return False
    return False


def _attachment_object_key(att: dict) -> str | None:
    att_type = att.get("type")
    if not att_type:
        return None
    obj = att.get(att_type)
    if not obj:
        return None
    return f"{att_type}{obj.get('owner_id')}_{obj.get('id')}"


def _ensure_photo_sizes(photo: dict) -> dict:
    if _largest_photo_url(photo):
        return photo
    api = get_api()
    if not api:
        return photo
    try:
        items = api.photos.getById(photos=_attachment_string("photo", photo))
        if items:
            return items[0]
    except Exception as e:
        logger.warning("photos.getById failed: %s", e)
    return photo


def _collect_message_attachment_objects(message: dict) -> list[dict]:
    """Collect all attachments; Long Poll may crop albums to a single photo."""
    seen: set[str] = set()
    collected: list[dict] = []

    def add_from_msg(msg: dict | None):
        if not msg:
            return
        for att in msg.get("attachments") or []:
            key = _attachment_object_key(att)
            if key and key not in seen:
                seen.add(key)
                collected.append(att)

    add_from_msg(message)

    attachments = message.get("attachments") or []
    has_photos = any(att.get("type") == "photo" for att in attachments)
    should_load_full = bool(message.get("is_cropped")) or has_photos
    cmid = message.get("conversation_message_id")
    peer_id = message.get("peer_id")
    if should_load_full and cmid and peer_id:
        api = get_api()
        if api:
            try:
                resp = api.messages.getByConversationMessageId(
                    peer_id=peer_id,
                    conversation_message_ids=cmid,
                )
                for item in resp.get("items") or []:
                    add_from_msg(item)
                    for fwd in item.get("fwd_messages") or []:
                        add_from_msg(fwd)
            except Exception as e:
                logger.warning(
                    "getByConversationMessageId failed peer_id=%s cmid=%s: %s",
                    peer_id,
                    cmid,
                    e,
                )

    return collected


def _reupload_photo_dict(photo: dict, peer_id: int | None = None) -> str:
    """Download user photo and upload it as a community message attachment."""
    photo = _ensure_photo_sizes(photo)
    fallback = _attachment_string("photo", photo)
    session = get_session()
    if not session:
        return fallback
    url = _largest_photo_url(photo)
    if not url:
        return fallback
    upload = VkUpload(session)
    path = None
    try:
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
            tmp.write(resp.content)
            path = tmp.name
        saved = upload.photo_messages(path, peer_id=peer_id)
        item = saved[0] if isinstance(saved, list) else saved
        return _attachment_string("photo", item)
    except Exception as e:
        logger.warning("Photo reupload failed: %s", e)
        return fallback
    finally:
        if path:
            try:
                os.unlink(path)
            except OSError:
                pass


def _reupload_photos_batch(photos: list[dict], peer_id: int | None = None) -> list[str]:
    if not photos:
        return []
    if len(photos) == 1:
        return [_reupload_photo_dict(photos[0], peer_id=peer_id)]

    session = get_session()
    if not session:
        return [_attachment_string("photo", _ensure_photo_sizes(p)) for p in photos]

    upload = VkUpload(session)
    paths: list[tuple[int, str]] = []
    results: list[str | None] = [None] * len(photos)

    try:
        for i, raw_photo in enumerate(photos):
            photo = _ensure_photo_sizes(raw_photo)
            url = _largest_photo_url(photo)
            if not url:
                results[i] = _attachment_string("photo", photo)
                continue
            try:
                resp = requests.get(url, timeout=30)
                resp.raise_for_status()
                with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
                    tmp.write(resp.content)
                    paths.append((i, tmp.name))
            except Exception as e:
                logger.warning("Photo download failed: %s", e)
                results[i] = _attachment_string("photo", photo)

        if paths:
            saved = upload.photo_messages([path for _, path in paths], peer_id=peer_id)
            if not isinstance(saved, list):
                saved = [saved]
            for (idx, _), item in zip(paths, saved):
                results[idx] = _attachment_string("photo", item)

        return [value for value in results if value]
    finally:
        for _, path in paths:
            try:
                os.unlink(path)
            except OSError:
                pass


def reupload_attachment_for_community(attachment: str, peer_id: int | None = None) -> str | None:
    """Ensure attachment can be sent by the community (re-upload user photos)."""
    if not attachment:
        return None
    if not attachment.startswith("photo") or not _is_user_owned_attachment(attachment):
        return attachment

    api = get_api()
    if not api:
        return attachment
    try:
        items = api.photos.getById(photos=attachment)
        if not items:
            return attachment
        photo = items[0]
        if photo.get("owner_id", 0) < 0:
            return attachment
        return _reupload_photo_dict(photo, peer_id=peer_id) or attachment
    except Exception as e:
        logger.warning("Cannot reupload attachment %s: %s", attachment, e)
        return attachment


def prepare_outgoing_attachments(message: dict, peer_id: int | None = None) -> list[str]:
    """Parse attachments and re-upload photos so the community can forward them."""
    attachment_objects = _collect_message_attachment_objects(message)
    photos: list[dict] = []
    photo_slots: list[int] = []
    result: list[str | None] = []

    for att in attachment_objects:
        att_type = att.get("type")
        if att_type == "photo" and att.get("photo"):
            photos.append(att["photo"])
            photo_slots.append(len(result))
            result.append(None)
        elif att_type == "video" and att.get("video"):
            result.append(_attachment_string("video", att["video"]))
        elif att_type == "doc" and att.get("doc"):
            result.append(_attachment_string("doc", att["doc"]))

    if photos:
        uploaded = _reupload_photos_batch(photos, peer_id=peer_id)
        for slot, item in zip(photo_slots, uploaded):
            result[slot] = item

    return [item for item in result if item]


def parse_message_attachments(message: dict) -> list[str]:
    """Build VK attachment strings from message.attachments (no re-upload)."""
    result = []
    for att in _collect_message_attachment_objects(message):
        att_type = att.get("type")
        if att_type == "photo" and att.get("photo"):
            result.append(_attachment_string("photo", att["photo"]))
        elif att_type == "video" and att.get("video"):
            result.append(_attachment_string("video", att["video"]))
        elif att_type == "doc" and att.get("doc"):
            result.append(_attachment_string("doc", att["doc"]))
    return result


def keyboard_to_json(keyboard: VkKeyboard) -> str:
    return keyboard.get_keyboard()
