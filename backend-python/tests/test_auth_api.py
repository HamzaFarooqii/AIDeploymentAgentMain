"""
API tests for authentication routes (register, login, me, verify).

Uses a dedicated throwaway MongoDB database ("devops_autopilot_test") so
these tests never touch the real "devops_autopilot" database used by local
development. DATABASE_NAME must be set BEFORE "app.config.settings" (and
therefore "app.main") is imported anywhere, since pydantic-settings reads
env vars once, at instantiation time.

Fixtures are defined locally in this file (not in conftest.py) per this
suite's isolation requirements.
"""

import os
import sys
import shutil
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Must happen before any "app.*" import.
os.environ["DATABASE_NAME"] = "devops_autopilot_test"

import pytest
from fastapi.testclient import TestClient
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError
from unittest.mock import patch

from app.main import app
from app.config.settings import settings
import app.controllers.auth_controller as auth_controller_module

TEST_DB_NAME = "devops_autopilot_test"
assert settings.DATABASE_NAME == TEST_DB_NAME, (
    "Refusing to run: settings.DATABASE_NAME is not the throwaway test "
    f"database (got {settings.DATABASE_NAME!r}). Aborting to avoid writing "
    "test data into a real database."
)

_created_usernames = []


def _unique_username(prefix="user"):
    name = f"{prefix}_{uuid.uuid4().hex[:10]}"
    _created_usernames.append(name)
    return name


def _register_payload(**overrides):
    username = _unique_username()
    payload = {
        "username": username,
        "email": f"{username}@example.com",
        "password": "SecurePass123",
        "full_name": "Test User",
    }
    payload.update(overrides)
    return payload


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module", autouse=True)
def _cleanup_after_module():
    """Drop the throwaway test database and remove any workspace folders
    created by registration, once all tests in this module have run."""
    yield

    sync_client = MongoClient(settings.MONGODB_URL)
    try:
        sync_client.drop_database(TEST_DB_NAME)
    finally:
        sync_client.close()

    for username in _created_usernames:
        workspace = os.path.join("uploads", f"user_{username}")
        shutil.rmtree(workspace, ignore_errors=True)


class TestRegister:
    def test_register_success(self, client):
        payload = _register_payload()
        resp = client.post("/api/auth/register", json=payload)
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["user"]["username"] == payload["username"]
        assert body["user"]["email"] == payload["email"]
        assert body["user"]["full_name"] == payload["full_name"]
        assert "user_id" in body["user"] and body["user"]["user_id"]

    def test_register_duplicate_username_returns_400(self, client):
        """The common-case duplicate (pre-insert find_one check catches it
        before any insert is attempted) returns 400, not 409 - the 409 path
        is specifically for the DuplicateKeyError race, covered below."""
        payload = _register_payload()
        r1 = client.post("/api/auth/register", json=payload)
        assert r1.status_code == 200

        dup_payload = _register_payload(username=payload["username"])
        r2 = client.post("/api/auth/register", json=dup_payload)
        assert r2.status_code == 400
        assert "username" in r2.json()["message"].lower()

    def test_register_duplicate_email_returns_400(self, client):
        payload = _register_payload()
        r1 = client.post("/api/auth/register", json=payload)
        assert r1.status_code == 200

        dup_payload = _register_payload(email=payload["email"])
        r2 = client.post("/api/auth/register", json=dup_payload)
        assert r2.status_code == 400
        assert "email" in r2.json()["message"].lower()

    def test_register_duplicate_key_race_returns_409(self, client):
        """
        Exercises the DuplicateKeyError -> 409 handling added this session
        (auth_controller.register_user_handler, lines ~51-71). That branch
        only fires when the pre-insert find_one checks pass (e.g. a
        concurrent request already inserted the same username/email between
        the check and this insert) but the unique index still rejects the
        insert. Rather than relying on a flaky real concurrent-request race,
        we deterministically simulate it by swapping in a fake "users"
        collection whose find_one always reports "no conflict" while
        insert_one raises the real pymongo DuplicateKeyError - then assert
        the API surfaces a clean 409, not a generic 500.
        """
        payload = _register_payload()

        class _RacyUsersCollection:
            async def find_one(self, *args, **kwargs):
                return None  # simulate having raced past the pre-checks

            async def insert_one(self, doc):
                raise DuplicateKeyError(
                    "E11000 duplicate key error collection: "
                    f"{TEST_DB_NAME}.users index: uniq_username dup key: "
                    f"{{ username: \"{doc['username']}\" }}",
                    11000,
                    {
                        "keyPattern": {"username": 1},
                        "keyValue": {"username": doc["username"]},
                    },
                )

        with patch.object(
            auth_controller_module.db,
            "get_collection",
            return_value=_RacyUsersCollection(),
        ):
            resp = client.post("/api/auth/register", json=payload)

        assert resp.status_code == 409
        assert "already registered" in resp.json()["message"].lower()

    def test_register_invalid_email_format(self, client):
        payload = _register_payload(email="not-an-email")
        resp = client.post("/api/auth/register", json=payload)
        assert resp.status_code == 422

    def test_register_password_too_short(self, client):
        payload = _register_payload(password="abc")
        resp = client.post("/api/auth/register", json=payload)
        assert resp.status_code == 422

    def test_register_username_too_short(self, client):
        payload = _register_payload(username="ab")
        resp = client.post("/api/auth/register", json=payload)
        assert resp.status_code == 422

    def test_register_missing_required_field(self, client):
        payload = _register_payload()
        del payload["password"]
        resp = client.post("/api/auth/register", json=payload)
        assert resp.status_code == 422


class TestLogin:
    def test_login_success(self, client):
        payload = _register_payload()
        assert client.post("/api/auth/register", json=payload).status_code == 200

        resp = client.post(
            "/api/auth/login",
            json={"username": payload["username"], "password": payload["password"]},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["access_token"]
        assert body["token_type"] == "bearer"
        assert body["user"]["username"] == payload["username"]

    def test_login_wrong_password(self, client):
        payload = _register_payload()
        assert client.post("/api/auth/register", json=payload).status_code == 200

        resp = client.post(
            "/api/auth/login",
            json={"username": payload["username"], "password": "totally-wrong-pw"},
        )
        # auth_controller.login_user_handler uses 401 for bad credentials.
        assert resp.status_code == 401

    def test_login_nonexistent_user(self, client):
        resp = client.post(
            "/api/auth/login",
            json={"username": _unique_username("ghost"), "password": "whatever123"},
        )
        assert resp.status_code == 401


class TestMe:
    def _get_token(self, client):
        payload = _register_payload()
        client.post("/api/auth/register", json=payload)
        login = client.post(
            "/api/auth/login",
            json={"username": payload["username"], "password": payload["password"]},
        )
        return payload, login.json()["access_token"]

    def test_me_with_valid_token(self, client):
        payload, token = self._get_token(client)
        resp = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["user"]["username"] == payload["username"]
        assert body["user"]["email"] == payload["email"]

    def test_me_without_token(self, client):
        resp = client.get("/api/auth/me")
        # FastAPI's HTTPBearer (default auto_error=True) raises 403 "Not
        # authenticated" when no Authorization header is present at all -
        # 401 is reserved for a header that IS present but fails JWT
        # decoding (see decode_access_token in app/utils/auth.py). Verified
        # directly against fastapi.security.HTTPBearer.__call__ source.
        assert resp.status_code == 403

    def test_me_with_invalid_token(self, client):
        resp = client.get(
            "/api/auth/me", headers={"Authorization": "Bearer not-a-real-token"}
        )
        assert resp.status_code == 401


class TestVerify:
    def _get_token(self, client):
        payload = _register_payload()
        client.post("/api/auth/register", json=payload)
        login = client.post(
            "/api/auth/login",
            json={"username": payload["username"], "password": payload["password"]},
        )
        return payload, login.json()["access_token"]

    def test_verify_valid_token(self, client):
        payload, token = self._get_token(client)
        resp = client.get(
            "/api/auth/verify", headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["username"] == payload["username"]

    def test_verify_invalid_token(self, client):
        resp = client.get(
            "/api/auth/verify",
            headers={"Authorization": "Bearer garbage.token.value"},
        )
        assert resp.status_code == 401

    def test_verify_without_token(self, client):
        resp = client.get("/api/auth/verify")
        assert resp.status_code == 403
