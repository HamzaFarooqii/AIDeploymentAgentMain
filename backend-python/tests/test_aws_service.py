"""
Unit tests for the AWS deployment execution layer:

- app.services.aws_service.AWSDeploymentService (terraform init/plan/apply/
  destroy execution, terraform output parsing, deployment status, EC2
  stop/start via AWS CLI, terraform CLI presence check)
- app.services.aws_service.verify_aws_credentials (module-level AWS
  credential verification helper)

SAFETY: every test in this file mocks subprocess.Popen/subprocess.run at the
exact point aws_service.py calls them
(``app.services.aws_service.subprocess.Popen`` / ``...subprocess.run``). No
test in this file ever invokes a real ``terraform`` or ``aws`` CLI binary.
Services under test are additionally constructed with an obviously-fake
``terraform_path`` (a path that does not exist on disk) so that even if a
patch were somehow bypassed, the call would fail fast with FileNotFoundError
rather than silently shelling out to a real terraform installation. Mock
call assertions are used throughout to prove the constructed commands never
actually left the process, and a dedicated lifecycle test at the bottom of
this file drives init -> apply -> destroy -> stop -> start in one go while
asserting on the mocked call counts/args.

aws_service.py does not touch MongoDB (no database imports anywhere in the
module), so unlike test_k8s_deploy.py this file does not need a
DATABASE_NAME override or a Mongo reachability self-check.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import ANY, MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config.settings import settings
from app.services.aws_service import AWSDeploymentService, verify_aws_credentials

# Obviously-fake terraform binary path: if a Popen/run patch were ever
# bypassed, using this path would raise FileNotFoundError immediately
# instead of silently invoking a real terraform install.
FAKE_TERRAFORM_PATH = os.path.join(tempfile.gettempdir(), "definitely-not-a-real-terraform-binary")


def _popen_mock(returncode=0, lines=None):
    """
    Build a MagicMock standing in for a subprocess.Popen instance whose
    stdout is iterated line-by-line by _run_terraform_command (text=True,
    stderr redirected into stdout).
    """
    mock_process = MagicMock()
    mock_process.stdout = iter([line + "\n" for line in (lines or [])])
    mock_process.returncode = returncode
    mock_process.wait.return_value = returncode
    return mock_process


def _run_result(returncode=0, stdout="", stderr=""):
    """Build a MagicMock standing in for a subprocess.run() CompletedProcess."""
    mock_result = MagicMock()
    mock_result.returncode = returncode
    mock_result.stdout = stdout
    mock_result.stderr = stderr
    return mock_result


class _ServiceTestCase(unittest.TestCase):
    """Base class that gives each test a fresh temp project dir + service."""

    def setUp(self):
        self.project_dir = tempfile.mkdtemp()
        self.service = AWSDeploymentService(self.project_dir, terraform_path=FAKE_TERRAFORM_PATH)

    def tearDown(self):
        shutil.rmtree(self.project_dir, ignore_errors=True)


class TestServiceInitialization(_ServiceTestCase):
    def test_infra_directory_is_created_under_project_path(self):
        expected_infra_path = os.path.join(self.project_dir, "infra")

        self.assertEqual(self.service.infra_path, expected_infra_path)
        self.assertTrue(os.path.isdir(expected_infra_path))

    def test_default_terraform_path_is_bare_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = AWSDeploymentService(tmp)
            self.assertEqual(service.terraform_path, "terraform")


class TestWriteTerraform(_ServiceTestCase):
    def test_writes_hcl_content_to_default_filename(self):
        filepath = self.service.write_terraform('resource "aws_instance" "x" {}')

        self.assertEqual(filepath, os.path.join(self.service.infra_path, "main.tf"))
        with open(filepath, "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), 'resource "aws_instance" "x" {}')

    def test_writes_hcl_content_to_custom_filename(self):
        filepath = self.service.write_terraform("variable x {}", filename="variables.tf")

        self.assertEqual(filepath, os.path.join(self.service.infra_path, "variables.tf"))
        with open(filepath, "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), "variable x {}")


class TestRunTerraformCommandGenerators(_ServiceTestCase):
    """
    Covers terraform_init/plan/apply/destroy, which are all thin wrappers
    around the shared _run_terraform_command generator.
    """

    @patch("app.services.aws_service.subprocess.Popen")
    def test_successful_init_yields_running_then_lines_then_success(self, mock_popen):
        mock_popen.return_value = _popen_mock(
            returncode=0,
            lines=["Initializing the backend...", "Terraform has been successfully initialized!"],
        )

        chunks = list(self.service.terraform_init())

        self.assertEqual(chunks[0], {
            "type": "info",
            "message": f"Running: {FAKE_TERRAFORM_PATH} init -input=false",
            "stage": "init",
        })
        self.assertEqual(chunks[1]["message"], "Initializing the backend...")
        self.assertEqual(chunks[1]["type"], "info")
        self.assertEqual(chunks[-1], {
            "type": "success",
            "message": "Terraform init completed",
            "stage": "init",
            "exit_code": 0,
        })

        mock_popen.assert_called_once_with(
            [FAKE_TERRAFORM_PATH, "init", "-input=false"],
            cwd=self.service.infra_path,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=ANY,
        )
        # env is built from os.environ + TF_IN_AUTOMATION - prove it's a
        # real dict and not something that could carry a live subprocess.
        env_used = mock_popen.call_args.kwargs["env"]
        self.assertEqual(env_used.get("TF_IN_AUTOMATION"), "1")

    @patch("app.services.aws_service.subprocess.Popen")
    def test_apply_builds_var_flags_and_stringifies_booleans(self, mock_popen):
        mock_popen.return_value = _popen_mock(returncode=0, lines=["Apply complete! Resources: 1 added, 0 changed."])

        list(self.service.terraform_apply(variables={"instance_count": 2, "enable_https": True}))

        cmd = mock_popen.call_args.args[0]
        self.assertEqual(
            cmd,
            [
                FAKE_TERRAFORM_PATH,
                "apply",
                "-auto-approve",
                "-var",
                "instance_count=2",
                "-var",
                "enable_https=true",
            ],
        )

    @patch("app.services.aws_service.subprocess.Popen")
    def test_apply_without_auto_approve_omits_flag(self, mock_popen):
        mock_popen.return_value = _popen_mock(returncode=0, lines=[])

        list(self.service.terraform_apply(auto_approve=False))

        cmd = mock_popen.call_args.args[0]
        self.assertEqual(cmd, [FAKE_TERRAFORM_PATH, "apply"])

    @patch("app.services.aws_service.subprocess.Popen")
    def test_apply_success_line_is_classified_as_success_type(self, mock_popen):
        mock_popen.return_value = _popen_mock(
            returncode=0,
            lines=["Apply complete! Resources: 1 added, 0 changed, 0 destroyed."],
        )

        chunks = list(self.service.terraform_apply())

        success_lines = [c for c in chunks if c["message"].startswith("Apply complete")]
        self.assertEqual(len(success_lines), 1)
        self.assertEqual(success_lines[0]["type"], "success")

    @patch("app.services.aws_service.subprocess.Popen")
    def test_failing_apply_surfaces_error_lines_and_nonzero_exit_code(self, mock_popen):
        mock_popen.return_value = _popen_mock(
            returncode=1,
            lines=[
                "Error: Error launching source instance: UnauthorizedOperation",
                "\tstatus code: 403",
            ],
        )

        chunks = list(self.service.terraform_apply())

        error_line_chunks = [c for c in chunks if "UnauthorizedOperation" in c["message"]]
        self.assertEqual(len(error_line_chunks), 1)
        self.assertEqual(error_line_chunks[0]["type"], "error")

        # Final summary chunk must surface the failure - not swallow it.
        self.assertEqual(chunks[-1], {
            "type": "error",
            "message": "Terraform apply failed",
            "stage": "apply",
            "exit_code": 1,
        })

    @patch("app.services.aws_service.subprocess.Popen")
    def test_warning_line_is_classified_as_warning_type(self, mock_popen):
        mock_popen.return_value = _popen_mock(
            returncode=0,
            lines=["Warning: argument is deprecated"],
        )

        chunks = list(self.service.terraform_plan())

        warning_chunks = [c for c in chunks if "deprecated" in c["message"]]
        self.assertEqual(warning_chunks[0]["type"], "warning")

    @patch("app.services.aws_service.subprocess.Popen")
    def test_blank_lines_are_not_yielded(self, mock_popen):
        mock_popen.return_value = _popen_mock(returncode=0, lines=["", "  ", "real output line"])

        chunks = list(self.service.terraform_plan())

        messages = [c["message"] for c in chunks]
        self.assertEqual(messages, [f"Running: {FAKE_TERRAFORM_PATH} plan", "real output line", "Terraform plan completed"])

    @patch("app.services.aws_service.subprocess.Popen")
    def test_ansi_escape_codes_are_stripped_from_output_lines(self, mock_popen):
        mock_popen.return_value = _popen_mock(returncode=0, lines=["\x1b[32mTerraform has been successfully initialized!\x1b[0m"])

        chunks = list(self.service.terraform_init())

        self.assertIn("Terraform has been successfully initialized!", chunks[1]["message"])
        self.assertNotIn("\x1b", chunks[1]["message"])

    @patch("app.services.aws_service.subprocess.Popen")
    def test_destroy_builds_command_and_reports_destroy_complete(self, mock_popen):
        mock_popen.return_value = _popen_mock(
            returncode=0,
            lines=["Destroy complete! Resources: 3 destroyed."],
        )

        chunks = list(self.service.terraform_destroy(variables={"region": "eu-north-1"}))

        cmd = mock_popen.call_args.args[0]
        self.assertEqual(cmd, [FAKE_TERRAFORM_PATH, "destroy", "-auto-approve", "-var", "region=eu-north-1"])

        destroy_line = [c for c in chunks if "Destroy complete" in c["message"]][0]
        self.assertEqual(destroy_line["type"], "success")
        self.assertEqual(chunks[-1]["exit_code"], 0)
        self.assertEqual(chunks[-1]["stage"], "destroy")

    @patch("app.services.aws_service.subprocess.Popen")
    def test_failing_destroy_reports_nonzero_exit_code(self, mock_popen):
        mock_popen.return_value = _popen_mock(
            returncode=1,
            lines=["Error: Instance cannot be destroyed: DependencyViolation"],
        )

        chunks = list(self.service.terraform_destroy())

        self.assertEqual(chunks[-1]["type"], "error")
        self.assertEqual(chunks[-1]["exit_code"], 1)
        self.assertEqual(chunks[-1]["stage"], "destroy")

    @patch("app.services.aws_service.subprocess.Popen", side_effect=FileNotFoundError("terraform not found"))
    def test_missing_terraform_binary_is_handled_without_crashing(self, mock_popen):
        chunks = list(self.service.terraform_init())

        self.assertEqual(chunks[0]["type"], "info")  # "Running: ..." still yielded first
        self.assertEqual(chunks[-1], {
            "type": "error",
            "message": f"Terraform CLI not found at: {FAKE_TERRAFORM_PATH}",
            "stage": "init",
            "exit_code": -1,
        })

    @patch("app.services.aws_service.subprocess.Popen", side_effect=RuntimeError("unexpected failure"))
    def test_unexpected_exception_is_caught_and_reported_not_raised(self, mock_popen):
        chunks = list(self.service.terraform_apply())

        self.assertEqual(chunks[-1], {
            "type": "error",
            "message": "Error running terraform: unexpected failure",
            "stage": "apply",
            "exit_code": -1,
        })


class TestTerraformOutput(_ServiceTestCase):
    @patch("app.services.aws_service.subprocess.run")
    def test_successful_output_is_parsed_into_flat_dict(self, mock_run):
        mock_run.return_value = _run_result(
            returncode=0,
            stdout=json.dumps({
                "instance_id": {"value": "i-0123456789abcdef0"},
                "instance_public_ip": {"value": "1.2.3.4"},
            }),
        )

        result = self.service.terraform_output()

        self.assertEqual(result, {"instance_id": "i-0123456789abcdef0", "instance_public_ip": "1.2.3.4"})
        mock_run.assert_called_once_with(
            [FAKE_TERRAFORM_PATH, "output", "-json"],
            cwd=self.service.infra_path,
            capture_output=True,
            text=True,
            timeout=30,
        )

    @patch("app.services.aws_service.subprocess.run")
    def test_nonzero_returncode_yields_empty_dict(self, mock_run):
        mock_run.return_value = _run_result(returncode=1, stdout="", stderr="no state file")

        self.assertEqual(self.service.terraform_output(), {})

    @patch("app.services.aws_service.subprocess.run")
    def test_empty_stdout_yields_empty_dict(self, mock_run):
        mock_run.return_value = _run_result(returncode=0, stdout="   ")

        self.assertEqual(self.service.terraform_output(), {})

    @patch("app.services.aws_service.subprocess.run")
    def test_invalid_json_is_swallowed_and_returns_empty_dict(self, mock_run):
        mock_run.return_value = _run_result(returncode=0, stdout="{not valid json")

        self.assertEqual(self.service.terraform_output(), {})

    @patch("app.services.aws_service.subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="terraform", timeout=30))
    def test_timeout_is_swallowed_and_returns_empty_dict(self, mock_run):
        self.assertEqual(self.service.terraform_output(), {})


class TestGetDeploymentStatus(_ServiceTestCase):
    @patch("app.services.aws_service.subprocess.run")
    def test_no_state_file_reports_not_deployed_without_calling_terraform(self, mock_run):
        result = self.service.get_deployment_status()

        self.assertEqual(result, {
            "status": "not_deployed",
            "public_ip": None,
            "frontend_url": None,
            "instance_id": None,
            "vpc_id": None,
        })
        mock_run.assert_not_called()

    @patch("app.services.aws_service.subprocess.run")
    def test_state_file_present_with_outputs_reports_deployed(self, mock_run):
        state_file = os.path.join(self.service.infra_path, "terraform.tfstate")
        with open(state_file, "w") as f:
            f.write("{}")

        mock_run.return_value = _run_result(
            returncode=0,
            stdout=json.dumps({
                "instance_public_ip": {"value": "1.2.3.4"},
                "frontend_url": {"value": "http://1.2.3.4"},
                "backend_url": {"value": "http://1.2.3.4:5000"},
                "instance_id": {"value": "i-0123456789abcdef0"},
                "vpc_id": {"value": "vpc-abc123"},
            }),
        )

        result = self.service.get_deployment_status()

        self.assertEqual(result, {
            "status": "deployed",
            "public_ip": "1.2.3.4",
            "frontend_url": "http://1.2.3.4",
            "backend_url": "http://1.2.3.4:5000",
            "instance_id": "i-0123456789abcdef0",
            "vpc_id": "vpc-abc123",
        })

    @patch("app.services.aws_service.subprocess.run")
    def test_state_file_present_but_no_outputs_reports_unknown(self, mock_run):
        state_file = os.path.join(self.service.infra_path, "terraform.tfstate")
        with open(state_file, "w") as f:
            f.write("{}")

        mock_run.return_value = _run_result(returncode=1, stdout="", stderr="no outputs")

        result = self.service.get_deployment_status()

        self.assertEqual(result["status"], "unknown")
        self.assertIsNone(result["public_ip"])


class TestStopInstance(_ServiceTestCase):
    @patch("app.services.aws_service.subprocess.run")
    def test_stops_correct_instance_and_reports_success(self, mock_run):
        mock_run.side_effect = [
            _run_result(returncode=0, stdout=json.dumps({"instance_id": {"value": "i-0123456789abcdef0"}})),
            _run_result(returncode=0, stdout="", stderr=""),
        ]

        chunks = list(self.service.stop_instance())

        self.assertEqual(chunks[0]["type"], "info")
        self.assertIn("i-0123456789abcdef0", chunks[0]["message"])
        self.assertEqual(chunks[-1]["type"], "success")
        self.assertEqual(chunks[-1]["exit_code"], 0)

        # Second subprocess.run call must target the exact instance id via AWS CLI.
        second_call = mock_run.call_args_list[1]
        self.assertEqual(
            second_call.args[0],
            ["aws", "ec2", "stop-instances", "--instance-ids", "i-0123456789abcdef0"],
        )
        self.assertEqual(second_call.kwargs, {"capture_output": True, "text": True, "timeout": 60})

    @patch("app.services.aws_service.subprocess.run")
    def test_no_instance_found_short_circuits_without_aws_cli_call(self, mock_run):
        mock_run.return_value = _run_result(returncode=0, stdout=json.dumps({}))

        chunks = list(self.service.stop_instance())

        self.assertEqual(chunks, [{
            "type": "error",
            "message": "No instance found to stop",
            "stage": "stop",
        }])
        # Only the terraform_output lookup call, never a stop-instances call.
        self.assertEqual(mock_run.call_count, 1)

    @patch("app.services.aws_service.subprocess.run")
    def test_aws_cli_failure_surfaces_stderr_and_exit_code(self, mock_run):
        mock_run.side_effect = [
            _run_result(returncode=0, stdout=json.dumps({"instance_id": {"value": "i-abc"}})),
            _run_result(returncode=1, stdout="", stderr="An error occurred (UnauthorizedOperation)"),
        ]

        chunks = list(self.service.stop_instance())

        self.assertEqual(chunks[-1]["type"], "error")
        self.assertIn("UnauthorizedOperation", chunks[-1]["message"])
        self.assertEqual(chunks[-1]["exit_code"], 1)

    @patch("app.services.aws_service.subprocess.run")
    def test_aws_cli_exception_is_caught_and_reported(self, mock_run):
        mock_run.side_effect = [
            _run_result(returncode=0, stdout=json.dumps({"instance_id": {"value": "i-abc"}})),
            OSError("aws cli not found"),
        ]

        chunks = list(self.service.stop_instance())

        self.assertEqual(chunks[-1]["type"], "error")
        self.assertIn("aws cli not found", chunks[-1]["message"])
        self.assertEqual(chunks[-1]["exit_code"], -1)


class TestStartInstance(_ServiceTestCase):
    @patch("app.services.aws_service.subprocess.run")
    def test_starts_correct_instance_and_reports_success(self, mock_run):
        mock_run.side_effect = [
            _run_result(returncode=0, stdout=json.dumps({"instance_id": {"value": "i-0123456789abcdef0"}})),
            _run_result(returncode=0, stdout="", stderr=""),
        ]

        chunks = list(self.service.start_instance())

        self.assertEqual(chunks[-1]["type"], "success")
        second_call = mock_run.call_args_list[1]
        self.assertEqual(
            second_call.args[0],
            ["aws", "ec2", "start-instances", "--instance-ids", "i-0123456789abcdef0"],
        )
        self.assertEqual(second_call.kwargs, {"capture_output": True, "text": True, "timeout": 60})

    @patch("app.services.aws_service.subprocess.run")
    def test_no_instance_found_short_circuits_without_aws_cli_call(self, mock_run):
        mock_run.return_value = _run_result(returncode=0, stdout=json.dumps({}))

        chunks = list(self.service.start_instance())

        self.assertEqual(chunks, [{
            "type": "error",
            "message": "No instance found to start",
            "stage": "start",
        }])
        self.assertEqual(mock_run.call_count, 1)

    @patch("app.services.aws_service.subprocess.run")
    def test_aws_cli_failure_surfaces_stderr_and_exit_code(self, mock_run):
        mock_run.side_effect = [
            _run_result(returncode=0, stdout=json.dumps({"instance_id": {"value": "i-abc"}})),
            _run_result(returncode=1, stdout="", stderr="An error occurred (InvalidInstanceID.NotFound)"),
        ]

        chunks = list(self.service.start_instance())

        self.assertEqual(chunks[-1]["type"], "error")
        self.assertIn("InvalidInstanceID.NotFound", chunks[-1]["message"])
        self.assertEqual(chunks[-1]["exit_code"], 1)


class TestScaleAliases(_ServiceTestCase):
    """scale_to_zero/scale_up are documented aliases for stop_instance/start_instance."""

    def test_scale_to_zero_delegates_to_stop_instance(self):
        expected = [{"type": "success", "message": "stopped", "stage": "stop", "exit_code": 0}]
        with patch.object(self.service, "stop_instance", return_value=iter(expected)) as mock_stop:
            chunks = list(self.service.scale_to_zero())

        self.assertEqual(chunks, expected)
        mock_stop.assert_called_once_with()

    def test_scale_up_delegates_to_start_instance(self):
        expected = [{"type": "success", "message": "started", "stage": "start", "exit_code": 0}]
        with patch.object(self.service, "start_instance", return_value=iter(expected)) as mock_start:
            chunks = list(self.service.scale_up(desired_count=3))

        self.assertEqual(chunks, expected)
        mock_start.assert_called_once_with()


class TestCheckTerraformInstalled(_ServiceTestCase):
    @patch("app.services.aws_service.subprocess.run")
    def test_returns_true_on_zero_exit_code(self, mock_run):
        mock_run.return_value = _run_result(returncode=0, stdout="Terraform v1.7.0")

        self.assertTrue(self.service.check_terraform_installed())
        mock_run.assert_called_once_with(
            [FAKE_TERRAFORM_PATH, "version"],
            capture_output=True,
            text=True,
            timeout=10,
        )

    @patch("app.services.aws_service.subprocess.run")
    def test_returns_false_on_nonzero_exit_code(self, mock_run):
        mock_run.return_value = _run_result(returncode=1)

        self.assertFalse(self.service.check_terraform_installed())

    @patch("app.services.aws_service.subprocess.run", side_effect=FileNotFoundError())
    def test_returns_false_when_binary_missing(self, mock_run):
        self.assertFalse(self.service.check_terraform_installed())

    @patch("app.services.aws_service.subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="terraform", timeout=10))
    def test_returns_false_on_timeout(self, mock_run):
        self.assertFalse(self.service.check_terraform_installed())


class TestGetTerraformEnv(_ServiceTestCase):
    def test_includes_profile_and_region_when_configured(self):
        with patch.object(settings, "AWS_PROFILE", "my-terraform-profile"), \
             patch.object(settings, "AWS_DEFAULT_REGION", "eu-north-1"):
            env = self.service._get_terraform_env()

        self.assertEqual(env["TF_IN_AUTOMATION"], "1")
        self.assertEqual(env["AWS_PROFILE"], "my-terraform-profile")
        self.assertEqual(env["AWS_DEFAULT_REGION"], "eu-north-1")
        self.assertEqual(env["AWS_REGION"], "eu-north-1")

    def test_omits_profile_key_when_not_configured(self):
        with patch.object(settings, "AWS_PROFILE", None):
            env = self.service._get_terraform_env()

        self.assertNotIn("AWS_PROFILE", env)


class TestStripAnsi(_ServiceTestCase):
    def test_strips_color_codes(self):
        self.assertEqual(
            self.service._strip_ansi("\x1b[1;32mSuccess\x1b[0m"),
            "Success",
        )

    def test_leaves_plain_text_untouched(self):
        self.assertEqual(self.service._strip_ansi("plain output line"), "plain output line")


class TestVerifyAwsCredentials(unittest.TestCase):
    @patch("app.services.aws_service.subprocess.run")
    def test_valid_profile_reports_success(self, mock_run):
        mock_run.return_value = _run_result(returncode=0, stdout='{"Account": "123456789012"}')

        with patch.dict(os.environ, {"AWS_PROFILE": "my-profile", "AWS_DEFAULT_REGION": "eu-north-1"}, clear=True):
            result = verify_aws_credentials()

        self.assertTrue(result["is_valid"])
        self.assertIn("my-profile", result["message"])
        mock_run.assert_called_once_with(
            ["aws", "sts", "get-caller-identity", "--profile", "my-profile"],
            capture_output=True,
            text=True,
            timeout=10,
        )

    @patch("app.services.aws_service.subprocess.run")
    def test_invalid_profile_reports_failure(self, mock_run):
        mock_run.return_value = _run_result(returncode=1, stderr="ExpiredToken")

        with patch.dict(os.environ, {"AWS_PROFILE": "stale-profile"}, clear=True):
            result = verify_aws_credentials()

        self.assertFalse(result["is_valid"])
        self.assertIn("invalid or expired", result["message"])

    @patch("app.services.aws_service.subprocess.run", side_effect=FileNotFoundError())
    def test_missing_aws_cli_reports_failure(self, mock_run):
        with patch.dict(os.environ, {"AWS_PROFILE": "my-profile"}, clear=True):
            result = verify_aws_credentials()

        self.assertFalse(result["is_valid"])
        self.assertIn("AWS CLI not found", result["message"])

    @patch("app.services.aws_service.subprocess.run")
    def test_access_key_pair_without_profile_skips_cli_call(self, mock_run):
        env = {
            "AWS_ACCESS_KEY_ID": "AKIAFAKEEXAMPLE",
            "AWS_SECRET_ACCESS_KEY": "fakesecret",
        }
        with patch.dict(os.environ, env, clear=True):
            result = verify_aws_credentials()

        self.assertTrue(result["is_valid"])
        mock_run.assert_not_called()

    @patch("app.services.aws_service.subprocess.run")
    def test_no_credentials_configured_reports_failure(self, mock_run):
        with patch.dict(os.environ, {}, clear=True):
            result = verify_aws_credentials()

        self.assertFalse(result["is_valid"])
        self.assertIn("Set AWS_PROFILE", result["message"])
        mock_run.assert_not_called()


class TestNoRealSubprocessEscapesFullLifecycle(_ServiceTestCase):
    """
    Drives init -> apply -> destroy -> stop -> start through one service
    instance with both subprocess entry points mocked, and asserts on the
    mock call counts/args. Every command that reached "the OS" in this test
    actually reached a MagicMock instead - proving no real terraform/aws-cli
    process could have been spawned anywhere in this flow.
    """

    @patch("app.services.aws_service.subprocess.run")
    @patch("app.services.aws_service.subprocess.Popen")
    def test_full_lifecycle_never_touches_real_subprocess(self, mock_popen, mock_run):
        mock_popen.return_value = _popen_mock(returncode=0, lines=["ok"])
        mock_run.return_value = _run_result(
            returncode=0,
            stdout=json.dumps({"instance_id": {"value": "i-lifecycle-test"}}),
        )

        list(self.service.terraform_init())
        list(self.service.terraform_apply())
        list(self.service.terraform_destroy())
        list(self.service.stop_instance())
        list(self.service.start_instance())

        # 3 terraform CLI invocations via Popen (init/apply/destroy).
        self.assertEqual(mock_popen.call_count, 3)
        for call in mock_popen.call_args_list:
            self.assertEqual(call.args[0][0], FAKE_TERRAFORM_PATH)

        # 2 terraform-output lookups + 2 aws-cli calls (stop, start) via run.
        self.assertEqual(mock_run.call_count, 4)
        aws_calls = [c for c in mock_run.call_args_list if c.args[0][0] == "aws"]
        self.assertEqual(len(aws_calls), 2)
        self.assertEqual(aws_calls[0].args[0], ["aws", "ec2", "stop-instances", "--instance-ids", "i-lifecycle-test"])
        self.assertEqual(aws_calls[1].args[0], ["aws", "ec2", "start-instances", "--instance-ids", "i-lifecycle-test"])

        # Every call went through the mocks, none of them are the real
        # subprocess.Popen/subprocess.run functions.
        self.assertIsInstance(mock_popen, MagicMock)
        self.assertIsInstance(mock_run, MagicMock)


if __name__ == "__main__":
    unittest.main()
