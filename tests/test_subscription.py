from datetime import datetime

from models import User
from services import subscription_service


def test_trial_and_features(memory_db):
    user = User.create(vk_id=999001, created_at=datetime.utcnow())
    assert subscription_service.start_trial(user)
    assert subscription_service.can_use_feature(user, "planned_consultation")
    assert not subscription_service.can_use_feature(user, "emergency")


def test_effective_plan(memory_db):
    user = User.create(vk_id=999002, created_at=datetime.utcnow(), trial_used=False)
    subscription_service.start_trial(user)
    assert subscription_service.effective_plan(user) == "starter"
