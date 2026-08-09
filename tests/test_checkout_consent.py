from datetime import datetime

import config
from migrations.seed import seed_legal_documents
from models import User
from services import legal_service
from services.bot_docs import resolve_bot_doc_paths


EXPECTED_DOC_TYPES = {"offer", "privacy", "personal_data", "order"}


def test_onboarding_uses_bot_docs_types(memory_db):
    seed_legal_documents()
    keys = legal_service.onboarding_version_keys()
    doc_types = {k.split(":")[0] for k in keys}
    assert doc_types == EXPECTED_DOC_TYPES


def test_checkout_consent_keys_subscription(memory_db):
    seed_legal_documents()
    keys = legal_service.checkout_consent_keys("starter")
    doc_types = {k.split(":")[0] for k in keys}
    assert doc_types == EXPECTED_DOC_TYPES


def test_checkout_consent_keys_one_time(memory_db):
    seed_legal_documents()
    keys = legal_service.checkout_consent_keys("one_time")
    doc_types = {k.split(":")[0] for k in keys}
    assert doc_types == EXPECTED_DOC_TYPES


def test_save_checkout_consent(memory_db):
    seed_legal_documents()
    user = User.create(vk_id=1001, created_at=datetime.utcnow())
    keys = legal_service.checkout_consent_keys("basic")
    record = legal_service.save_checkout_consent(user, "basic", 3, 8099, keys)
    assert record.tariff == "basic"
    assert record.amount == config.TARIFF_PRICES["basic"][3]


def test_resolve_bot_doc_paths():
    paths = resolve_bot_doc_paths()
    assert set(paths) == EXPECTED_DOC_TYPES
    for path in paths.values():
        assert path.exists()
        assert path.suffix.lower() == ".docx"
    assert paths["order"].name.casefold().startswith("приказ")
