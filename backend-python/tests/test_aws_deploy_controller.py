import os
import sys
import unittest
from unittest.mock import patch


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.controllers.aws_deploy_controller import (
    _dedupe_ingress_blocks_in_security_groups,
    _enforce_ssh_key_settings,
    _ensure_compose_host_ports_allowed,
    _ensure_output_urls_include_compose_ports,
    _expected_aws_app_images,
    _normalize_compose_images_for_aws,
    _validate_docker_hub_manifests_exist,
    _validate_ssh_command_output,
)
from app.LLM.terraform_deploy_agent import build_terraform_message
from app.utils.image_naming import build_project_image_repo, build_service_image


class TestAWSSecurityGroupPostProcessing(unittest.TestCase):
    def test_compose_port_matching_quoted_app_port_default_is_not_added(self):
        terraform_code = '''
variable "app_port" {
  default = "5000"
}

resource "aws_security_group" "instance_sg" {
  ingress {
    from_port   = var.app_port
    to_port     = var.app_port
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_instance" "main" {
  user_data = <<-EOF
ports:
  - "5000:5000"
EOF
}
'''

        result = _ensure_compose_host_ports_allowed(terraform_code)

        self.assertNotIn('description = "App port 5000"', result)
        self.assertEqual(result.count("from_port   = var.app_port"), 1)

    def test_duplicate_literal_and_variable_ingress_blocks_are_deduped(self):
        terraform_code = '''
variable "app_port" {
  default = 5000
}

resource "aws_security_group" "instance_sg" {
  ingress {
    from_port   = var.app_port
    to_port     = var.app_port
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "App port 5000"
    from_port   = 5000
    to_port     = 5000
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
}
'''

        result = _dedupe_ingress_blocks_in_security_groups(terraform_code)

        self.assertEqual(result.count("ingress {"), 1)
        self.assertIn("from_port   = var.app_port", result)
        self.assertNotIn('description = "App port 5000"', result)


class TestAWSSSHKeyPostProcessing(unittest.TestCase):
    def test_ssh_placeholder_is_replaced_with_configured_key_path(self):
        terraform_code = '''
variable "key_name" {
  default = ""
}

resource "aws_instance" "app_server" {
  key_name = var.key_name
}

output "ssh_command" { value = "ssh -i <your-key.pem> ec2-user@${aws_instance.app_server.public_ip}" }
'''

        # AWS_SSH_PRIVATE_KEY_PATH has no machine-specific default (it must be
        # configured per-deployment via .env), so patch a deterministic value
        # for this test rather than depending on the empty default.
        with patch(
            "app.controllers.aws_deploy_controller.settings.AWS_SSH_PRIVATE_KEY_PATH",
            "/test/keys/aws-deployment-devops.pem",
        ):
            result = _enforce_ssh_key_settings(terraform_code)

        self.assertIn('default     = "aws-deployment-devops"', result)
        self.assertIn('variable "ssh_private_key_path"', result)
        self.assertIn('/test/keys/aws-deployment-devops.pem', result)
        self.assertIn("ssh -i ${var.ssh_private_key_path}", result)
        self.assertNotIn("<your-key.pem>", result)

    def test_ssh_placeholder_in_comment_does_not_fail_validation(self):
        terraform_code = '''
output "ssh_command" {
  value = "ssh -i ${var.ssh_private_key_path} ec2-user@${aws_instance.app_server.public_ip}"
}

# ssh -i <key.pem> ec2-user@<public_ip>
'''

        _validate_ssh_command_output(terraform_code)


class TestAWSOutputUrlPortPostProcessing(unittest.TestCase):
    def test_frontend_and_backend_outputs_use_compose_host_ports(self):
        terraform_code = '''
resource "aws_instance" "web" {
  user_data = <<-EOF
cat > /home/ec2-user/app/docker-compose.yml << 'COMPOSE'
services:
  frontend:
    image: abdulahad2242/devops-autopilot-mern_notes_app-frontend:latest
    ports:
      - 5173:80
  server:
    image: abdulahad2242/devops-autopilot-mern_notes_app-server:latest
    ports:
      - "5000:5000"
  mongodb:
    image: mongo:latest
    ports:
      - "27017:27017"
COMPOSE
EOF
}

output "frontend_url" {
  value = "http://${aws_instance.web.public_ip}"
}

output "backend_url" {
  value = "http://${aws_instance.web.public_ip}:${var.app_port}"
}
'''

        result = _ensure_output_urls_include_compose_ports(terraform_code)

        self.assertIn(
            'value = "http://${aws_instance.web.public_ip}:5173"',
            result,
        )
        self.assertIn(
            'value = "http://${aws_instance.web.public_ip}:5000"',
            result,
        )
        self.assertNotIn("public_ip}:27017", result)


class TestAWSComposeImageNormalization(unittest.TestCase):
    def test_builds_same_image_repo_base_as_docker_push(self):
        image_repo = build_project_image_repo("mern_notes_app", "abdulahad2242", "devops-autopilot")

        self.assertEqual(image_repo, "abdulahad2242/devops-autopilot-mern_notes_app")
        self.assertEqual(
            build_service_image(image_repo, "server"),
            "abdulahad2242/devops-autopilot-mern_notes_app-server:latest",
        )

    def test_rewrites_app_images_and_removes_build_for_ec2_pull(self):
        compose = '''
services:
  server:
    image: mern_notes_app_server:latest
    build: ./server
    ports:
      - "5000:5000"
  client:
    build:
      context: ./client
    ports:
      - "3000:80"
  mongo:
    image: mongo:latest
    ports:
      - "27017:27017"
'''

        result = _normalize_compose_images_for_aws(
            compose,
            "abdulahad2242/devops-autopilot-mern-notes-app",
        )

        self.assertIn("image: abdulahad2242/devops-autopilot-mern-notes-app-server:latest", result)
        self.assertIn("image: abdulahad2242/devops-autopilot-mern-notes-app-client:latest", result)
        self.assertIn("image: mongo:latest", result)
        self.assertNotIn("build:", result)


class TestAWSDockerHubManifestValidation(unittest.TestCase):
    def test_expected_images_skip_database_services(self):
        compose = '''
services:
  server:
    image: abdulahad2242/devops-autopilot-mern_notes_app-server:latest
  mongo:
    image: mongo:latest
'''

        result = _expected_aws_app_images(compose, [], "abdulahad2242/devops-autopilot-mern_notes_app")

        self.assertEqual(result, ["abdulahad2242/devops-autopilot-mern_notes_app-server:latest"])

    @patch("app.controllers.aws_deploy_controller.subprocess.run")
    def test_missing_manifest_blocks_aws_generation(self, mock_run):
        class Result:
            returncode = 1
            stderr = "manifest unknown"

        mock_run.return_value = Result()

        with self.assertRaises(Exception) as ctx:
            _validate_docker_hub_manifests_exist([
                "abdulahad2242/devops-autopilot-mern_notes_app-server:latest"
            ])

        self.assertIn("manifest not found", str(ctx.exception))


class TestAWSTerraformPromptImageNames(unittest.TestCase):
    def test_prompt_uses_resolved_pushed_image_repo(self):
        message = build_terraform_message(
            project_name="mern_notes_app",
            services=[{"name": "server", "port": 5000, "type": "backend"}],
            docker_repo_prefix="abdulahad2242",
            image_repo="abdulahad2242/devops-autopilot-mern_notes_app",
        )

        self.assertIn(
            "Image: abdulahad2242/devops-autopilot-mern_notes_app-server:latest",
            message,
        )
        self.assertNotIn("Image: abdulahad2242/mern_notes_app-server:latest", message)


if __name__ == "__main__":
    unittest.main()
