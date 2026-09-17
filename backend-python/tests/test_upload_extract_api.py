"""
API tests for upload & extraction routes: POST /api/upload/, GET
/api/upload/projects, GET/DELETE /api/upload/projects/{id}, POST
/api/extract/{id}, GET /api/extract/{id}/files, GET /api/extract/{id}/status,
DELETE /api/extract/{id}/cleanup.

Uses a dedicated throwaway MongoDB database ("devops_autopilot_test") so
these tests never touch the real "devops_autopilot" database used by local
development, following the exact isolation pattern established in
test_auth_api.py. DATABASE_NAME must be set BEFORE "app.config.settings"
(and therefore "app.main") is imported anywhere, since pydantic-settings
reads env vars once, at instantiation time.

Fixtures are defined locally in this file (not in conftest.py) per this
suite's isolation requirements. Any files this suite writes under
uploads/user_<username>/... (uploaded zip files plus extracted/project-<id>
trees created underneath that same per-user workspace folder - see
upload_controller.upload_file_handler and extract_controller's use of
f"uploads/user_{username}" as the extraction root) are removed in the
module-level teardown fixture, keyed off the usernames this suite itself
registered - mirroring test_auth_api.py's cleanup.

Two real-behavior notes worth flagging up front (verified by reading
upload_controller.py / extract_controller.py before writing these tests):

- upload_controller.get_project_by_id and delete_project query Mongo with
  {"_id": ..., "user_id": str(current_user["_id"])} combined in a single
  find_one - so a non-owner querying/deleting another user's project gets
  the *same* 404 "Project not found or unauthorized" as a truly nonexistent
  id (it never leaks existence to a non-owner via a 403). This differs from
  extract_controller's handlers (extract/files/status/cleanup), which fetch
  by _id alone and then do an explicit ownership check that returns a
  distinct 403 "Access denied: Not project owner" for a real non-owner vs.
  404 for a truly missing project. Both patterns are tested here as they
  actually behave, not assumed.
- All protected routes use FastAPI's HTTPBearer (auto_error=True) via
  get_current_active_user. A request with NO Authorization header at all
  never reaches the handler and gets 403 "Not authenticated" from HTTPBearer
  itself - not 401. (401 is reserved for a header that IS present but fails
  JWT decoding - see decode_access_token in app/utils/auth.py, and
  test_auth_api.py's TestMe.test_me_without_token which documents the same
  thing.) The "without auth" tests below assert 403 accordingly.
"""

import io
import os
import sys
import shutil
import uuid
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Must happen before any "app.*" import.
os.environ["DATABASE_NAME"] = "devops_autopilot_test"

import pytest
from fastapi.testclient import TestClient
from pymongo import MongoClient

from app.main import app
from app.config.settings import settings

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


def _make_zip_bytes(files=None):
    """Build a small, real zip archive in memory. `files` maps archive path
    -> bytes content; defaults to a top-level package.json plus a nested
    src/app.py so extraction produces a deterministic files_count=2,
    folders_count=1."""
    if files is None:
        files = {
            "package.json": b'{"name": "test-app", "version": "1.0.0"}',
            "src/app.py": b"print('hello world')\n",
        }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module", autouse=True)
def _cleanup_after_module():
    """Drop the throwaway test database and remove any per-user workspace
    folders (uploaded zips + extracted/project-<id> trees) created by this
    suite, once all tests in this module have run."""
    yield

    sync_client = MongoClient(settings.MONGODB_URL)
    try:
        sync_client.drop_database(TEST_DB_NAME)
    finally:
        sync_client.close()

    for username in _created_usernames:
        workspace = os.path.join("uploads", f"user_{username}")
        shutil.rmtree(workspace, ignore_errors=True)


def _register_and_login(client, **overrides):
    payload = _register_payload(**overrides)
    r = client.post("/api/auth/register", json=payload)
    assert r.status_code == 200, r.text
    login = client.post(
        "/api/auth/login",
        json={"username": payload["username"], "password": payload["password"]},
    )
    assert login.status_code == 200, login.text
    token = login.json()["access_token"]
    return payload, token


def _auth_header(token):
    return {"Authorization": f"Bearer {token}"}


def _upload_project(client, token, files=None, project_name=None, zip_filename="project.zip"):
    zip_bytes = _make_zip_bytes(files)
    data = {}
    if project_name is not None:
        data["project_name"] = project_name
    resp = client.post(
        "/api/upload/",
        headers=_auth_header(token),
        files={"file": (zip_filename, zip_bytes, "application/zip")},
        data=data,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["project_id"]


class TestUpload:
    def test_upload_zip_success(self, client):
        _, token = _register_and_login(client)
        zip_bytes = _make_zip_bytes()

        resp = client.post(
            "/api/upload/",
            headers=_auth_header(token),
            files={"file": ("myproject.zip", zip_bytes, "application/zip")},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        data = body["data"]
        assert data["project_id"]
        assert data["file_name"] == "myproject.zip"
        assert data["project_name"] == "myproject"  # derived from filename
        assert data["file_size"]  # formatted human-readable string
        assert data["upload_date"]

    def test_upload_creates_project_with_uploaded_status(self, client):
        _, token = _register_and_login(client)
        project_id = _upload_project(client, token)

        resp = client.get(
            f"/api/upload/projects/{project_id}", headers=_auth_header(token)
        )
        assert resp.status_code == 200
        project = resp.json()["project"]
        assert project["status"] == "uploaded"
        assert project["file_name"] == "project.zip"
        assert project["files_count"] == 0
        assert project["extracted_path"] is None

    def test_upload_with_custom_project_name(self, client):
        _, token = _register_and_login(client)
        project_id = _upload_project(client, token, project_name="Custom Name")

        resp = client.get(
            f"/api/upload/projects/{project_id}", headers=_auth_header(token)
        )
        assert resp.status_code == 200
        assert resp.json()["project"]["project_name"] == "Custom Name"

    def test_upload_invalid_file_type_rejected(self, client):
        """upload_controller.upload_file_handler only allows
        .zip/.tar/.gz/.tgz - everything else is a 400 before any file is
        written to disk."""
        _, token = _register_and_login(client)

        resp = client.post(
            "/api/upload/",
            headers=_auth_header(token),
            files={"file": ("notes.txt", b"just plain text", "text/plain")},
        )
        assert resp.status_code == 400
        assert "invalid file type" in resp.json()["message"].lower()

    def test_upload_without_auth(self, client):
        zip_bytes = _make_zip_bytes()
        resp = client.post(
            "/api/upload/", files={"file": ("x.zip", zip_bytes, "application/zip")}
        )
        assert resp.status_code == 403


class TestListProjects:
    def test_list_projects_only_returns_own(self, client):
        _, token_a = _register_and_login(client)
        _, token_b = _register_and_login(client)

        project_a1 = _upload_project(client, token_a, zip_filename="a1.zip")
        project_a2 = _upload_project(client, token_a, zip_filename="a2.zip")
        project_b1 = _upload_project(client, token_b, zip_filename="b1.zip")

        resp_a = client.get("/api/upload/projects", headers=_auth_header(token_a))
        assert resp_a.status_code == 200
        body_a = resp_a.json()
        assert body_a["success"] is True
        ids_a = {p["_id"] for p in body_a["projects"]}
        assert {project_a1, project_a2}.issubset(ids_a)
        assert project_b1 not in ids_a
        assert body_a["count"] == len(body_a["projects"])

        resp_b = client.get("/api/upload/projects", headers=_auth_header(token_b))
        ids_b = {p["_id"] for p in resp_b.json()["projects"]}
        assert project_b1 in ids_b
        assert project_a1 not in ids_b
        assert project_a2 not in ids_b

    def test_list_projects_without_auth(self, client):
        resp = client.get("/api/upload/projects")
        assert resp.status_code == 403


class TestGetProject:
    def test_get_project_as_owner(self, client):
        _, token = _register_and_login(client)
        project_id = _upload_project(client, token)

        resp = client.get(
            f"/api/upload/projects/{project_id}", headers=_auth_header(token)
        )
        assert resp.status_code == 200
        assert resp.json()["project"]["_id"] == project_id

    def test_get_project_as_non_owner_returns_404(self, client):
        """See module docstring: get_project_by_id filters by user_id in the
        same query, so a non-owner gets the same 404 as a nonexistent id -
        never a 403 that would confirm the project exists."""
        _, token_owner = _register_and_login(client)
        _, token_other = _register_and_login(client)
        project_id = _upload_project(client, token_owner)

        resp = client.get(
            f"/api/upload/projects/{project_id}", headers=_auth_header(token_other)
        )
        assert resp.status_code == 404
        assert "not found" in resp.json()["message"].lower()

    def test_get_project_nonexistent_id_returns_404(self, client):
        _, token = _register_and_login(client)
        fake_id = "5f9f1b9b9b9b9b9b9b9b9b9b"  # well-formed ObjectId, no doc
        resp = client.get(
            f"/api/upload/projects/{fake_id}", headers=_auth_header(token)
        )
        assert resp.status_code == 404

    def test_get_project_invalid_id_format_returns_400(self, client):
        _, token = _register_and_login(client)
        resp = client.get(
            "/api/upload/projects/not-a-valid-object-id", headers=_auth_header(token)
        )
        assert resp.status_code == 400

    def test_get_project_without_auth(self, client):
        resp = client.get("/api/upload/projects/5f9f1b9b9b9b9b9b9b9b9b9b")
        assert resp.status_code == 403


class TestDeleteProject:
    def test_delete_project_as_owner(self, client):
        _, token = _register_and_login(client)
        project_id = _upload_project(client, token)

        resp = client.delete(
            f"/api/upload/projects/{project_id}", headers=_auth_header(token)
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is True

        follow_up = client.get(
            f"/api/upload/projects/{project_id}", headers=_auth_header(token)
        )
        assert follow_up.status_code == 404

    def test_delete_project_as_non_owner_returns_404(self, client):
        """Same combined-query pattern as get_project_by_id (see module
        docstring) - a non-owner's delete attempt looks identical to
        deleting a nonexistent id."""
        _, token_owner = _register_and_login(client)
        _, token_other = _register_and_login(client)
        project_id = _upload_project(client, token_owner)

        resp = client.delete(
            f"/api/upload/projects/{project_id}", headers=_auth_header(token_other)
        )
        assert resp.status_code == 404

        # Confirm it was NOT actually deleted by the non-owner's attempt.
        still_there = client.get(
            f"/api/upload/projects/{project_id}", headers=_auth_header(token_owner)
        )
        assert still_there.status_code == 200

    def test_delete_nonexistent_project_returns_404(self, client):
        _, token = _register_and_login(client)
        resp = client.delete(
            "/api/upload/projects/5f9f1b9b9b9b9b9b9b9b9b9b",
            headers=_auth_header(token),
        )
        assert resp.status_code == 404

    def test_delete_project_without_auth(self, client):
        resp = client.delete("/api/upload/projects/5f9f1b9b9b9b9b9b9b9b9b9b")
        assert resp.status_code == 403


class TestExtract:
    def test_extract_project_success(self, client):
        _, token = _register_and_login(client)
        project_id = _upload_project(client, token)

        resp = client.post(
            f"/api/extract/{project_id}", headers=_auth_header(token)
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        data = body["data"]
        assert data["files_count"] == 2  # package.json + src/app.py
        assert data["folders_count"] == 1  # src
        assert data["extracted_path"]

        status_resp = client.get(
            f"/api/upload/projects/{project_id}", headers=_auth_header(token)
        )
        assert status_resp.json()["project"]["status"] == "extracted"

    def test_extract_already_extracted_returns_success_false(self, client):
        _, token = _register_and_login(client)
        project_id = _upload_project(client, token)

        first = client.post(f"/api/extract/{project_id}", headers=_auth_header(token))
        assert first.status_code == 200
        assert first.json()["success"] is True

        second = client.post(f"/api/extract/{project_id}", headers=_auth_header(token))
        assert second.status_code == 200
        body = second.json()
        assert body["success"] is False
        assert "already extracted" in body["message"].lower()

    def test_extract_nonexistent_project_returns_404(self, client):
        _, token = _register_and_login(client)
        resp = client.post(
            "/api/extract/5f9f1b9b9b9b9b9b9b9b9b9b", headers=_auth_header(token)
        )
        assert resp.status_code == 404

    def test_extract_as_non_owner_returns_403(self, client):
        """Unlike upload_controller's get/delete, extract_controller fetches
        by _id alone and does an explicit ownership check - so a real
        non-owner gets a distinct 403, not 404 (see module docstring)."""
        _, token_owner = _register_and_login(client)
        _, token_other = _register_and_login(client)
        project_id = _upload_project(client, token_owner)

        resp = client.post(
            f"/api/extract/{project_id}", headers=_auth_header(token_other)
        )
        assert resp.status_code == 403
        assert "not project owner" in resp.json()["message"].lower()

    def test_extract_without_auth(self, client):
        resp = client.post("/api/extract/5f9f1b9b9b9b9b9b9b9b9b9b")
        assert resp.status_code == 403


class TestExtractedFiles:
    def test_get_files_before_extraction(self, client):
        _, token = _register_and_login(client)
        project_id = _upload_project(client, token)

        resp = client.get(
            f"/api/extract/{project_id}/files", headers=_auth_header(token)
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is False
        assert body["current_status"] == "uploaded"

    def test_get_files_after_extraction(self, client):
        _, token = _register_and_login(client)
        project_id = _upload_project(client, token)
        assert client.post(
            f"/api/extract/{project_id}", headers=_auth_header(token)
        ).status_code == 200

        resp = client.get(
            f"/api/extract/{project_id}/files", headers=_auth_header(token)
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["total_files"] == 2
        assert body["total_folders"] == 1
        names = {f["name"] for f in body["files"]}
        assert "package.json" in names
        assert "src" in names
        assert "app.py" in names

    def test_get_files_as_non_owner_returns_403(self, client):
        _, token_owner = _register_and_login(client)
        _, token_other = _register_and_login(client)
        project_id = _upload_project(client, token_owner)
        client.post(f"/api/extract/{project_id}", headers=_auth_header(token_owner))

        resp = client.get(
            f"/api/extract/{project_id}/files", headers=_auth_header(token_other)
        )
        assert resp.status_code == 403

    def test_get_files_without_auth(self, client):
        resp = client.get("/api/extract/5f9f1b9b9b9b9b9b9b9b9b9b/files")
        assert resp.status_code == 403


class TestExtractionStatus:
    def test_status_before_extraction(self, client):
        _, token = _register_and_login(client)
        project_id = _upload_project(client, token)

        resp = client.get(
            f"/api/extract/{project_id}/status", headers=_auth_header(token)
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["status"] == "uploaded"
        assert data["files_count"] == 0
        assert data["extracted_path"] is None
        assert data["extraction_date"] is None

    def test_status_after_extraction(self, client):
        _, token = _register_and_login(client)
        project_id = _upload_project(client, token)
        client.post(f"/api/extract/{project_id}", headers=_auth_header(token))

        resp = client.get(
            f"/api/extract/{project_id}/status", headers=_auth_header(token)
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["status"] == "extracted"
        assert data["files_count"] == 2
        assert data["folders_count"] == 1
        assert data["extracted_path"]
        assert data["extraction_date"]

    def test_status_as_non_owner_returns_403(self, client):
        _, token_owner = _register_and_login(client)
        _, token_other = _register_and_login(client)
        project_id = _upload_project(client, token_owner)

        resp = client.get(
            f"/api/extract/{project_id}/status", headers=_auth_header(token_other)
        )
        assert resp.status_code == 403

    def test_status_without_auth(self, client):
        resp = client.get("/api/extract/5f9f1b9b9b9b9b9b9b9b9b9b/status")
        assert resp.status_code == 403


class TestCleanupExtraction:
    def test_cleanup_after_extraction(self, client):
        _, token = _register_and_login(client)
        project_id = _upload_project(client, token)
        client.post(f"/api/extract/{project_id}", headers=_auth_header(token))

        resp = client.delete(
            f"/api/extract/{project_id}/cleanup", headers=_auth_header(token)
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is True

        status_resp = client.get(
            f"/api/extract/{project_id}/status", headers=_auth_header(token)
        )
        data = status_resp.json()["data"]
        assert data["status"] == "uploaded"
        assert data["files_count"] == 0
        assert data["extracted_path"] is None

    def test_cleanup_when_nothing_extracted_returns_false(self, client):
        _, token = _register_and_login(client)
        project_id = _upload_project(client, token)

        resp = client.delete(
            f"/api/extract/{project_id}/cleanup", headers=_auth_header(token)
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is False
        assert "no extracted files" in body["message"].lower()

    def test_cleanup_as_non_owner_returns_403(self, client):
        _, token_owner = _register_and_login(client)
        _, token_other = _register_and_login(client)
        project_id = _upload_project(client, token_owner)
        client.post(f"/api/extract/{project_id}", headers=_auth_header(token_owner))

        resp = client.delete(
            f"/api/extract/{project_id}/cleanup", headers=_auth_header(token_other)
        )
        assert resp.status_code == 403

    def test_cleanup_without_auth(self, client):
        resp = client.delete("/api/extract/5f9f1b9b9b9b9b9b9b9b9b9b/cleanup")
        assert resp.status_code == 403
