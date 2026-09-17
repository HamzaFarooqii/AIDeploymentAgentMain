"""
Unit tests for the Kubernetes deployment execution layer:

- app.utils.k8s_deployer (kubectl apply/delete/status, retry logic)
- app.controllers.deployment_controller (the non-LLM deploy pipeline:
  build -> push -> k8s deploy, used by POST/GET/DELETE /api/deploy/{id})

SAFETY: every test in this file mocks subprocess.Popen/subprocess.run at the
point k8s_deployer.py (and, transitively, deployment_controller.py) calls
them. No test in this file ever invokes a real `kubectl`, `docker`, or
`terraform` binary. Mock call assertions are used throughout to prove the
constructed commands never actually left the process.

Because deployment_controller.py touches MongoDB (via
app.config.database.get_projects_collection), DATABASE_NAME is overridden to
an isolated test database *before* app.config.settings is imported anywhere
(including transitively), and that test database is dropped in
tearDownClass.
"""

import os

# Must happen before any `app.*` import (including transitive imports from
# other test modules that may already have imported app.config.settings in
# the same pytest session) so deployment_controller's Mongo writes never
# touch the real local `devops_autopilot` database.
os.environ["DATABASE_NAME"] = "devops_autopilot_test"

import asyncio
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bson import ObjectId
from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorClient

from app.config.database import Database, get_projects_collection
from app.config.settings import settings
from app.controllers import deployment_controller
from app.utils import k8s_deployer


def _popen_mock(returncode=0, stdout=b"", stderr=b""):
    """Build a MagicMock standing in for a subprocess.Popen instance."""
    mock_process = MagicMock()
    mock_process.communicate.return_value = (stdout, stderr)
    mock_process.returncode = returncode
    return mock_process


class TestCheckKubernetesConnection(unittest.TestCase):
    @patch("app.utils.k8s_deployer.subprocess.Popen")
    def test_reachable_cluster_reports_success(self, mock_popen):
        mock_popen.return_value = _popen_mock(returncode=0, stdout=b"Kubernetes control plane is running")

        result = k8s_deployer.check_kubernetes_connection()

        self.assertTrue(result["success"])
        mock_popen.assert_called_once_with(
            ["kubectl", "cluster-info"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    @patch("app.utils.k8s_deployer.subprocess.Popen")
    def test_unreachable_cluster_reports_failure_with_stderr(self, mock_popen):
        mock_popen.return_value = _popen_mock(returncode=1, stderr=b"connection refused")

        result = k8s_deployer.check_kubernetes_connection()

        self.assertFalse(result["success"])
        self.assertIn("connection refused", result["message"])

    @patch("app.utils.k8s_deployer.subprocess.Popen", side_effect=FileNotFoundError("kubectl not found"))
    def test_missing_kubectl_binary_is_handled(self, mock_popen):
        result = k8s_deployer.check_kubernetes_connection()

        self.assertFalse(result["success"])
        self.assertIn("kubectl not found", result["message"])


class TestApplyManifestWithRetry(unittest.TestCase):
    @patch("app.utils.k8s_deployer.subprocess.Popen")
    def test_success_on_first_attempt_returns_immediately(self, mock_popen):
        mock_popen.return_value = _popen_mock(returncode=0, stdout=b"configmap/app created")

        success, output = k8s_deployer.apply_manifest_with_retry("/tmp/cm.yaml", "ConfigMap", max_retries=3)

        self.assertTrue(success)
        self.assertEqual(output, "configmap/app created")
        mock_popen.assert_called_once_with(
            ["kubectl", "apply", "-f", "/tmp/cm.yaml", "--validate=false", "--force"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    @patch("app.utils.k8s_deployer.subprocess.Popen")
    def test_non_retryable_error_fails_without_retrying(self, mock_popen):
        mock_popen.return_value = _popen_mock(returncode=1, stderr=b"error: unable to recognize manifest")

        success, output = k8s_deployer.apply_manifest_with_retry("/tmp/deploy.yaml", "Deployment", max_retries=3)

        self.assertFalse(success)
        self.assertIn("unable to recognize manifest", output)
        # A non-retryable error (no EOF/timeout/connection substring) must
        # fail on the very first attempt, never looping.
        self.assertEqual(mock_popen.call_count, 1)

    @patch("app.utils.k8s_deployer.time.sleep")
    @patch("app.utils.k8s_deployer.subprocess.Popen")
    def test_retryable_eof_error_succeeds_after_retry(self, mock_popen, mock_sleep):
        mock_popen.side_effect = [
            _popen_mock(returncode=1, stderr=b"unexpected EOF"),
            _popen_mock(returncode=0, stdout=b"deployment.apps/app created"),
        ]

        success, output = k8s_deployer.apply_manifest_with_retry("/tmp/deploy.yaml", "Deployment", max_retries=3)

        self.assertTrue(success)
        self.assertEqual(output, "deployment.apps/app created")
        self.assertEqual(mock_popen.call_count, 2)
        mock_sleep.assert_called()  # retry backoff was exercised, not real waiting

    @patch("app.utils.k8s_deployer.time.sleep")
    @patch("app.utils.k8s_deployer.subprocess.Popen")
    def test_retries_exhausted_returns_failure(self, mock_popen, mock_sleep):
        mock_popen.return_value = _popen_mock(returncode=1, stderr=b"connection refused")

        success, output = k8s_deployer.apply_manifest_with_retry("/tmp/svc.yaml", "Service", max_retries=3)

        self.assertFalse(success)
        self.assertIn("connection refused", output)
        self.assertEqual(mock_popen.call_count, 3)

    @patch("app.utils.k8s_deployer.time.sleep")
    @patch("app.utils.k8s_deployer.subprocess.Popen")
    def test_timeout_expired_retries_then_fails(self, mock_popen, mock_sleep):
        process_mock = MagicMock()
        process_mock.communicate.side_effect = subprocess.TimeoutExpired(cmd="kubectl", timeout=60)
        mock_popen.return_value = process_mock

        success, output = k8s_deployer.apply_manifest_with_retry("/tmp/cm.yaml", "ConfigMap", max_retries=2)

        self.assertFalse(success)
        self.assertIn("timeout after 2 attempts", output)
        self.assertEqual(mock_popen.call_count, 2)


class TestDeployToKubernetes(unittest.TestCase):
    def _manifests(self):
        return {
            "deployment_name": "devops-autopilot-test-app",
            "deployment": "kind: Deployment",
            "service": "kind: Service",
            "configmap": "kind: ConfigMap",
            "service_port": 30500,
        }

    @patch("app.utils.k8s_deployer.get_pod_status")
    @patch("app.utils.k8s_deployer.time.sleep")
    @patch("app.utils.k8s_deployer.subprocess.run")
    @patch("app.utils.k8s_deployer.apply_manifest_with_retry")
    @patch("app.utils.k8s_deployer.check_kubernetes_connection")
    def test_successful_deploy_applies_all_manifests_in_order(
        self, mock_check, mock_apply, mock_run, mock_sleep, mock_pod_status
    ):
        mock_check.return_value = {"success": True, "message": "reachable"}
        mock_apply.return_value = (True, "ok")
        mock_run.return_value = MagicMock(returncode=0)
        mock_pod_status.return_value = {"pod_name": "devops-autopilot-test-app-abc123", "status": "Running"}

        result = k8s_deployer.deploy_to_kubernetes(self._manifests())

        self.assertEqual(
            result,
            {
                "success": True,
                "deployment_name": "devops-autopilot-test-app",
                "pod_name": "devops-autopilot-test-app-abc123",
                "pod_status": "Running",
                "service_port": 30500,
            },
        )

        # ConfigMap, then Deployment, then Service - in that order.
        applied_types = [call.args[1] for call in mock_apply.call_args_list]
        self.assertEqual(applied_types, ["ConfigMap", "Deployment", "Service"])

        # Pod restart is triggered for the right deployment, via subprocess.run
        # (never a real kubectl invocation - subprocess.run is mocked).
        mock_run.assert_called_once_with(
            ["kubectl", "rollout", "restart", "deployment/devops-autopilot-test-app"],
            capture_output=True,
            timeout=30,
        )

    @patch("app.utils.k8s_deployer.apply_manifest_with_retry")
    @patch("app.utils.k8s_deployer.check_kubernetes_connection")
    def test_configmap_failure_short_circuits_deployment(self, mock_check, mock_apply):
        mock_check.return_value = {"success": True, "message": "reachable"}
        mock_apply.return_value = (False, "invalid configmap yaml")

        result = k8s_deployer.deploy_to_kubernetes(self._manifests())

        self.assertFalse(result["success"])
        self.assertIn("ConfigMap failed", result["message"])
        self.assertIn("invalid configmap yaml", result["message"])
        # Only the ConfigMap apply should have been attempted.
        self.assertEqual(mock_apply.call_count, 1)

    @patch("app.utils.k8s_deployer.apply_manifest_with_retry")
    @patch("app.utils.k8s_deployer.check_kubernetes_connection")
    def test_eof_error_returns_helpful_troubleshooting_message(self, mock_check, mock_apply):
        mock_check.return_value = {"success": True, "message": "reachable"}
        mock_apply.return_value = (False, "Unexpected EOF while reading server response")

        result = k8s_deployer.deploy_to_kubernetes(self._manifests())

        self.assertFalse(result["success"])
        self.assertIn("Possible solutions", result["message"])
        self.assertIn("Docker Desktop", result["message"])

    @patch("app.utils.k8s_deployer.get_pod_status")
    @patch("app.utils.k8s_deployer.time.sleep")
    @patch("app.utils.k8s_deployer.subprocess.run")
    @patch("app.utils.k8s_deployer.apply_manifest_with_retry")
    @patch("app.utils.k8s_deployer.check_kubernetes_connection")
    def test_unreachable_cluster_still_attempts_deploy(
        self, mock_check, mock_apply, mock_run, mock_sleep, mock_pod_status
    ):
        # deploy_to_kubernetes logs a warning but proceeds even when the
        # initial reachability check fails.
        mock_check.return_value = {"success": False, "message": "cluster not reachable"}
        mock_apply.return_value = (True, "ok")
        mock_run.return_value = MagicMock(returncode=0)
        mock_pod_status.return_value = {"pod_name": None, "status": "pending"}

        result = k8s_deployer.deploy_to_kubernetes(self._manifests())

        self.assertTrue(result["success"])
        self.assertEqual(mock_apply.call_count, 3)


class TestGetPodStatus(unittest.TestCase):
    @patch("app.utils.k8s_deployer.subprocess.Popen")
    def test_returns_pod_name_and_phase_when_present(self, mock_popen):
        pods_json = (
            b'{"items": [{"metadata": {"name": "app-abc123"}, '
            b'"status": {"phase": "Running"}}]}'
        )
        mock_popen.return_value = _popen_mock(returncode=0, stdout=pods_json)

        result = k8s_deployer.get_pod_status("app")

        self.assertEqual(result, {"pod_name": "app-abc123", "status": "Running"})
        mock_popen.assert_called_once_with(
            ["kubectl", "get", "pods", "-l", "app=app", "-o", "json"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    @patch("app.utils.k8s_deployer.subprocess.Popen")
    def test_no_pods_yet_returns_pending(self, mock_popen):
        mock_popen.return_value = _popen_mock(returncode=0, stdout=b'{"items": []}')

        result = k8s_deployer.get_pod_status("app")

        self.assertEqual(result, {"pod_name": None, "status": "pending"})

    @patch("app.utils.k8s_deployer.subprocess.Popen")
    def test_non_eof_failure_returns_unknown_without_retry(self, mock_popen):
        mock_popen.return_value = _popen_mock(returncode=1, stderr=b"NotFound")

        result = k8s_deployer.get_pod_status("app", max_retries=3)

        self.assertEqual(result, {"pod_name": None, "status": "unknown"})
        self.assertEqual(mock_popen.call_count, 1)


class TestGetDeploymentStatus(unittest.TestCase):
    @patch("app.utils.k8s_deployer.get_pod_status")
    @patch("app.utils.k8s_deployer.subprocess.Popen")
    def test_ready_replicas_match_reports_running(self, mock_popen, mock_pod_status):
        deploy_json = b'{"status": {"replicas": 2, "readyReplicas": 2}}'
        mock_popen.return_value = _popen_mock(returncode=0, stdout=deploy_json)
        mock_pod_status.return_value = {"pod_name": "app-abc123", "status": "Running"}

        result = k8s_deployer.get_deployment_status("app")

        self.assertEqual(result["status"], "running")
        self.assertEqual(result["replicas"], 2)
        self.assertEqual(result["ready_replicas"], 2)
        self.assertEqual(result["pod_name"], "app-abc123")

    @patch("app.utils.k8s_deployer.get_pod_status")
    @patch("app.utils.k8s_deployer.subprocess.Popen")
    def test_ready_replicas_below_desired_reports_pending(self, mock_popen, mock_pod_status):
        deploy_json = b'{"status": {"replicas": 2, "readyReplicas": 1}}'
        mock_popen.return_value = _popen_mock(returncode=0, stdout=deploy_json)
        mock_pod_status.return_value = {"pod_name": "app-abc123", "status": "Pending"}

        result = k8s_deployer.get_deployment_status("app")

        self.assertEqual(result["status"], "pending")

    @patch("app.utils.k8s_deployer.subprocess.Popen")
    def test_missing_deployment_reports_not_found(self, mock_popen):
        mock_popen.return_value = _popen_mock(returncode=1, stderr=b'Error from server (NotFound)')

        result = k8s_deployer.get_deployment_status("missing-app")

        self.assertEqual(result, {"status": "not_found"})


class TestCleanupDeployment(unittest.TestCase):
    @patch("app.utils.k8s_deployer.subprocess.Popen")
    def test_delete_command_targets_correct_label_selector(self, mock_popen):
        mock_popen.return_value = _popen_mock(returncode=0, stdout=b"deployment.apps \"app\" deleted")

        result = k8s_deployer.cleanup_deployment("app")

        self.assertEqual(result, {"success": True, "deployment_name": "app"})
        mock_popen.assert_called_once_with(
            ["kubectl", "delete", "deployment,service,configmap", "-l", "app=app", "--ignore-not-found=true"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    @patch("app.utils.k8s_deployer.subprocess.Popen", side_effect=Exception("kubectl unavailable"))
    def test_exception_reports_failure(self, mock_popen):
        result = k8s_deployer.cleanup_deployment("app")

        self.assertFalse(result["success"])
        self.assertIn("kubectl unavailable", result["message"])


class TestDiagnosePodHealth(unittest.TestCase):
    @patch("app.utils.k8s_deployer.subprocess.Popen")
    def test_running_container_is_healthy(self, mock_popen):
        pods_json = (
            b'{"items": [{"metadata": {"name": "app-abc"}, '
            b'"status": {"podIP": "10.0.0.5", "containerStatuses": '
            b'[{"restartCount": 0, "state": {"running": {}}}]}}]}'
        )
        mock_popen.return_value = _popen_mock(returncode=0, stdout=pods_json)

        result = k8s_deployer.diagnose_pod_health("app")

        self.assertTrue(result["healthy"])
        self.assertEqual(result["state"], "Running")
        self.assertEqual(result["pod_ip"], "10.0.0.5")

    @patch("app.utils.k8s_deployer.subprocess.Popen")
    def test_crash_loop_backoff_is_unhealthy(self, mock_popen):
        pods_json = (
            b'{"items": [{"metadata": {"name": "app-abc"}, '
            b'"status": {"containerStatuses": [{"restartCount": 5, '
            b'"state": {"waiting": {"reason": "CrashLoopBackOff"}}}]}}]}'
        )
        mock_popen.return_value = _popen_mock(returncode=0, stdout=pods_json)

        result = k8s_deployer.diagnose_pod_health("app")

        self.assertFalse(result["healthy"])
        self.assertEqual(result["state"], "Waiting")
        self.assertEqual(result["reason"], "CrashLoopBackOff")
        self.assertEqual(result["restart_count"], 5)

    @patch("app.utils.k8s_deployer.subprocess.Popen")
    def test_no_pod_found(self, mock_popen):
        mock_popen.return_value = _popen_mock(returncode=0, stdout=b'{"items": []}')

        result = k8s_deployer.diagnose_pod_health("app")

        self.assertFalse(result["healthy"])
        self.assertEqual(result["reason"], "Pod Not Found")


class TestStreamPodLogsNoRealSubprocess(unittest.TestCase):
    """
    stream_pod_logs() only reaches asyncio.create_subprocess_exec (a real
    kubectl invocation) once a pod name has been resolved. We verify the
    early-exit "no pod" path here, which never touches subprocess at all -
    the safest possible way to confirm no real kubectl process can be
    spawned when there's nothing to stream.
    """

    @patch("app.utils.k8s_deployer.get_pod_status")
    def test_no_pod_found_yields_error_and_returns(self, mock_pod_status):
        mock_pod_status.return_value = {"pod_name": None, "status": "pending"}

        async def _collect():
            chunks = []
            async for chunk in k8s_deployer.stream_pod_logs("app"):
                chunks.append(chunk)
            return chunks

        chunks = asyncio.run(_collect())

        self.assertEqual(len(chunks), 1)
        self.assertIn('"type": "error"', chunks[0])
        self.assertIn("No pod found", chunks[0])


# ---------------------------------------------------------------------------
# deployment_controller: the non-LLM deploy pipeline (build -> push -> k8s)
# ---------------------------------------------------------------------------


class TestDeploymentControllerPipeline(unittest.TestCase):
    """
    Exercises deploy_project_handler / get_deployment_status_handler /
    undeploy_project_handler end to end against a real MongoDB instance
    scoped to the `devops_autopilot_test` database (never the real
    `devops_autopilot` database), while mocking every step that would
    otherwise shell out to docker/kubectl (build_docker_image,
    push_docker_image, deploy_to_kubernetes, cleanup_deployment).
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
                "Refusing to run deployment_controller tests: DATABASE_NAME "
                f"is '{settings.DATABASE_NAME}', expected 'devops_autopilot_test'. "
                "This guards against accidentally writing to the real database."
            )

    @classmethod
    def tearDownClass(cls):
        async def _drop():
            client = AsyncIOMotorClient(settings.MONGODB_URL)
            try:
                await client.drop_database(settings.DATABASE_NAME)
            finally:
                client.close()

        asyncio.run(_drop())

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.user_id = "test-user-1"
        self.current_user = {"_id": self.user_id, "username": "tester"}

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _default_project_doc(self):
        return {
            "project_name": "Test App",
            "user_id": self.user_id,
            "status": "analyzed",
            "extracted_path": self.temp_dir,
            "metadata": {
                "language": "Python",
                "framework": "FastAPI",
                "port": 8000,
                "env_variables": [],
            },
            "logs": [],
        }

    async def _run_handler(self, handler_factory, doc_overrides=None, skip_insert=False):
        """
        Runs an entire test scenario (DB connect -> optional insert ->
        handler call -> re-fetch doc -> DB disconnect) inside a single
        asyncio event loop, since AsyncIOMotorClient objects are bound to
        the loop they were created in.
        """
        Database.client = AsyncIOMotorClient(settings.MONGODB_URL)
        Database.database = Database.client[settings.DATABASE_NAME]
        try:
            collection = get_projects_collection()

            if skip_insert:
                project_id = str(ObjectId())
            else:
                doc = self._default_project_doc()
                if doc_overrides:
                    doc.update(doc_overrides)
                insert_result = await collection.insert_one(doc)
                project_id = str(insert_result.inserted_id)

            result = None
            error = None
            try:
                result = await handler_factory(project_id)
            except HTTPException as exc:
                error = exc

            final_doc = None
            if ObjectId.is_valid(project_id):
                final_doc = await collection.find_one({"_id": ObjectId(project_id)})

            return project_id, result, error, final_doc
        finally:
            Database.client.close()

    # -- deploy_project_handler: success path --------------------------

    @patch("app.controllers.deployment_controller.deploy_to_kubernetes")
    @patch("app.controllers.deployment_controller.generate_k8s_manifests")
    @patch("app.controllers.deployment_controller.push_docker_image")
    @patch("app.controllers.deployment_controller.build_docker_image")
    def test_successful_pipeline_uses_docker_hub_username_for_image_name(
        self, mock_build, mock_push, mock_manifests, mock_k8s_deploy
    ):
        mock_build.return_value = {"success": True, "message": "built"}
        mock_push.return_value = {"success": True, "message": "pushed"}
        mock_manifests.return_value = {
            "deployment": "kind: Deployment",
            "service": "kind: Service",
            "configmap": "kind: ConfigMap",
            "deployment_name": "devops-autopilot-test-app",
            "service_port": 30500,
        }
        mock_k8s_deploy.return_value = {
            "success": True,
            "pod_name": "devops-autopilot-test-app-xyz",
            "pod_status": "Running",
        }

        with patch.object(settings, "DOCKER_HUB_USERNAME", "testuser"):
            project_id, result, error, final_doc = asyncio.run(
                self._run_handler(
                    lambda pid: deployment_controller.deploy_project_handler(pid, self.current_user)
                )
            )

        self.assertIsNone(error)
        self.assertTrue(result["success"])

        # Bug-fix regression check (B1, e755e44): the image tag handed to
        # build_docker_image/push_docker_image must be derived from
        # settings.DOCKER_HUB_USERNAME via image_naming.build_project_image_repo,
        # not a hardcoded Docker Hub username.
        build_kwargs = mock_build.call_args.kwargs
        self.assertEqual(build_kwargs["image_tag"], "testuser/devops-autopilot-test-app:latest")
        push_args = mock_push.call_args.args
        self.assertEqual(push_args[0], "testuser/devops-autopilot-test-app:latest")

        # generate_k8s_manifests received the same image tag.
        manifest_kwargs = mock_manifests.call_args.kwargs
        self.assertEqual(manifest_kwargs["image"], "testuser/devops-autopilot-test-app:latest")

        # Final DB state reflects a completed deployment.
        self.assertEqual(final_doc["deployment_status"], "deployed")
        self.assertEqual(final_doc["status"], "completed")
        self.assertEqual(final_doc["deployment"]["image"], "testuser/devops-autopilot-test-app:latest")
        self.assertEqual(final_doc["deployment"]["pod_name"], "devops-autopilot-test-app-xyz")

    # -- deploy_project_handler: failure propagation --------------------

    @patch("app.controllers.deployment_controller.build_docker_image")
    def test_docker_build_failure_marks_deployment_failed(self, mock_build):
        mock_build.return_value = {"success": False, "message": "Dockerfile syntax error"}

        project_id, result, error, final_doc = asyncio.run(
            self._run_handler(
                lambda pid: deployment_controller.deploy_project_handler(pid, self.current_user)
            )
        )

        self.assertIsNotNone(error)
        self.assertEqual(error.status_code, 500)
        self.assertIn("Docker build failed", error.detail)
        self.assertIn("Dockerfile syntax error", error.detail)

        self.assertEqual(final_doc["deployment_status"], "failed")
        self.assertTrue(any("Deployment failed" in log["message"] for log in final_doc["logs"]))

    @patch("app.controllers.deployment_controller.push_docker_image")
    @patch("app.controllers.deployment_controller.build_docker_image")
    def test_docker_push_failure_marks_deployment_failed(self, mock_build, mock_push):
        mock_build.return_value = {"success": True, "message": "built"}
        mock_push.return_value = {"success": False, "message": "unauthorized: authentication required"}

        project_id, result, error, final_doc = asyncio.run(
            self._run_handler(
                lambda pid: deployment_controller.deploy_project_handler(pid, self.current_user)
            )
        )

        self.assertIsNotNone(error)
        self.assertEqual(error.status_code, 500)
        self.assertIn("Docker push failed", error.detail)
        self.assertEqual(final_doc["deployment_status"], "failed")

    @patch("app.controllers.deployment_controller.deploy_to_kubernetes")
    @patch("app.controllers.deployment_controller.generate_k8s_manifests")
    @patch("app.controllers.deployment_controller.push_docker_image")
    @patch("app.controllers.deployment_controller.build_docker_image")
    def test_k8s_deploy_failure_marks_deployment_failed(
        self, mock_build, mock_push, mock_manifests, mock_k8s_deploy
    ):
        mock_build.return_value = {"success": True, "message": "built"}
        mock_push.return_value = {"success": True, "message": "pushed"}
        mock_manifests.return_value = {
            "deployment": "kind: Deployment",
            "service": "kind: Service",
            "configmap": "kind: ConfigMap",
            "deployment_name": "devops-autopilot-test-app",
            "service_port": 30500,
        }
        mock_k8s_deploy.return_value = {"success": False, "message": "ImagePullBackOff"}

        project_id, result, error, final_doc = asyncio.run(
            self._run_handler(
                lambda pid: deployment_controller.deploy_project_handler(pid, self.current_user)
            )
        )

        self.assertIsNotNone(error)
        self.assertEqual(error.status_code, 500)
        self.assertIn("K8s deployment failed", error.detail)
        self.assertIn("ImagePullBackOff", error.detail)
        self.assertEqual(final_doc["deployment_status"], "failed")

    # -- deploy_project_handler: guard clauses ---------------------------

    def test_deploy_when_not_analyzed_returns_failure_without_side_effects(self):
        project_id, result, error, final_doc = asyncio.run(
            self._run_handler(
                lambda pid: deployment_controller.deploy_project_handler(pid, self.current_user),
                doc_overrides={"status": "uploaded"},
            )
        )

        self.assertIsNone(error)
        self.assertFalse(result["success"])
        self.assertEqual(result["current_status"], "uploaded")
        # Guard clause fires before the "deploying" status is ever set.
        self.assertNotEqual(final_doc.get("deployment_status"), "deploying")

    def test_deploy_already_in_progress_returns_400(self):
        project_id, result, error, final_doc = asyncio.run(
            self._run_handler(
                lambda pid: deployment_controller.deploy_project_handler(pid, self.current_user),
                doc_overrides={"deployment_status": "deploying"},
            )
        )

        self.assertIsNotNone(error)
        self.assertEqual(error.status_code, 400)

    def test_deploy_wrong_owner_returns_403(self):
        project_id, result, error, final_doc = asyncio.run(
            self._run_handler(
                lambda pid: deployment_controller.deploy_project_handler(pid, self.current_user),
                doc_overrides={"user_id": "someone-else"},
            )
        )

        self.assertIsNotNone(error)
        self.assertEqual(error.status_code, 403)

    def test_deploy_missing_project_returns_404(self):
        project_id, result, error, final_doc = asyncio.run(
            self._run_handler(
                lambda pid: deployment_controller.deploy_project_handler(pid, self.current_user),
                skip_insert=True,
            )
        )

        self.assertIsNotNone(error)
        self.assertEqual(error.status_code, 404)

    def test_deploy_missing_extracted_path_returns_400(self):
        missing_path = os.path.join(self.temp_dir, "does-not-exist")
        project_id, result, error, final_doc = asyncio.run(
            self._run_handler(
                lambda pid: deployment_controller.deploy_project_handler(pid, self.current_user),
                doc_overrides={"extracted_path": missing_path},
            )
        )

        self.assertIsNotNone(error)
        self.assertEqual(error.status_code, 400)
        self.assertIn("Extracted project files not found", error.detail)

    # -- get_deployment_status_handler -----------------------------------

    def test_get_status_returns_stored_deployment_info(self):
        deployment_info = {
            "deployment_name": "devops-autopilot-test-app",
            "service_url": "http://localhost:30500",
            "status": "running",
        }
        project_id, result, error, final_doc = asyncio.run(
            self._run_handler(
                lambda pid: deployment_controller.get_deployment_status_handler(pid, self.current_user),
                doc_overrides={"deployment_status": "deployed", "deployment": deployment_info},
            )
        )

        self.assertIsNone(error)
        self.assertTrue(result["success"])
        self.assertEqual(result["deployment_status"], "deployed")
        self.assertEqual(result["deployment"], deployment_info)

    def test_get_status_wrong_owner_returns_403(self):
        project_id, result, error, final_doc = asyncio.run(
            self._run_handler(
                lambda pid: deployment_controller.get_deployment_status_handler(pid, self.current_user),
                doc_overrides={"user_id": "someone-else"},
            )
        )

        self.assertIsNotNone(error)
        self.assertEqual(error.status_code, 403)

    # -- undeploy_project_handler ------------------------------------------

    @patch("app.controllers.deployment_controller.cleanup_deployment")
    def test_undeploy_calls_cleanup_and_clears_deployment_state(self, mock_cleanup):
        mock_cleanup.return_value = {"success": True, "deployment_name": "devops-autopilot-test-app"}
        deployment_info = {"deployment_name": "devops-autopilot-test-app"}

        project_id, result, error, final_doc = asyncio.run(
            self._run_handler(
                lambda pid: deployment_controller.undeploy_project_handler(pid, self.current_user),
                doc_overrides={"deployment_status": "deployed", "deployment": deployment_info},
            )
        )

        self.assertIsNone(error)
        self.assertTrue(result["success"])
        mock_cleanup.assert_called_once_with("devops-autopilot-test-app")

        self.assertEqual(final_doc["deployment_status"], "undeployed")
        self.assertIsNone(final_doc["deployment"])

    @patch("app.controllers.deployment_controller.cleanup_deployment")
    def test_undeploy_without_existing_deployment_is_a_noop(self, mock_cleanup):
        project_id, result, error, final_doc = asyncio.run(
            self._run_handler(
                lambda pid: deployment_controller.undeploy_project_handler(pid, self.current_user),
                doc_overrides={"deployment_status": "not_deployed", "deployment": {}},
            )
        )

        self.assertIsNone(error)
        self.assertFalse(result["success"])
        mock_cleanup.assert_not_called()


if __name__ == "__main__":
    unittest.main()
