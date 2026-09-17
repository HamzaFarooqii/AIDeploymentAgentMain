"""
Unit / API tests for the live-monitoring stack:

- app.services.monitor_service   (kubectl/terraform-shelling health checks,
  self-heal, log/event fetchers)
- app.controllers.monitor_controller (project ownership lookup + wiring
  between the routes and monitor_service)
- app.routes.monitor (auth enforcement on every /api/monitor/* endpoint)

PRIORITY COVERAGE: this session's commit 0688da7 fixed
monitor_service._resolve_project_infra_dir() / get_aws_health() so the AWS
health check resolves the Terraform working directory from the project's
real `extracted_path` (looked up in Mongo) via app.utils.detector.
find_project_root(), instead of a broken hardcoded "terraform/<project_id>"
path. That fix had zero test coverage before this file - it was only
hand-verified by code inspection. TestResolveProjectInfraDir,
TestGetAwsHealth, and TestGetAwsHealthEndToEnd below exercise it directly.

SAFETY:
- Every `kubectl` / `terraform` invocation in this file is mocked at
  subprocess.Popen/subprocess.run, exactly where monitor_service.py calls
  them (app.services.monitor_service.subprocess.*). No test here ever
  shells out to a real kubectl or terraform binary.
- Every MongoDB read performed by monitor_service._resolve_project_infra_dir
  (a short-lived synchronous pymongo.MongoClient, separate from the app's
  main motor connection) is mocked at app.services.monitor_service.
  MongoClient. It is never given a chance to reach a real Mongo instance.
- Terraform state "file existence" is exercised with a real tempdir this
  test file creates and tears down itself (via tempfile.mkdtemp /
  shutil.rmtree) rather than touching any real project files.
- The controller-level tests (TestMonitorController*) fake out
  get_projects_collection() entirely with an in-memory async stand-in
  (_FakeProjectsCollection), so they never touch Mongo either.
- Only the route-level auth tests (TestMonitorRouteAuthRequired) go through
  the real FastAPI app + a real MongoDB connection (for the app's startup
  lifespan), exactly like tests/test_auth_api.py. DATABASE_NAME is forced to
  the throwaway "devops_autopilot_test" database before app.main is
  imported, and that class is skipped (not failed) if Mongo isn't reachable,
  mirroring TestDeploymentControllerPipeline.setUpClass in
  tests/test_k8s_deploy.py. Every request in that class fails auth before
  the controller/service layer (and therefore before any Mongo/kubectl
  call) is ever reached.
"""

import os

# Must happen before any "app.*" import (including transitively, from other
# test modules already imported in the same pytest session), so the
# TestClient-driven route tests below never connect to the real local
# "devops_autopilot" database.
os.environ["DATABASE_NAME"] = "devops_autopilot_test"

import asyncio
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bson import ObjectId
from fastapi.testclient import TestClient
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import MongoClient

from app.main import app
from app.config.settings import settings
from app.controllers.monitor_controller import MonitorController, monitor_controller
from app.services.monitor_service import (
    MonitorService,
    monitor_service,
    _resolve_project_infra_dir,
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _popen_mock(returncode=0, stdout=b"", stderr=b""):
    """Stand-in for a subprocess.Popen instance (used by get_aws_health,
    which calls `terraform output -json` via Popen)."""
    mock_process = MagicMock()
    mock_process.communicate.return_value = (stdout, stderr)
    mock_process.returncode = returncode
    return mock_process


def _run_mock(returncode=0, stdout=b"", stderr=b""):
    """Stand-in for a subprocess.run() CompletedProcess (used by every
    kubectl-shelling function in monitor_service.py except get_aws_health)."""
    result = MagicMock()
    result.returncode = returncode
    result.stdout = stdout
    result.stderr = stderr
    return result


def _mock_mongo_project_lookup(mock_mongo_client_cls, project_doc):
    """Wires a @patch("app.services.monitor_service.MongoClient") mock so
    that `client[db][collection].find_one(...)` returns `project_doc`,
    mirroring the real chain _resolve_project_infra_dir() performs."""
    mock_collection = MagicMock()
    mock_collection.find_one.return_value = project_doc
    mock_db = MagicMock()
    mock_db.__getitem__.return_value = mock_collection
    mock_client_instance = MagicMock()
    mock_client_instance.__getitem__.return_value = mock_db
    mock_mongo_client_cls.return_value = mock_client_instance
    return mock_client_instance, mock_collection


class _FakeProjectsCollection:
    """Minimal stand-in for the async Mongo `projects` collection, used to
    unit-test MonitorController without touching any real database. Mirrors
    real find_one() filtering on both _id and user_id so ownership checks
    behave the same way they would against a real collection."""

    def __init__(self, doc=None):
        self._doc = doc

    async def find_one(self, query):
        if not self._doc:
            return None
        if "_id" in query and query["_id"] != self._doc.get("_id"):
            return None
        if "user_id" in query and query["user_id"] != self._doc.get("user_id"):
            return None
        return self._doc


def _patch_projects_collection(doc):
    return patch(
        "app.controllers.monitor_controller.get_projects_collection",
        return_value=_FakeProjectsCollection(doc),
    )


# ---------------------------------------------------------------------------
# PRIORITY: _resolve_project_infra_dir() - the AWS health-check path fix
# ---------------------------------------------------------------------------


class TestResolveProjectInfraDir(unittest.TestCase):
    """
    _resolve_project_infra_dir() must:
      1. Reject invalid project_ids without ever touching Mongo.
      2. Look the project up in Mongo and read its `extracted_path`.
      3. Resolve the real project root via app.utils.detector.
         find_project_root(os.path.abspath(extracted_path)).
      4. Return "<project_root>/infra" - NOT a hardcoded
         "terraform/<project_id>" path relative to the CWD.
    """

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_invalid_project_id_returns_none_without_touching_mongo(self):
        with patch("app.services.monitor_service.MongoClient") as mock_cls:
            result = _resolve_project_infra_dir("not-a-valid-object-id")

        self.assertIsNone(result)
        mock_cls.assert_not_called()

    @patch("app.services.monitor_service.MongoClient")
    def test_project_not_found_returns_none(self, mock_cls):
        _mock_mongo_project_lookup(mock_cls, None)

        result = _resolve_project_infra_dir(str(ObjectId()))

        self.assertIsNone(result)

    @patch("app.services.monitor_service.MongoClient")
    def test_missing_extracted_path_returns_none(self, mock_cls):
        _mock_mongo_project_lookup(mock_cls, {"_id": ObjectId()})  # no extracted_path key

        result = _resolve_project_infra_dir(str(ObjectId()))

        self.assertIsNone(result)

    @patch("app.services.monitor_service.MongoClient")
    def test_extracted_path_not_on_disk_returns_none(self, mock_cls):
        missing_path = os.path.join(self.temp_dir, "does-not-exist")
        _mock_mongo_project_lookup(mock_cls, {"_id": ObjectId(), "extracted_path": missing_path})

        result = _resolve_project_infra_dir(str(ObjectId()))

        self.assertIsNone(result)

    @patch("app.services.monitor_service.find_project_root")
    @patch("app.services.monitor_service.MongoClient")
    def test_resolves_infra_dir_from_extracted_path_via_find_project_root(
        self, mock_cls, mock_find_root
    ):
        project_root = os.path.join(self.temp_dir, "my-repo")
        nested_extracted = os.path.join(project_root, "backend", "src")
        os.makedirs(nested_extracted, exist_ok=True)
        mock_find_root.return_value = project_root

        _mock_mongo_project_lookup(
            mock_cls, {"_id": ObjectId(), "extracted_path": nested_extracted}
        )

        result = _resolve_project_infra_dir(str(ObjectId()))

        self.assertEqual(result, os.path.join(project_root, "infra"))
        mock_find_root.assert_called_once_with(os.path.abspath(nested_extracted))

    @patch("app.services.monitor_service.MongoClient")
    def test_mongo_connection_exception_returns_none(self, mock_cls):
        mock_cls.side_effect = Exception("connection refused")

        result = _resolve_project_infra_dir(str(ObjectId()))

        self.assertIsNone(result)

    @patch("app.services.monitor_service.MongoClient")
    def test_mongo_client_is_always_closed(self, mock_cls):
        client_instance, _ = _mock_mongo_project_lookup(mock_cls, None)

        _resolve_project_infra_dir(str(ObjectId()))

        client_instance.close.assert_called_once()


class TestGetAwsHealth(unittest.TestCase):
    """
    monitor_service.get_aws_health(): not_deployed / deployed / unknown /
    error branches, with _resolve_project_infra_dir mocked directly (its own
    behavior is already covered in isolation above) so these tests focus
    purely on the tfstate-file-exists check and the `terraform output -json`
    branches.
    """

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    @patch("app.services.monitor_service._resolve_project_infra_dir")
    def test_not_deployed_when_infra_dir_cannot_be_resolved(self, mock_resolve):
        mock_resolve.return_value = None

        result = monitor_service.get_aws_health(str(ObjectId()))

        self.assertEqual(result, {"status": "not_deployed", "healthy": False})

    @patch("app.services.monitor_service._resolve_project_infra_dir")
    def test_not_deployed_when_tfstate_file_absent(self, mock_resolve):
        infra_dir = os.path.join(self.temp_dir, "infra")
        os.makedirs(infra_dir, exist_ok=True)  # infra dir exists, tfstate does not
        mock_resolve.return_value = infra_dir

        result = monitor_service.get_aws_health(str(ObjectId()))

        self.assertEqual(result["status"], "not_deployed")
        self.assertFalse(result["healthy"])

    @patch("app.services.monitor_service.subprocess.Popen")
    @patch("app.services.monitor_service._resolve_project_infra_dir")
    def test_deployed_and_healthy_when_tfstate_exists_and_terraform_succeeds(
        self, mock_resolve, mock_popen
    ):
        infra_dir = os.path.join(self.temp_dir, "infra")
        os.makedirs(infra_dir, exist_ok=True)
        with open(os.path.join(infra_dir, "terraform.tfstate"), "w") as f:
            f.write("{}")
        mock_resolve.return_value = infra_dir
        mock_popen.return_value = _popen_mock(
            returncode=0, stdout=b'{"instance_ip": {"value": "1.2.3.4"}}'
        )

        result = monitor_service.get_aws_health(str(ObjectId()))

        self.assertTrue(result["healthy"])
        self.assertEqual(result["status"], "deployed")
        mock_popen.assert_called_once_with(
            ["terraform", "output", "-json"],
            cwd=infra_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    @patch("app.services.monitor_service.subprocess.Popen")
    @patch("app.services.monitor_service._resolve_project_infra_dir")
    def test_unknown_when_terraform_output_fails(self, mock_resolve, mock_popen):
        infra_dir = os.path.join(self.temp_dir, "infra")
        os.makedirs(infra_dir, exist_ok=True)
        open(os.path.join(infra_dir, "terraform.tfstate"), "w").close()
        mock_resolve.return_value = infra_dir
        mock_popen.return_value = _popen_mock(returncode=1, stderr=b"no outputs defined")

        result = monitor_service.get_aws_health(str(ObjectId()))

        self.assertFalse(result["healthy"])
        self.assertEqual(result["status"], "unknown")

    @patch("app.services.monitor_service.subprocess.Popen")
    @patch("app.services.monitor_service._resolve_project_infra_dir")
    def test_error_when_terraform_invocation_raises(self, mock_resolve, mock_popen):
        infra_dir = os.path.join(self.temp_dir, "infra")
        os.makedirs(infra_dir, exist_ok=True)
        open(os.path.join(infra_dir, "terraform.tfstate"), "w").close()
        mock_resolve.return_value = infra_dir
        mock_popen.side_effect = FileNotFoundError("terraform not found")

        result = monitor_service.get_aws_health(str(ObjectId()))

        self.assertFalse(result["healthy"])
        self.assertEqual(result["status"], "error")
        self.assertIn("terraform not found", result["details"])


class TestGetAwsHealthEndToEnd(unittest.TestCase):
    """
    Full pipeline regression test for the path-resolution bug fix: drives
    get_aws_health() all the way through _resolve_project_infra_dir() ->
    mocked Mongo lookup -> mocked find_project_root() -> a REAL tempdir
    standing in for the filesystem, so the tfstate-exists check (the part
    that was broken before commit 0688da7) is exercised for real rather
    than mocked away.
    """

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    @patch("app.services.monitor_service.subprocess.Popen")
    @patch("app.services.monitor_service.find_project_root")
    @patch("app.services.monitor_service.MongoClient")
    def test_healthy_when_project_root_infra_has_tfstate(
        self, mock_mongo_cls, mock_find_root, mock_popen
    ):
        project_root = os.path.join(self.temp_dir, "repo-root")
        infra_dir = os.path.join(project_root, "infra")
        os.makedirs(infra_dir, exist_ok=True)
        open(os.path.join(infra_dir, "terraform.tfstate"), "w").close()
        extracted_path = os.path.join(project_root, "backend")
        os.makedirs(extracted_path, exist_ok=True)

        mock_find_root.return_value = project_root
        _mock_mongo_project_lookup(
            mock_mongo_cls, {"_id": ObjectId(), "extracted_path": extracted_path}
        )
        mock_popen.return_value = _popen_mock(returncode=0, stdout=b"{}")

        project_id = str(ObjectId())
        result = monitor_service.get_aws_health(project_id)

        self.assertEqual(result["status"], "deployed")
        self.assertTrue(result["healthy"])
        # The actual bug being regression-tested: terraform must be invoked
        # with cwd=<project_root>/infra (derived via find_project_root()),
        # never a hardcoded "terraform/<project_id>" directory relative to
        # the process CWD.
        self.assertEqual(mock_popen.call_args.kwargs["cwd"], infra_dir)

    @patch("app.services.monitor_service.find_project_root")
    @patch("app.services.monitor_service.MongoClient")
    def test_not_deployed_when_project_root_infra_has_no_tfstate(
        self, mock_mongo_cls, mock_find_root
    ):
        project_root = os.path.join(self.temp_dir, "repo-root")
        infra_dir = os.path.join(project_root, "infra")
        os.makedirs(infra_dir, exist_ok=True)  # infra dir exists, but no tfstate written
        extracted_path = os.path.join(project_root, "backend")
        os.makedirs(extracted_path, exist_ok=True)

        mock_find_root.return_value = project_root
        _mock_mongo_project_lookup(
            mock_mongo_cls, {"_id": ObjectId(), "extracted_path": extracted_path}
        )

        result = monitor_service.get_aws_health(str(ObjectId()))

        self.assertEqual(result, {"status": "not_deployed", "healthy": False})

    @patch("app.services.monitor_service.MongoClient")
    def test_not_deployed_when_project_has_no_extracted_path(self, mock_mongo_cls):
        _mock_mongo_project_lookup(mock_mongo_cls, {"_id": ObjectId()})

        result = monitor_service.get_aws_health(str(ObjectId()))

        self.assertEqual(result, {"status": "not_deployed", "healthy": False})


# ---------------------------------------------------------------------------
# monitor_service: Kubernetes-facing functions
# ---------------------------------------------------------------------------


class TestGetK8sHealth(unittest.TestCase):
    def test_empty_deployment_name_short_circuits_without_kubectl(self):
        result = monitor_service.get_k8s_health("")

        self.assertEqual(result, {"status": "not_deployed", "healthy": False})

    @patch("app.services.monitor_service.diagnose_pod_health")
    def test_delegates_to_diagnose_pod_health(self, mock_diagnose):
        mock_diagnose.return_value = {"healthy": True, "state": "Running", "pod_name": "app-abc"}

        result = monitor_service.get_k8s_health("devops-autopilot-app")

        self.assertEqual(result, {"healthy": True, "state": "Running", "pod_name": "app-abc"})
        mock_diagnose.assert_called_once_with("devops-autopilot-app")


class TestTriggerSelfHealing(unittest.TestCase):
    @patch("app.services.monitor_service.subprocess.run")
    def test_successful_restart_targets_correct_deployment(self, mock_run):
        mock_run.return_value = _run_mock(returncode=0)

        result = monitor_service.trigger_self_healing("devops-autopilot-my-app")

        self.assertTrue(result["success"])
        self.assertIn("devops-autopilot-my-app", result["message"])
        mock_run.assert_called_once_with(
            ["kubectl", "rollout", "restart", "deployment/devops-autopilot-my-app"],
            capture_output=True,
            timeout=30,
        )

    @patch("app.services.monitor_service.subprocess.run")
    def test_failed_restart_surfaces_stderr(self, mock_run):
        mock_run.return_value = _run_mock(
            returncode=1, stderr=b'deployments.apps "x" not found'
        )

        result = monitor_service.trigger_self_healing("x")

        self.assertFalse(result["success"])
        self.assertIn("not found", result["message"])

    @patch("app.services.monitor_service.subprocess.run", side_effect=Exception("kubectl unavailable"))
    def test_exception_is_reported_as_failure_not_raised(self, mock_run):
        result = monitor_service.trigger_self_healing("x")

        self.assertFalse(result["success"])
        self.assertIn("kubectl unavailable", result["message"])


class TestGetRecentK8sEvents(unittest.TestCase):
    @patch("app.services.monitor_service.subprocess.run")
    def test_parses_events_and_targets_correct_deployment(self, mock_run):
        events_json = json.dumps(
            {
                "items": [
                    {
                        "type": "Warning",
                        "reason": "BackOff",
                        "message": "back-off restarting failed container",
                        "lastTimestamp": "2026-01-01T00:00:00Z",
                        "count": 3,
                    },
                    {
                        "type": "Normal",
                        "reason": "Scheduled",
                        "message": "assigned to node",
                        "firstTimestamp": "2026-01-01T00:00:01Z",
                        "count": 1,
                    },
                ]
            }
        ).encode("utf-8")
        mock_run.return_value = _run_mock(returncode=0, stdout=events_json)

        result = monitor_service.get_recent_k8s_events("devops-autopilot-my-app", limit=10)

        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["reason"], "BackOff")
        self.assertEqual(result[0]["timestamp"], "2026-01-01T00:00:00Z")
        # falls back to firstTimestamp when lastTimestamp is absent
        self.assertEqual(result[1]["timestamp"], "2026-01-01T00:00:01Z")

        cmd = mock_run.call_args.args[0]
        self.assertIn("--field-selector=involvedObject.name=devops-autopilot-my-app", cmd)

    @patch("app.services.monitor_service.subprocess.run")
    def test_limit_keeps_the_most_recent_items(self, mock_run):
        items = [
            {"type": "Normal", "reason": f"E{i}", "message": "", "lastTimestamp": "", "count": 1}
            for i in range(5)
        ]
        mock_run.return_value = _run_mock(
            returncode=0, stdout=json.dumps({"items": items}).encode("utf-8")
        )

        result = monitor_service.get_recent_k8s_events("app", limit=2)

        self.assertEqual([e["reason"] for e in result], ["E3", "E4"])

    @patch("app.services.monitor_service.subprocess.run")
    def test_non_zero_returncode_returns_empty_list(self, mock_run):
        mock_run.return_value = _run_mock(returncode=1, stderr=b"error")

        result = monitor_service.get_recent_k8s_events("app")

        self.assertEqual(result, [])

    @patch("app.services.monitor_service.subprocess.run", side_effect=Exception("kubectl not found"))
    def test_exception_returns_single_warning_event(self, mock_run):
        result = monitor_service.get_recent_k8s_events("app")

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["type"], "Warning")
        self.assertEqual(result[0]["reason"], "MonitorError")
        self.assertIn("kubectl not found", result[0]["message"])


class TestGetPodLogs(unittest.TestCase):
    @patch("app.services.monitor_service.subprocess.run")
    def test_successful_retrieval_resolves_pod_then_fetches_logs(self, mock_run):
        mock_run.side_effect = [
            _run_mock(returncode=0, stdout=b"devops-autopilot-app-abc123"),
            _run_mock(
                returncode=0,
                stdout=b"2026-01-01T00:00:00Z line one\n2026-01-01T00:00:01Z line two",
            ),
        ]

        result = monitor_service.get_pod_logs("devops-autopilot-app", tail_lines=50)

        self.assertEqual(
            result, ["2026-01-01T00:00:00Z line one", "2026-01-01T00:00:01Z line two"]
        )
        pod_cmd = mock_run.call_args_list[0].args[0]
        self.assertIn("app=devops-autopilot-app", pod_cmd)
        log_cmd = mock_run.call_args_list[1].args[0]
        self.assertEqual(
            log_cmd,
            ["kubectl", "logs", "devops-autopilot-app-abc123", "--tail=50", "--timestamps=true"],
        )

    @patch("app.services.monitor_service.subprocess.run")
    def test_no_running_pod_returns_message(self, mock_run):
        mock_run.return_value = _run_mock(returncode=1, stderr=b"")

        result = monitor_service.get_pod_logs("app")

        self.assertEqual(result, ["No running pod found for deployment 'app'"])

    @patch("app.services.monitor_service.subprocess.run")
    def test_empty_pod_name_returns_message(self, mock_run):
        mock_run.return_value = _run_mock(returncode=0, stdout=b"   ")

        result = monitor_service.get_pod_logs("app")

        self.assertEqual(result, ["No pod name resolved"])

    @patch("app.services.monitor_service.subprocess.run")
    def test_log_command_failure_surfaces_stderr(self, mock_run):
        mock_run.side_effect = [
            _run_mock(returncode=0, stdout=b"app-abc"),
            _run_mock(returncode=1, stderr=b"pod not found"),
        ]

        result = monitor_service.get_pod_logs("app")

        self.assertEqual(result, ["[kubectl logs error] pod not found"])

    @patch(
        "app.services.monitor_service.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="kubectl", timeout=10),
    )
    def test_timeout_returns_timeout_message(self, mock_run):
        result = monitor_service.get_pod_logs("app")

        self.assertEqual(result, ["[kubectl logs] Command timed out"])


class TestGetAllPodsStatus(unittest.TestCase):
    @patch("app.services.monitor_service.subprocess.run")
    def test_running_pod_parsed_into_expected_shape(self, mock_run):
        pods_json = json.dumps(
            {
                "items": [
                    {
                        "metadata": {
                            "name": "app-abc",
                            "namespace": "default",
                            "labels": {"app": "app"},
                            "creationTimestamp": "2026-01-01T00:00:00Z",
                        },
                        "status": {
                            "phase": "Running",
                            "podIP": "10.0.0.5",
                            "containerStatuses": [
                                {"ready": True, "restartCount": 0, "state": {"running": {}}}
                            ],
                        },
                    }
                ]
            }
        ).encode("utf-8")
        mock_run.return_value = _run_mock(returncode=0, stdout=pods_json)

        result = monitor_service.get_all_pods_status()

        self.assertEqual(len(result), 1)
        pod = result[0]
        self.assertEqual(pod["name"], "app-abc")
        self.assertEqual(pod["namespace"], "default")
        self.assertEqual(pod["status"], "Running")
        self.assertTrue(pod["ready"])
        self.assertEqual(pod["restart_count"], 0)
        self.assertEqual(pod["pod_ip"], "10.0.0.5")
        self.assertEqual(pod["labels"], {"app": "app"})

        cmd = mock_run.call_args.args[0]
        self.assertEqual(cmd, ["kubectl", "get", "pods", "-n", "default", "-o", "json"])

    @patch("app.services.monitor_service.subprocess.run")
    def test_waiting_pod_includes_reason(self, mock_run):
        pods_json = json.dumps(
            {
                "items": [
                    {
                        "metadata": {"name": "app-def"},
                        "status": {
                            "containerStatuses": [
                                {
                                    "ready": False,
                                    "restartCount": 4,
                                    "state": {"waiting": {"reason": "CrashLoopBackOff"}},
                                }
                            ]
                        },
                    }
                ]
            }
        ).encode("utf-8")
        mock_run.return_value = _run_mock(returncode=0, stdout=pods_json)

        result = monitor_service.get_all_pods_status()

        self.assertEqual(result[0]["status"], "Waiting (CrashLoopBackOff)")
        self.assertEqual(result[0]["restart_count"], 4)

    @patch("app.services.monitor_service.subprocess.run")
    def test_terminated_pod_includes_reason(self, mock_run):
        pods_json = json.dumps(
            {
                "items": [
                    {
                        "metadata": {"name": "app-ghi"},
                        "status": {
                            "containerStatuses": [
                                {
                                    "ready": False,
                                    "restartCount": 1,
                                    "state": {"terminated": {"reason": "OOMKilled"}},
                                }
                            ]
                        },
                    }
                ]
            }
        ).encode("utf-8")
        mock_run.return_value = _run_mock(returncode=0, stdout=pods_json)

        result = monitor_service.get_all_pods_status()

        self.assertEqual(result[0]["status"], "Terminated (OOMKilled)")

    @patch("app.services.monitor_service.subprocess.run")
    def test_no_container_statuses_falls_back_to_phase(self, mock_run):
        pods_json = json.dumps(
            {"items": [{"metadata": {"name": "app-pending"}, "status": {"phase": "Pending"}}]}
        ).encode("utf-8")
        mock_run.return_value = _run_mock(returncode=0, stdout=pods_json)

        result = monitor_service.get_all_pods_status()

        self.assertEqual(result[0]["status"], "Pending")
        self.assertFalse(result[0]["ready"])

    @patch("app.services.monitor_service.subprocess.run")
    def test_non_zero_returncode_returns_empty_list(self, mock_run):
        mock_run.return_value = _run_mock(returncode=1, stderr=b"error")

        result = monitor_service.get_all_pods_status()

        self.assertEqual(result, [])

    @patch("app.services.monitor_service.subprocess.run", side_effect=Exception("kubectl not found"))
    def test_exception_returns_single_error_entry(self, mock_run):
        result = monitor_service.get_all_pods_status()

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["name"], "error")
        self.assertIn("kubectl not found", result[0]["status"])


# ---------------------------------------------------------------------------
# monitor_controller: project ownership lookup + wiring
# ---------------------------------------------------------------------------


class TestMonitorControllerDeploymentName(unittest.TestCase):
    def test_simple_project_name(self):
        self.assertEqual(
            MonitorController._deployment_name({"project_name": "Test App"}),
            "devops-autopilot-test-app",
        )

    def test_underscores_and_spaces_normalized_to_hyphens(self):
        self.assertEqual(
            MonitorController._deployment_name({"project_name": "My_Cool Project"}),
            "devops-autopilot-my-cool-project",
        )

    def test_missing_project_name_defaults_to_app(self):
        self.assertEqual(MonitorController._deployment_name({}), "devops-autopilot-app")


class TestMonitorControllerStatus(unittest.TestCase):
    def setUp(self):
        self.user_id = "user-1"
        self.project_doc = {
            "_id": ObjectId(),
            "user_id": self.user_id,
            "project_name": "Test App",
            "analysis_date": "2026-01-01T00:00:00",
            "deployment": {"image": "user/app:latest"},
            "deployment_status": "deployed",
        }

    @patch("app.controllers.monitor_controller.monitor_service.get_all_pods_status")
    @patch("app.controllers.monitor_controller.monitor_service.get_recent_k8s_events")
    @patch("app.controllers.monitor_controller.monitor_service.get_aws_health")
    @patch("app.controllers.monitor_controller.monitor_service.get_k8s_health")
    def test_status_aggregates_k8s_and_aws_health(
        self, mock_k8s, mock_aws, mock_events, mock_pods
    ):
        mock_k8s.return_value = {"healthy": True, "state": "Running"}
        mock_aws.return_value = {"healthy": False, "status": "not_deployed"}
        mock_events.return_value = [{"type": "Normal"}]
        mock_pods.return_value = [{"name": "pod-1"}]

        with _patch_projects_collection(self.project_doc):
            result = asyncio.run(
                monitor_controller.get_project_monitoring_status(
                    str(self.project_doc["_id"]), self.user_id
                )
            )

        self.assertTrue(result["success"])
        self.assertEqual(result["deployment_name"], "devops-autopilot-test-app")
        # overall_healthy is k8s.healthy OR aws.healthy
        self.assertTrue(result["overall_healthy"])
        self.assertEqual(result["kubernetes"], {"healthy": True, "state": "Running"})
        self.assertEqual(result["aws"], {"healthy": False, "status": "not_deployed"})
        self.assertEqual(result["recent_events"], [{"type": "Normal"}])
        self.assertEqual(result["pods"], [{"name": "pod-1"}])
        self.assertEqual(result["deployment_status"], "deployed")
        mock_k8s.assert_called_once_with("devops-autopilot-test-app")
        mock_aws.assert_called_once_with(str(self.project_doc["_id"]))
        mock_events.assert_called_once_with("devops-autopilot-test-app", limit=20)

    @patch("app.controllers.monitor_controller.monitor_service.get_all_pods_status")
    @patch("app.controllers.monitor_controller.monitor_service.get_recent_k8s_events")
    @patch("app.controllers.monitor_controller.monitor_service.get_aws_health")
    @patch("app.controllers.monitor_controller.monitor_service.get_k8s_health")
    def test_status_unhealthy_when_neither_k8s_nor_aws_is_healthy(
        self, mock_k8s, mock_aws, mock_events, mock_pods
    ):
        mock_k8s.return_value = {"healthy": False}
        mock_aws.return_value = {"healthy": False}
        mock_events.return_value = []
        mock_pods.return_value = []

        with _patch_projects_collection(self.project_doc):
            result = asyncio.run(
                monitor_controller.get_project_monitoring_status(
                    str(self.project_doc["_id"]), self.user_id
                )
            )

        self.assertFalse(result["overall_healthy"])

    def test_status_project_not_found_returns_failure(self):
        with _patch_projects_collection(None):
            result = asyncio.run(
                monitor_controller.get_project_monitoring_status(str(ObjectId()), self.user_id)
            )

        self.assertEqual(result, {"success": False, "message": "Project not found"})

    def test_status_wrong_owner_is_treated_as_not_found(self):
        # _get_project() filters on {_id, user_id} in a single Mongo query
        # (see monitor_controller.py), so a mismatched owner surfaces the
        # same generic "Project not found" as a nonexistent project rather
        # than a distinct 403 - this is existing, intentional behavior (it
        # avoids leaking project existence to non-owners), not a bug.
        with _patch_projects_collection(self.project_doc):
            result = asyncio.run(
                monitor_controller.get_project_monitoring_status(
                    str(self.project_doc["_id"]), "someone-else"
                )
            )

        self.assertEqual(result, {"success": False, "message": "Project not found"})


class TestMonitorControllerHeal(unittest.TestCase):
    def setUp(self):
        self.user_id = "user-1"
        self.project_doc = {"_id": ObjectId(), "user_id": self.user_id, "project_name": "Test App"}

    @patch("app.controllers.monitor_controller.monitor_service.trigger_self_healing")
    def test_heal_targets_correct_deployment_and_reports_success(self, mock_heal):
        mock_heal.return_value = {
            "success": True,
            "message": "Successfully triggered restart for devops-autopilot-test-app",
        }

        with _patch_projects_collection(self.project_doc):
            result = asyncio.run(
                monitor_controller.heal_project(str(self.project_doc["_id"]), self.user_id)
            )

        self.assertTrue(result["success"])
        mock_heal.assert_called_once_with("devops-autopilot-test-app")

    @patch("app.controllers.monitor_controller.monitor_service.trigger_self_healing")
    def test_heal_failure_is_propagated(self, mock_heal):
        mock_heal.return_value = {"success": False, "message": "Failed to restart: not found"}

        with _patch_projects_collection(self.project_doc):
            result = asyncio.run(
                monitor_controller.heal_project(str(self.project_doc["_id"]), self.user_id)
            )

        self.assertFalse(result["success"])
        self.assertIn("not found", result["message"])

    def test_heal_project_not_found(self):
        with _patch_projects_collection(None):
            result = asyncio.run(monitor_controller.heal_project(str(ObjectId()), self.user_id))

        self.assertEqual(result, {"success": False, "message": "Project not found"})


class TestMonitorControllerLogsAndEvents(unittest.TestCase):
    def setUp(self):
        self.user_id = "user-1"
        self.project_doc = {"_id": ObjectId(), "user_id": self.user_id, "project_name": "Test App"}

    @patch("app.controllers.monitor_controller.monitor_service.get_pod_logs")
    def test_get_pod_logs_returns_snapshot_with_count(self, mock_logs):
        mock_logs.return_value = ["line1", "line2", "line3"]

        with _patch_projects_collection(self.project_doc):
            result = asyncio.run(
                monitor_controller.get_pod_logs(
                    str(self.project_doc["_id"]), self.user_id, tail=200
                )
            )

        self.assertTrue(result["success"])
        self.assertEqual(result["logs"], ["line1", "line2", "line3"])
        self.assertEqual(result["count"], 3)
        mock_logs.assert_called_once_with("devops-autopilot-test-app", tail_lines=200)

    def test_get_pod_logs_project_not_found(self):
        with _patch_projects_collection(None):
            result = asyncio.run(monitor_controller.get_pod_logs(str(ObjectId()), self.user_id))

        self.assertFalse(result["success"])
        self.assertEqual(result["logs"], [])

    @patch("app.controllers.monitor_controller.monitor_service.get_recent_k8s_events")
    def test_get_k8s_events_returns_events_with_count(self, mock_events):
        mock_events.return_value = [{"type": "Warning", "reason": "BackOff"}]

        with _patch_projects_collection(self.project_doc):
            result = asyncio.run(
                monitor_controller.get_k8s_events(
                    str(self.project_doc["_id"]), self.user_id, limit=5
                )
            )

        self.assertTrue(result["success"])
        self.assertEqual(result["count"], 1)
        mock_events.assert_called_once_with("devops-autopilot-test-app", limit=5)

    def test_get_k8s_events_project_not_found(self):
        with _patch_projects_collection(None):
            result = asyncio.run(monitor_controller.get_k8s_events(str(ObjectId()), self.user_id))

        self.assertFalse(result["success"])
        self.assertEqual(result["events"], [])

    @patch("app.controllers.monitor_controller.monitor_service.get_all_pods_status")
    def test_get_all_pods_returns_pods_with_count(self, mock_pods):
        mock_pods.return_value = [{"name": "pod-1"}, {"name": "pod-2"}]

        result = asyncio.run(monitor_controller.get_all_pods(self.user_id))

        self.assertTrue(result["success"])
        self.assertEqual(result["count"], 2)
        self.assertEqual(result["pods"], [{"name": "pod-1"}, {"name": "pod-2"}])


class TestMonitorControllerStreamLogs(unittest.TestCase):
    def setUp(self):
        self.user_id = "user-1"
        self.project_doc = {"_id": ObjectId(), "user_id": self.user_id, "project_name": "Test App"}

    def test_project_not_found_yields_error_without_touching_kubectl(self):
        async def _collect():
            with _patch_projects_collection(None):
                gen = await monitor_controller.stream_logs(str(ObjectId()), self.user_id)
            return [chunk async for chunk in gen]

        chunks = asyncio.run(_collect())

        self.assertEqual(len(chunks), 1)
        self.assertIn('"type": "error"', chunks[0])
        self.assertIn("Project not found", chunks[0])

    @patch("app.controllers.monitor_controller.stream_pod_logs")
    def test_success_delegates_to_stream_pod_logs_with_correct_deployment_name(
        self, mock_stream
    ):
        async def _fake_gen():
            yield "data: ok\n\n"

        mock_stream.return_value = _fake_gen()

        with _patch_projects_collection(self.project_doc):
            result_gen = asyncio.run(
                monitor_controller.stream_logs(str(self.project_doc["_id"]), self.user_id)
            )

        mock_stream.assert_called_once_with("devops-autopilot-test-app")
        self.assertIs(result_gen, mock_stream.return_value)


# ---------------------------------------------------------------------------
# Route-level: every /api/monitor/* endpoint requires a valid JWT
# ---------------------------------------------------------------------------


class TestMonitorRouteAuthRequired(unittest.TestCase):
    """
    Drives the real FastAPI app through TestClient (the established pattern
    in tests/test_auth_api.py) to prove every /api/monitor/* endpoint is
    behind auth:
      - No Authorization header at all -> 403 ("Not authenticated"), which
        is FastAPI's HTTPBearer(auto_error=True) default behavior - verified
        against the same get_current_user dependency in
        test_auth_api.py::TestMe::test_me_without_token. This is NOT a bug;
        401 is reserved for a header that IS present but fails JWT decoding.
      - A header that IS present but isn't a valid JWT -> 401.
      - The one exception is /logs/stream, which can't use HTTPBearer at all
        (browser EventSource can't set custom headers) and instead reads
        ?token=<jwt> from the query string by hand - it returns 401 in
        *both* the "missing" and "invalid" cases (see app/routes/monitor.py).

    None of these requests ever reach Mongo, kubectl, or terraform: auth
    fails during dependency resolution, before MonitorController/
    monitor_service is ever invoked. A live MongoDB is only needed because
    the app's startup lifespan (app.config.database.Database.connect_db)
    pings it; the class is skipped, not failed, if that's unavailable -
    mirroring TestDeploymentControllerPipeline.setUpClass in
    tests/test_k8s_deploy.py.
    """

    @classmethod
    def setUpClass(cls):
        async def _ping():
            client = AsyncIOMotorClient(settings.MONGODB_URL)
            try:
                await client.admin.command("ping")
            finally:
                client.close()

        try:
            asyncio.run(_ping())
        except Exception as exc:  # pragma: no cover - environment guard
            raise unittest.SkipTest(f"MongoDB not reachable at {settings.MONGODB_URL}: {exc}")

        if settings.DATABASE_NAME != "devops_autopilot_test":
            raise RuntimeError(
                "Refusing to run monitor route tests: DATABASE_NAME is "
                f"'{settings.DATABASE_NAME}', expected 'devops_autopilot_test'. "
                "This guards against accidentally connecting to the real database."
            )

        cls._client_cm = TestClient(app)
        cls.client = cls._client_cm.__enter__()
        cls.project_id = str(ObjectId())

    @classmethod
    def tearDownClass(cls):
        cls._client_cm.__exit__(None, None, None)
        sync_client = MongoClient(settings.MONGODB_URL)
        try:
            sync_client.drop_database(settings.DATABASE_NAME)
        finally:
            sync_client.close()

    def test_status_without_header_returns_403(self):
        resp = self.client.get(f"/api/monitor/{self.project_id}/status")
        self.assertEqual(resp.status_code, 403)

    def test_status_with_invalid_token_returns_401(self):
        resp = self.client.get(
            f"/api/monitor/{self.project_id}/status",
            headers={"Authorization": "Bearer garbage.token.value"},
        )
        self.assertEqual(resp.status_code, 401)

    def test_heal_without_header_returns_403(self):
        resp = self.client.post(f"/api/monitor/{self.project_id}/heal")
        self.assertEqual(resp.status_code, 403)

    def test_heal_with_invalid_token_returns_401(self):
        resp = self.client.post(
            f"/api/monitor/{self.project_id}/heal",
            headers={"Authorization": "Bearer garbage.token.value"},
        )
        self.assertEqual(resp.status_code, 401)

    def test_logs_without_header_returns_403(self):
        resp = self.client.get(f"/api/monitor/{self.project_id}/logs")
        self.assertEqual(resp.status_code, 403)

    def test_logs_with_invalid_token_returns_401(self):
        resp = self.client.get(
            f"/api/monitor/{self.project_id}/logs",
            headers={"Authorization": "Bearer garbage.token.value"},
        )
        self.assertEqual(resp.status_code, 401)

    def test_events_without_header_returns_403(self):
        resp = self.client.get(f"/api/monitor/{self.project_id}/events")
        self.assertEqual(resp.status_code, 403)

    def test_events_with_invalid_token_returns_401(self):
        resp = self.client.get(
            f"/api/monitor/{self.project_id}/events",
            headers={"Authorization": "Bearer garbage.token.value"},
        )
        self.assertEqual(resp.status_code, 401)

    def test_pods_all_without_header_returns_403(self):
        resp = self.client.get("/api/monitor/pods/all")
        self.assertEqual(resp.status_code, 403)

    def test_pods_all_with_invalid_token_returns_401(self):
        resp = self.client.get(
            "/api/monitor/pods/all", headers={"Authorization": "Bearer garbage.token.value"}
        )
        self.assertEqual(resp.status_code, 401)

    def test_logs_stream_without_token_query_param_returns_401(self):
        resp = self.client.get(f"/api/monitor/{self.project_id}/logs/stream")
        self.assertEqual(resp.status_code, 401)
        self.assertIn("Missing auth token", resp.text)

    def test_logs_stream_with_invalid_token_query_param_returns_401(self):
        resp = self.client.get(
            f"/api/monitor/{self.project_id}/logs/stream", params={"token": "garbage"}
        )
        self.assertEqual(resp.status_code, 401)
        self.assertIn("Invalid token", resp.text)


if __name__ == "__main__":
    unittest.main()
