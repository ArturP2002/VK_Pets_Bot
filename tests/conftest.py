import pytest
from peewee import SqliteDatabase

from models import ALL_MODELS


@pytest.fixture(autouse=True)
def memory_db():
    test_db = SqliteDatabase(":memory:")
    test_db.bind(ALL_MODELS)
    test_db.connect()
    test_db.create_tables(ALL_MODELS)
    yield test_db
    test_db.drop_tables(ALL_MODELS)
    test_db.close()
