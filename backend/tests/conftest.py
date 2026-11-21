"""Test fixtures. ALL data in tests is synthetic (example.com style handles, fabricated addresses/hashes);
no real person, wallet or account is represented. No test touches the network (providers get mocked clients)."""

from __future__ import annotations

import os
import tempfile

_TMP = tempfile.mkdtemp(prefix="osint-test-")
os.environ["OSINT_DATABASE_URL"] = "sqlite://"
os.environ["OSINT_ARTIFACT_DIR"] = os.path.join(_TMP, "artifacts")
os.environ["OSINT_ADMIN_PASSWORD"] = "test-admin-password"
os.environ["OSINT_JWT_SECRET"] = "test-secret-test-secret-test-secret-123456"
os.environ["OSINT_MAX_DISCOVERY_DEPTH"] = "2"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app import artifacts  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import Base, get_engine, get_session_factory  # noqa: E402
from app.providers import registry  # noqa: E402
from app.providers.registry import sync_providers  # noqa: E402

# Synthetic identifiers (valid format, obviously fake).
EVM_A = "0x" + "11" * 20
EVM_B = "0x" + "22" * 20
EVM_TOKEN = "0x" + "33" * 20
EVM_TX = "0x" + "ab" * 32
SOL_ADDR = "11111111111111111111111111111112"  # valid base58, 32 bytes (System-program-like, synthetic use)


@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    get_settings.cache_clear()
    os.environ["OSINT_ARTIFACT_DIR"] = str(tmp_path / "artifacts")
    get_settings.cache_clear()
    artifacts.set_store(None)
    engine = get_engine()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    registry.reset_registry()
    yield
    Base.metadata.drop_all(engine)
    artifacts.set_store(None)


@pytest.fixture
def session() -> Session:
    with get_session_factory()() as s:
        sync_providers(s)
        s.commit()
        yield s
        s.rollback()


@pytest.fixture
def client() -> TestClient:
    from app.main import app, init_runtime

    init_runtime()
    with TestClient(app) as c:
        yield c


@pytest.fixture
def admin_headers(client: TestClient) -> dict:
    r = client.post("/api/v1/auth/login", json={"username": "admin", "password": "test-admin-password"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def make_user(role: str, username: str | None = None) -> dict:
    """Create a user directly and return auth headers."""
    from app.models import User
    from app.security import create_token, hash_password

    with get_session_factory()() as s:
        u = User(username=username or f"user-{role}", password_hash=hash_password("pw-pw-pw-pw"), role=role)
        s.add(u)
        s.commit()
        return {"Authorization": f"Bearer {create_token(u)}"}
