
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.core.database import SessionLocal
from app.features.auth.dependencies import get_current_user, require_active
from app.core.database import get_db
from tests.fixtures.dummy_user import DUMMY_EMAIL, DUMMY_SUB



DUMMY_USER_ADMIN = {
    "sub":DUMMY_SUB,
    "username":"testuser",
    "email":DUMMY_EMAIL,
    "roles":["Admin"],
    "realm":"attendance-app"
}


DUMMY_USER_TR = {
    "sub":DUMMY_SUB,
    "username":"testuser",
    "email":DUMMY_EMAIL,
    "roles": ["Technical Resource"],
    "realm":"attendance-app",
    
}


def _override_get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
    


async def _override_require_active():
    return None



@pytest.fixture(scope="function")
def db_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
    

@pytest.fixture()
def client_admin():
    app.dependency_overrides[get_db] = _override_get_db
