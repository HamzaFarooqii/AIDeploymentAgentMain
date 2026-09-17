"""
AWS Deployment Routes - API endpoints for AWS EC2 (Free Tier) docker-compose deployment.

Provides endpoints for:
- Generating Terraform configurations
- Applying/destroying infrastructure
- Scaling services
- Getting deployment status
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import Dict, Optional, Any

from ..controllers.aws_deploy_controller import (
    check_aws_prerequisites,
    generate_terraform_handler,
    apply_terraform_handler,
    destroy_terraform_handler,
    scale_to_zero_handler,
    scale_up_handler,
    get_aws_status_handler,
)
from ..utils.auth import get_current_active_user

router = APIRouter(prefix="/api/aws", tags=["AWS Deployment"])


# ============ Request Models ============

class AWSConfig(BaseModel):
    """Configuration for AWS deployment."""
    aws_region: str = "us-east-1"
    docker_repo_prefix: str
    db_engine: Optional[str] = None  # mongo, postgres, mysql, none
    mongo_db_url: Optional[str] = None
    rds_db_url: Optional[str] = None
    desired_count: int = 1
    extra_env: Optional[Dict[str, str]] = None
    # EC2 access / sizing
    key_name: str = "aws-deployment-devops" # EC2 key pair name for SSH
    ssh_private_key_path: str = ""  # Local path to the EC2 SSH private key (.pem); falls back to settings.AWS_SSH_PRIVATE_KEY_PATH when unset
    allowed_ssh_cidr: str = "0.0.0.0/0"    # CIDR allowed to SSH; restrict to your IP in production
    app_port: Optional[int] = None          # Primary app port; auto-detected from services when None
    root_volume_size: int = 20              # Root EBS volume GB (min 20 for Docker workloads)


class ApplyConfig(BaseModel):
    """Configuration for terraform apply."""
    scale_to_zero: bool = False
    desired_count: int = 1


# ============ Routes ============

@router.get("/{project_id}/prerequisites")
async def check_prerequisites(
    project_id: str,
    current_user: dict = Depends(get_current_active_user)
):
    """
    Check if the project is ready for AWS deployment.
    
    Returns:
        - can_deploy: True if all prerequisites are met
        - issues: List of missing prerequisites
        - project_name: Name of the project
        - docker_push_success: Whether Docker images are pushed
    """
    return await check_aws_prerequisites(project_id, current_user)


@router.post("/{project_id}/generate")
async def generate_terraform(
    project_id: str,
    config: AWSConfig,
    current_user: dict = Depends(get_current_active_user)
):
    """
    Generate Terraform configuration using LLM.
    
    The LLM analyzes the project metadata and generates a complete
    main.tf file for deploying to AWS EC2 (Free Tier) with docker-compose.
    
    Request body:
        - aws_region: AWS region (default: us-east-1)
        - docker_repo_prefix: Docker Hub username or ECR repo prefix
        - db_engine: Database type (mongo, postgres, mysql, none)
        - mongo_db_url: MongoDB connection string (if applicable)
        - rds_db_url: RDS connection string (if applicable)
        - desired_count: Number of Fargate tasks per service
        - extra_env: Additional environment variables to inject
    
    Returns:
        - status: "generated"
        - terraform_path: Path to generated main.tf
        - message: Success message
    """
    return await generate_terraform_handler(
        project_id,
        current_user,
        config.model_dump()
    )


@router.post("/{project_id}/apply")
async def apply_terraform(
    project_id: str,
    config: Optional[ApplyConfig] = None,
    current_user: dict = Depends(get_current_active_user)
):
    """
    Apply Terraform configuration to deploy infrastructure.
    
    Returns a streaming response (SSE) with real-time progress updates.
    
    Request body (optional):
        - scale_to_zero: If true, sets desired_count to 0
        - desired_count: Number of Fargate tasks (ignored if scale_to_zero)
    
    SSE Events:
        - type: "info" | "warning" | "error" | "success" | "complete"
        - message: Log line from Terraform
        - stage: "init" | "apply"
        - exit_code: Exit code (on completion)
        - outputs: Terraform outputs (on success)
    """
    # For the EC2/docker-compose flow we don't pass extra variables by default
    return await apply_terraform_handler(project_id, current_user, variables=None)


@router.post("/{project_id}/destroy")
async def destroy_terraform(
    project_id: str,
    current_user: dict = Depends(get_current_active_user)
):
    """
    Destroy all AWS infrastructure created by Terraform.
    
    ⚠️ WARNING: This will permanently delete all AWS resources!
    
    Returns a streaming response (SSE) with real-time progress updates.
    
    SSE Events:
        - type: "info" | "warning" | "error" | "success" | "complete"
        - message: Log line from Terraform
        - stage: "destroy"
        - exit_code: Exit code (on completion)
    """
    return await destroy_terraform_handler(project_id, current_user)


@router.post("/{project_id}/scale-zero")
async def scale_to_zero(
    project_id: str,
    current_user: dict = Depends(get_current_active_user)
):
    """
    Stop the EC2 instance (cost saving). Use /apply to recreate/start as needed.
    
    Returns a streaming response (SSE) with progress updates.
    """
    return await scale_to_zero_handler(project_id, current_user)


@router.post("/{project_id}/scale-up")
async def scale_up(
    project_id: str,
    current_user: dict = Depends(get_current_active_user)
):
    """
    Start a stopped EC2 instance.

    Returns a streaming response (SSE) with progress updates.
    """
    return await scale_up_handler(project_id, current_user)


@router.get("/{project_id}/status")
async def get_aws_status(
    project_id: str,
    current_user: dict = Depends(get_current_active_user)
):
    """
    Get current AWS deployment status.
    
    Returns:
        - aws_deployment_status: not_deployed | terraform_generated | deploying | deployed | failed
        - aws_region: Deployed region
        - aws_frontend_url: Public URL if deployed
        - aws_last_deployed: Last deployment timestamp
        - docker_push_success: Whether Docker images are ready
        - live_frontend_url/live_backend_url: Current URLs from Terraform state
        - live_vpc_id: VPC ID
    """
    return await get_aws_status_handler(project_id, current_user)


class FixRequest(BaseModel):
    """Request body for terraform fix."""
    error_output: str


@router.post("/{project_id}/fix")
async def fix_terraform(
    project_id: str,
    fix_request: FixRequest,
    current_user: dict = Depends(get_current_active_user)
):
    """
    Fix Terraform errors using LLM.
    
    Sends the current Terraform code and error output to the LLM
    to generate a corrected version.
    
    Request body:
        - error_output: The error message from terraform plan/apply
    
    Returns:
        - status: "fixed"
        - message: Success message
    """
    from ..controllers.aws_deploy_controller import fix_terraform_handler
    return await fix_terraform_handler(
        project_id,
        current_user,
        fix_request.error_output
    )

