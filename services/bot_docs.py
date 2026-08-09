"""Resolve and upload legal documents from BotDocs."""
from __future__ import annotations

import logging
from pathlib import Path

import config
from integrations import vk

logger = logging.getLogger(__name__)


def resolve_bot_doc_paths() -> dict[str, Path]:
    """Map doc_type → file path by matching substrings in BotDocs filenames."""
    docs_dir = config.BOT_DOCS_DIR
    if not docs_dir.is_dir():
        logger.warning("BotDocs directory missing: %s", docs_dir)
        return {}

    files = [p for p in docs_dir.iterdir() if p.is_file() and not p.name.startswith(".")]
    result: dict[str, Path] = {}
    for doc_type, needle in config.LEGAL_BOT_DOC_MATCHERS.items():
        needle_l = needle.casefold()
        match = next((p for p in files if needle_l in p.name.casefold()), None)
        if match:
            result[doc_type] = match
        else:
            logger.warning("No BotDocs file matched for %s (%r)", doc_type, needle)
    return result


def bot_doc_titles() -> dict[str, str]:
    return {doc_type: title for doc_type, title in config.LEGAL_DOC_TYPES}


def upload_legal_attachments(peer_id: int, doc_types: list[str] | None = None) -> list[str]:
    """Upload BotDocs files for the given types (or all known) and return VK attachments."""
    import time

    paths = resolve_bot_doc_paths()
    titles = bot_doc_titles()
    order = doc_types or [doc_type for doc_type, _ in config.LEGAL_DOC_TYPES]
    attachments: list[str] = []
    for i, doc_type in enumerate(order):
        path = paths.get(doc_type)
        if not path:
            continue
        if i:
            time.sleep(0.4)
        att = vk.upload_document_message(
            str(path),
            peer_id=peer_id,
            title=titles.get(doc_type) or path.name,
        )
        if att:
            attachments.append(att)
        else:
            logger.warning("Skipped legal attachment %s — upload failed", doc_type)
    return attachments
