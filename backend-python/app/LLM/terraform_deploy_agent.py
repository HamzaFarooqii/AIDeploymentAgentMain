"""
Terraform Deploy Agent - LLM-powered infrastructure generation for AWS EC2 Free Tier.

This agent generates Terraform configurations for deploying MERN projects to AWS
on a single EC2 instance using docker-compose (no ALB/ELB, no ECS).
"""

import os
from typing import Dict, List, Optional

from .llm_client import call_gemini, call_gemini_stream
from ..config.settings import settings
from ..utils.image_naming import build_service_image

# System prompt for Terraform generation - EC2 Free Tier Version
TERRAFORM_DEPLOY_SYSTEM_PROMPT = """You generate Terraform for deploying Docker containers to AWS EC2 (Free Tier).

## TASK
Generate a complete `main.tf` that deploys services to a single EC2 instance.

## CRITICAL RULES (MUST FOLLOW)
1. ALWAYS include: terraform { required_providers { aws = { source = "hashicorp/aws", version = "~> 5.0" } } }
2. ALWAYS include: provider "aws" { region = var.aws_region }
3. ALWAYS use data "aws_ami" to get Amazon Linux 2023 AMI - NEVER hardcode AMI IDs
4. Declare ALL variables provided in the input with their supplied defaults: project_name, aws_region, docker_repo_prefix, key_name, ssh_private_key_path, allowed_ssh_cidr, app_port, root_volume_size
5. Use vpc_security_group_ids = [aws_security_group.X.id] NOT security_groups
6. Add map_public_ip_on_launch = true to subnet
7. ALWAYS include key_name = var.key_name in aws_instance (NEVER omit this)
8. For availability_zone use: "${var.aws_region}a"
9. In user_data heredoc, use 'COMPOSE' for inner delimiter
10. HARDCODE all values in docker-compose.yml - NO variable interpolation
11. Include docker login using only DOCKER_USERNAME and DOCKER_PASSWORD placeholders; the backend replaces them before writing main.tf
12. NEVER use single-line block syntax when a block has multiple arguments. ALL variable, ingress, egress, and root_block_device blocks MUST use multi-line syntax with one argument per line. Single-line blocks with more than one argument are a Terraform syntax error.
13. NEVER output `<your-key.pem>`. The ssh_command output MUST use the supplied SSH_PRIVATE_KEY_PATH exactly.

## REQUIRED STRUCTURE (in this order)
1. terraform block with required_providers
2. provider "aws" block
3-9. Variables - MUST be multi-line when they have more than one argument:
```
variable "project_name" {
  default = "..."
}
variable "aws_region" {
  default = "..."
}
variable "docker_repo_prefix" {
  default = "..."
}
variable "key_name" {
  description = "EC2 key pair name for SSH access"
  default     = "aws-deployment-devops"
}
variable "ssh_private_key_path" {
  description = "Local path to the EC2 private key PEM file"
  default     = "/path/to/your-key.pem"
}
variable "allowed_ssh_cidr" {
  description = "CIDR block allowed to SSH"
  default     = "0.0.0.0/0"
}
variable "app_port" {
  description = "Primary application port"
  default     = 3000
}
variable "root_volume_size" {
  description = "Root EBS volume size in GB"
  default     = 20
}
```
10. data "aws_ami" for Amazon Linux 2023
11. aws_vpc, aws_internet_gateway, aws_subnet (map_public_ip_on_launch = true), aws_route_table, aws_route_table_association
12. aws_security_group - each ingress/egress block MUST be multi-line:
```
  ingress {
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = [var.allowed_ssh_cidr]
  }
  ingress {
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
```
Also add ingress for port 443, var.app_port, and every host-side port from docker-compose ports mappings.
13. aws_instance with: instance_type = provided EC2_INSTANCE_TYPE, ami = data.aws_ami.amazon_linux.id, vpc_security_group_ids, key_name = var.key_name, and multi-line root_block_device:
```
  root_block_device {
    volume_type           = "gp3"
    volume_size           = var.root_volume_size
    delete_on_termination = true
  }
```
14. outputs

## EXACT user_data FORMAT
```
user_data = <<-EOF
#!/bin/bash
exec > >(tee /var/log/devops-autopilot-userdata.log | logger -t devops-autopilot-userdata 2>&1) 2>&1
echo "=== DevOps AutoPilot user_data start: $(date) ==="
dnf update -y
dnf install -y docker
systemctl start docker
systemctl enable docker
usermod -a -G docker ec2-user
curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
chmod +x /usr/local/bin/docker-compose
echo "DOCKER_PASSWORD" | docker login -u DOCKER_USERNAME --password-stdin
mkdir -p /home/ec2-user/app
cat > /home/ec2-user/app/docker-compose.yml << 'COMPOSE'
services:
  backend:
    image: username/project-backend:latest
    ports:
      - "3000:3000"
    environment:
      - PORT=3000
      - MONGO_URI=mongodb://...
    restart: always
COMPOSE
cd /home/ec2-user/app
docker-compose pull
docker-compose up -d
echo "=== DevOps AutoPilot user_data complete: $(date) ==="
EOF
```

## REQUIRED OUTPUTS (ALL must be present)
Output all of these, no exceptions:
- instance_public_ip    (value = aws_instance.X.public_ip)
- public_dns            (value = aws_instance.X.public_dns)
- instance_id           (value = aws_instance.X.id)
- vpc_id                (value = aws_vpc.X.id)
- app_url               (value = "http://${aws_instance.X.public_ip}:${var.app_port}")
- frontend_url          (value = "http://${aws_instance.X.public_ip}")
- backend_url           (value = "http://${aws_instance.X.public_ip}:${var.app_port}")
- ssh_command           (value = "ssh -i ${var.ssh_private_key_path} ec2-user@${aws_instance.X.public_ip}")

## IAM PERMISSIONS REQUIRED (include as comments at the top of main.tf)
# Required IAM permissions for the deploying user/role:
# ec2:RunInstances, ec2:TerminateInstances, ec2:StartInstances, ec2:StopInstances
# ec2:CreateVpc, ec2:DeleteVpc, ec2:CreateSubnet, ec2:DeleteSubnet
# ec2:CreateSecurityGroup, ec2:DeleteSecurityGroup
# ec2:AuthorizeSecurityGroupIngress, ec2:AuthorizeSecurityGroupEgress
# ec2:RevokeSecurityGroupIngress, ec2:RevokeSecurityGroupEgress
# ec2:CreateInternetGateway, ec2:AttachInternetGateway, ec2:DetachInternetGateway, ec2:DeleteInternetGateway
# ec2:CreateRouteTable, ec2:DeleteRouteTable, ec2:CreateRoute, ec2:AssociateRouteTable, ec2:DisassociateRouteTable
# ec2:Describe* (ec2:DescribeInstances, ec2:DescribeVpcs, ec2:DescribeSubnets, ec2:DescribeSecurityGroups, etc.)
# ec2:AllocateAddress, ec2:ReleaseAddress, ec2:AssociateAddress, ec2:DisassociateAddress (if using Elastic IPs)
# iam:PassRole (only if attaching an instance profile to the EC2 instance)

## DEBUG COMMANDS (include as comments at the bottom of main.tf)
# After deployment, use these commands to debug:
# terraform output                                          - Show all outputs
# ssh -i /path/to/your-key.pem ec2-user@<public_ip> - SSH into instance
# curl http://<public_ip>:<app_port>                       - Test app endpoint
# sudo docker ps                                           - List running containers
# sudo docker logs <container_name>                        - View container logs
# sudo docker compose -f /home/ec2-user/app/docker-compose.yml logs  - View all service logs
# sudo ss -tulpn                                           - Check listening ports
# sudo df -h                                               - Check disk usage
# sudo docker system df                                    - Check Docker disk usage
# sudo cat /var/log/cloud-init-output.log                 - View cloud-init log
# sudo cat /var/log/devops-autopilot-userdata.log          - View app setup log

Return ONLY valid Terraform HCL. No markdown. No explanations."""


def build_terraform_message(
    project_name: str,
    services: List[Dict],
    docker_repo_prefix: str,
    aws_region: str = "us-east-1",
    db_engine: Optional[str] = None,
    db_url: Optional[str] = None,
    desired_count: int = 1,
    service_env_vars: Optional[Dict[str, Dict[str, str]]] = None,
    existing_compose: Optional[str] = None,
    existing_env_files: Optional[Dict[str, str]] = None,
    image_repo: Optional[str] = None,
    key_name: str = settings.AWS_EC2_KEY_NAME,
    ssh_private_key_path: str = settings.AWS_SSH_PRIVATE_KEY_PATH,
    allowed_ssh_cidr: str = "0.0.0.0/0",
    app_port: Optional[int] = None,
    root_volume_size: int = 20,
) -> str:
    """
    Build the message for LLM with project metadata for Terraform generation.
    
    Args:
        project_name: Name of the project (used for AWS resource naming)
        services: List of dicts with {name, port, path} for each service
        docker_repo_prefix: Docker Hub/ECR prefix (e.g., "username")
        aws_region: AWS region for deployment
        db_engine: Database type (mongo, postgres, mysql, none)
        db_url: Database connection URL
        desired_count: Number of Fargate tasks per service
        service_env_vars: Dict mapping service name to its env vars
            e.g., {"backend": {"PORT": "3000", "MONGO_URI": "..."}, "frontend": {"VITE_API_URL": "..."}}
    """
    # Derive app_port from first non-frontend service if not provided
    _app_port = app_port
    if not _app_port:
        for svc in services:
            if svc.get("type", "") not in ("frontend",):
                _app_port = svc.get("port", 3000)
                break
        if not _app_port:
            _app_port = services[0].get("port", 3000) if services else 3000

    lines = [
        "=== TERRAFORM GENERATION REQUEST ===",
        "",
        f"PROJECT_NAME: {project_name}",
        f"AWS_REGION: {aws_region}",
        f"EC2_INSTANCE_TYPE: {settings.AWS_EC2_INSTANCE_TYPE}",
        f"DOCKER_REPO_PREFIX: {docker_repo_prefix}",
        f"DESIRED_COUNT: {desired_count}",
        f"KEY_NAME: {key_name or settings.AWS_EC2_KEY_NAME}",
        f"SSH_PRIVATE_KEY_PATH: {ssh_private_key_path or settings.AWS_SSH_PRIVATE_KEY_PATH}",
        f"ALLOWED_SSH_CIDR: {allowed_ssh_cidr}",
        f"APP_PORT: {_app_port}",
        f"ROOT_VOLUME_SIZE: {root_volume_size}",
        "",
        f"DB_ENGINE: {db_engine or 'none'}",
        f"DB_URL: {db_url or 'NOT_SET'}",
        "",
        "=== SERVICES ===",
    ]
    
    for svc in services:
        svc_name = svc.get("name", "app")
        svc_port = svc.get("port", 3000)
        svc_type = svc.get("type", "backend")
        svc_image = (
            build_service_image(image_repo, str(svc_name))
            if image_repo
            else f"{docker_repo_prefix}/{project_name}-{svc_name}:latest"
        )
        
        lines.append(f"\nService: {svc_name}")
        lines.append(f"  Type: {svc_type}")
        lines.append(f"  Port: {svc_port}")
        lines.append(f"  Image: {svc_image}")
        
        # Add service-specific environment variables
        if service_env_vars and svc_name in service_env_vars:
            env_dict = service_env_vars[svc_name]
            if env_dict:
                lines.append(f"  Environment Variables:")
                for key, value in env_dict.items():
                    # Pass through actual values so docker-compose can be fully populated
                    lines.append(f"    - {key}={value}")
    
    # Add existing docker-compose.yml if provided
    if existing_compose:
        lines.extend([
            "",
            "=== EXISTING DOCKER-COMPOSE.YML ===",
            "Use this docker-compose.yml content EXACTLY in the user_data:",
            existing_compose,
        ])
    
    # Add existing .env files if provided
    if existing_env_files:
        lines.extend([
            "",
            "=== EXISTING .ENV FILES ===",
            "Embed these .env file contents in user_data BEFORE docker-compose.yml:",
        ])
        for svc_name, env_content in existing_env_files.items():
            lines.append(f"\n--- {svc_name}/.env ---")
            lines.append(env_content)
    
    lines.extend([
        "",
        "=== INSTRUCTIONS ===",
        "Generate a complete main.tf file with:",
        "1. VPC, Subnets, Security Groups",
        f"2. Single EC2 {settings.AWS_EC2_INSTANCE_TYPE} running docker-compose (no ALB/ELB, no ECS)",
    ])
    
    if existing_compose:
        lines.extend([
            "3. In user_data, first create .env files (if provided), then create docker-compose.yml using the EXACT content provided above",
            "4. Do NOT generate a new docker-compose.yml - use the provided one exactly",
        ])
    else:
        lines.extend([
            "3. Inject ALL provided environment variables into docker-compose exactly as given (no placeholders or guesses)",
            "4. Frontend must map to port 80; backend maps to its provided port",
        ])
    
    lines.extend([
        "5. Outputs: instance_public_ip, public_dns, instance_id, vpc_id, app_url, frontend_url, backend_url, ssh_command",
        "6. ssh_command MUST use SSH_PRIVATE_KEY_PATH exactly; never use <your-key.pem>.",
        "7. Security group must allow every host-side port used in docker-compose.yml ports mappings.",
        "8. Do not map the same host-side port to more than one service.",
        "9. Use ONLY the values provided above; do not assume or invent any missing fields.",
        "",
        "Return ONLY the Terraform HCL code, no explanations.",
    ])
    
    return "\n".join(lines)


def run_terraform_deploy_chat(
    project_name: str,
    services: List[Dict],
    docker_repo_prefix: str,
    aws_region: str = "us-east-1",
    db_engine: Optional[str] = None,
    db_url: Optional[str] = None,
    desired_count: int = 1,
    service_env_vars: Optional[Dict[str, Dict[str, str]]] = None,
    existing_compose: Optional[str] = None,
    existing_env_files: Optional[Dict[str, str]] = None,
    image_repo: Optional[str] = None,
    key_name: str = settings.AWS_EC2_KEY_NAME,
    ssh_private_key_path: str = settings.AWS_SSH_PRIVATE_KEY_PATH,
    allowed_ssh_cidr: str = "0.0.0.0/0",
    app_port: Optional[int] = None,
    root_volume_size: int = 20,
) -> str:
    """
    Generate Terraform configuration via LLM.

    Returns:
        The generated Terraform HCL code as a string.
    """
    message = build_terraform_message(
        project_name=project_name,
        services=services,
        docker_repo_prefix=docker_repo_prefix,
        aws_region=aws_region,
        db_engine=db_engine,
        db_url=db_url,
        desired_count=desired_count,
        service_env_vars=service_env_vars,
        existing_compose=existing_compose,
        existing_env_files=existing_env_files,
        image_repo=image_repo,
        key_name=key_name,
        ssh_private_key_path=ssh_private_key_path,
        allowed_ssh_cidr=allowed_ssh_cidr,
        app_port=app_port,
        root_volume_size=root_volume_size,
    )
    
    # Debug output
    print(f"\n=== DEBUG: TERRAFORM LLM REQUEST ===")
    print(f"Project: {project_name}")
    print(f"Services: {[s.get('name') for s in services]}")
    print(f"Region: {aws_region}")
    print(f"Message (first 500 chars):\n{message[:500]}...")
    print("=" * 40)
    
    response = call_gemini([
        {"role": "system", "content": TERRAFORM_DEPLOY_SYSTEM_PROMPT},
        {"role": "user", "content": message},
    ])
    
    # Extract HCL from response (remove markdown fences if present)
    return _extract_hcl_from_response(response)


def run_terraform_deploy_chat_stream(
    project_name: str,
    services: List[Dict],
    docker_repo_prefix: str,
    aws_region: str = "us-east-1",
    db_engine: Optional[str] = None,
    db_url: Optional[str] = None,
    desired_count: int = 1,
    service_env_vars: Optional[Dict[str, Dict[str, str]]] = None,
    image_repo: Optional[str] = None,
    key_name: str = settings.AWS_EC2_KEY_NAME,
    ssh_private_key_path: str = settings.AWS_SSH_PRIVATE_KEY_PATH,
    allowed_ssh_cidr: str = "0.0.0.0/0",
    app_port: Optional[int] = None,
    root_volume_size: int = 20,
):
    """
    Streaming version of terraform generation.
    Yields tokens as they're generated by the LLM.
    """
    message = build_terraform_message(
        project_name=project_name,
        services=services,
        docker_repo_prefix=docker_repo_prefix,
        aws_region=aws_region,
        db_engine=db_engine,
        db_url=db_url,
        desired_count=desired_count,
        service_env_vars=service_env_vars,
        image_repo=image_repo,
        key_name=key_name,
        ssh_private_key_path=ssh_private_key_path,
        allowed_ssh_cidr=allowed_ssh_cidr,
        app_port=app_port,
        root_volume_size=root_volume_size,
    )
    
    print(f"\n=== DEBUG: STREAMING TERRAFORM REQUEST ===")
    print(f"Project: {project_name}")
    print(f"Services: {[s.get('name') for s in services]}")
    
    for chunk in call_gemini_stream([
        {"role": "system", "content": TERRAFORM_DEPLOY_SYSTEM_PROMPT},
        {"role": "user", "content": message},
    ]):
        yield chunk


def _extract_hcl_from_response(response: str) -> str:
    """
    Extract HCL code from LLM response.
    Removes markdown code fences if present.
    """
    if not response:
        return ""
    
    # Check for markdown code blocks
    if "```" in response:
        # Find content between code fences
        import re
        # Match ```hcl, ```terraform, or just ```
        pattern = r"```(?:hcl|terraform)?\s*\n(.*?)```"
        match = re.search(pattern, response, re.DOTALL)
        if match:
            return match.group(1).strip()
    
    # No code fences, return as-is (trimmed)
    return response.strip()


def get_service_env_vars_for_terraform(
    project_path: str,
    services: List[Dict],
) -> Dict[str, Dict[str, str]]:
    """
    Get environment variables for each service from their .env files.
    Uses the existing _read_env_key_values function from detector.py.
    
    Args:
        project_path: Root path of the project
        services: List of service dicts with 'name' and 'path' keys
    
    Returns:
        Dict mapping service name to its env vars
        e.g., {"backend": {"PORT": "3000", "MONGO_URI": "..."}}
    """
    from ..utils.detector import _read_env_key_values
    
    service_envs = {}
    
    for svc in services:
        svc_name = svc.get("name", "app")
        svc_path = svc.get("path", "")
        
        # Construct full path to service directory
        if svc_path:
            full_path = os.path.join(project_path, svc_path)
        else:
            full_path = project_path
        
        # Read env vars from this service's directory
        if os.path.exists(full_path):
            env_vars = _read_env_key_values(full_path)
            if env_vars:
                service_envs[svc_name] = env_vars
                print(f"📦 Loaded {len(env_vars)} env vars for {svc_name} from {full_path}")
    
    return service_envs


# --- TERRAFORM ERROR FIXING ---

TERRAFORM_FIX_SYSTEM_PROMPT = """You are a Terraform expert. A Terraform configuration has errors.

## YOUR TASK
Fix the Terraform HCL code based on the error output.

## RULES
1. Only fix the specific errors shown
2. Keep all other resources unchanged
3. Return the COMPLETE fixed main.tf file
4. Do NOT add explanations, only return HCL code
5. Common fixes include:
   - Missing required arguments
   - Invalid resource references
   - Syntax errors
   - Wrong attribute names (e.g., instance_type vs type)
   - Missing depends_on
   - Invalid ARN references

## OUTPUT FORMAT
Return ONLY the complete, fixed Terraform HCL code. No markdown, no explanations."""


def fix_terraform_error(
    current_terraform: str,
    error_output: str,
    project_name: str = "app",
) -> str:
    """
    Fix Terraform errors using LLM.
    
    Args:
        current_terraform: The current main.tf content that has errors
        error_output: The error output from terraform plan/apply
        project_name: Project name for context
    
    Returns:
        Fixed Terraform HCL code
    """
    message = f"""=== TERRAFORM FIX REQUEST ===

PROJECT: {project_name}

=== ERROR OUTPUT ===
{error_output[-3000:]}

=== CURRENT TERRAFORM CODE ===
{current_terraform}

=== INSTRUCTIONS ===
Fix the errors above and return the complete, corrected main.tf file.
Return ONLY the HCL code, no explanations."""

    print(f"\n=== DEBUG: TERRAFORM FIX REQUEST ===")
    print(f"Project: {project_name}")
    print(f"Error (first 500 chars): {error_output[:500]}...")
    print("=" * 40)
    
    response = call_gemini([
        {"role": "system", "content": TERRAFORM_FIX_SYSTEM_PROMPT},
        {"role": "user", "content": message},
    ])
    
    return _extract_hcl_from_response(response)


def fix_terraform_error_stream(
    current_terraform: str,
    error_output: str,
    project_name: str = "app",
):
    """
    Streaming version of Terraform error fixing.
    Yields tokens as they're generated.
    """
    message = f"""=== TERRAFORM FIX REQUEST ===

PROJECT: {project_name}

=== ERROR OUTPUT ===
{error_output[-3000:]}

=== CURRENT TERRAFORM CODE ===
{current_terraform}

=== INSTRUCTIONS ===
Fix the errors above and return the complete, corrected main.tf file.
Return ONLY the HCL code, no explanations."""

    print(f"\n=== DEBUG: STREAMING TERRAFORM FIX ===")
    print(f"Project: {project_name}")
    
    for chunk in call_gemini_stream([
        {"role": "system", "content": TERRAFORM_FIX_SYSTEM_PROMPT},
        {"role": "user", "content": message},
    ]):
        yield chunk

