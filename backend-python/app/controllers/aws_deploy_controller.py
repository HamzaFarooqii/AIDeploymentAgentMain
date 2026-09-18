"""
AWS Deployment Controller - Handles API requests for AWS EC2 (Free Tier) docker-compose deployment.

Provides handlers for:
- Generating Terraform configurations via LLM
- Applying Terraform to deploy infrastructure
- Destroying deployed infrastructure
- Scaling services to zero (cost control)
- Getting deployment status
"""

import os
import re
import subprocess
from datetime import datetime
from typing import Dict, List, Optional, Any

import yaml
from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool
from bson import ObjectId

from ..config.database import get_projects_collection
from ..config.settings import settings
from ..LLM.terraform_deploy_agent import (
    run_terraform_deploy_chat,
    run_terraform_deploy_chat_stream,
    get_service_env_vars_for_terraform,
    fix_terraform_error,
)
from ..services.aws_service import AWSDeploymentService, verify_aws_credentials
from ..utils.detector import find_project_root
from ..utils.image_naming import build_project_image_repo, build_service_image


_COMPOSE_DB_TOKENS = {"mongo", "postgres", "postgresql", "mysql", "mariadb", "redis", "sqlite", "database", "db"}


def _shell_single_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def _is_database_compose_service(service_name: str, image_name: str) -> bool:
    combined = f"{service_name} {image_name}".lower()
    return any(token in combined for token in _COMPOSE_DB_TOKENS)


def _normalize_compose_images_for_aws(compose_content: str, image_repo: str) -> str:
    """
    Make EC2 pull the same non-database service images pushed by Docker deploy.

    The push path publishes compose services as `{image_repo}-{service}:latest`.
    AWS user_data only receives docker-compose.yml, so build-only app services
    must become image-only pull targets.
    """
    if not compose_content or not image_repo:
        return compose_content

    try:
        compose_data = yaml.safe_load(compose_content) or {}
    except yaml.YAMLError:
        return compose_content

    if not isinstance(compose_data, dict):
        return compose_content
    services = compose_data.get("services")
    if not isinstance(services, dict):
        return compose_content

    changed = False
    for svc_name, svc in services.items():
        if not isinstance(svc, dict):
            continue
        current_image = str(svc.get("image") or "")
        if _is_database_compose_service(str(svc_name), current_image):
            continue

        target_image = build_service_image(image_repo, str(svc_name))
        if svc.get("image") != target_image:
            svc["image"] = target_image
            changed = True
        if "build" in svc:
            del svc["build"]
            changed = True

    if not changed:
        return compose_content
    return yaml.safe_dump(compose_data, sort_keys=False)


def _expected_aws_app_images(
    compose_content: Optional[str],
    services: List[Dict],
    image_repo: str,
) -> List[str]:
    images: List[str] = []

    if compose_content:
        try:
            compose_data = yaml.safe_load(compose_content) or {}
        except yaml.YAMLError:
            compose_data = {}
        compose_services = compose_data.get("services") if isinstance(compose_data, dict) else None
        if isinstance(compose_services, dict):
            for svc_name, svc in compose_services.items():
                if not isinstance(svc, dict):
                    continue
                image_name = str(svc.get("image") or "").strip()
                if image_name and not _is_database_compose_service(str(svc_name), image_name):
                    images.append(image_name)

    if not images and image_repo:
        for svc in services:
            if str(svc.get("type", "")).lower() == "database":
                continue
            svc_name = str(svc.get("name") or "app").lower()
            images.append(build_service_image(image_repo, svc_name))

    return list(dict.fromkeys(images))


def _validate_docker_hub_manifests_exist(images: List[str]) -> None:
    missing: List[str] = []
    for image in images:
        try:
            proc = subprocess.run(
                ["docker", "manifest", "inspect", image],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
            )
        except OSError as exc:
            raise HTTPException(
                status_code=400,
                detail=f"Unable to inspect Docker Hub image manifests: {exc}",
            ) from exc
        if proc.returncode != 0:
            missing.append(image)

    if missing:
        raise HTTPException(
            status_code=400,
            detail=(
                "Docker Hub image manifest not found for AWS deployment: "
                + ", ".join(missing)
                + ". Run PUSH_IMAGE successfully before generating or applying AWS infrastructure."
            ),
        )


def _inject_docker_credentials(terraform_code: str) -> str:
    """
    Replace Gemini's Docker login placeholders with configured Docker Hub credentials.
    Only touches generated docker login lines/placeholders before main.tf is written.
    """
    username = settings.DOCKER_HUB_USERNAME or os.getenv("DOCKER_HUB_USERNAME")
    password = settings.DOCKER_HUB_PASSWORD or os.getenv("DOCKER_HUB_PASSWORD")
    has_placeholders = "DOCKER_USERNAME" in terraform_code or "DOCKER_PASSWORD" in terraform_code

    if has_placeholders and not (username and password):
        raise HTTPException(
            status_code=400,
            detail="Docker Hub credentials are required to replace Terraform docker login placeholders.",
        )
    if not (username and password):
        return terraform_code

    login_line = (
        f"printf '%s\\n' {_shell_single_quote(password)} "
        f"| docker login -u {_shell_single_quote(username)} --password-stdin"
    )
    terraform_code = re.sub(
        r"^\s*echo\s+[\"']?DOCKER_PASSWORD[\"']?\s*\|\s*docker\s+login\s+-u\s+[\"']?DOCKER_USERNAME[\"']?\s+--password-stdin\s*$",
        login_line,
        terraform_code,
        flags=re.MULTILINE,
    )
    return (
        terraform_code
        .replace('"DOCKER_PASSWORD"', _shell_single_quote(password))
        .replace("'DOCKER_PASSWORD'", _shell_single_quote(password))
        .replace("DOCKER_PASSWORD", _shell_single_quote(password))
        .replace('"DOCKER_USERNAME"', _shell_single_quote(username))
        .replace("'DOCKER_USERNAME'", _shell_single_quote(username))
        .replace("DOCKER_USERNAME", _shell_single_quote(username))
    )


def _enforce_ec2_instance_type(terraform_code: str) -> str:
    instance_type = getattr(settings, "AWS_EC2_INSTANCE_TYPE", "t3.micro") or "t3.micro"
    return re.sub(
        r'instance_type\s*=\s*"[^"]+"',
        f'instance_type = "{instance_type}"',
        terraform_code,
        count=1,
    )


def _hcl_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _normalize_local_key_path(path: str) -> str:
    return (path or settings.AWS_SSH_PRIVATE_KEY_PATH).replace("\\", "/")


def _replace_hcl_block(
    terraform_code: str,
    header_pattern: str,
    replacement: str,
) -> tuple[str, bool]:
    match = re.search(header_pattern, terraform_code)
    if not match:
        return terraform_code, False
    open_index = terraform_code.find("{", match.start(), match.end())
    close_index = _find_matching_brace(terraform_code, open_index)
    if close_index is None:
        return terraform_code, False
    return terraform_code[:match.start()] + replacement + terraform_code[close_index + 1:], True


def _set_variable_default(terraform_code: str, variable_name: str, default_value: str) -> str:
    escaped_default = _hcl_escape(default_value)
    pattern = rf'variable\s+"{re.escape(variable_name)}"\s*{{'
    match = re.search(pattern, terraform_code)
    if match:
        open_index = terraform_code.find("{", match.start(), match.end())
        close_index = _find_matching_brace(terraform_code, open_index)
        if close_index is None:
            return terraform_code
        block = terraform_code[match.start():close_index + 1]
        if re.search(r"^\s*default\s*=", block, flags=re.MULTILINE):
            block = re.sub(
                r"^(\s*)default\s*=.*$",
                rf'\1default     = "{escaped_default}"',
                block,
                count=1,
                flags=re.MULTILINE,
            )
        else:
            block = block[:-1].rstrip() + f'\n  default     = "{escaped_default}"\n}}'
        return terraform_code[:match.start()] + block + terraform_code[close_index + 1:]

    variable_block = (
        f'\nvariable "{variable_name}" {{\n'
        f'  default     = "{escaped_default}"\n'
        f'}}\n'
    )
    data_match = re.search(r'\ndata\s+"aws_ami"', terraform_code)
    if data_match:
        return terraform_code[:data_match.start()] + variable_block + terraform_code[data_match.start():]
    return variable_block + terraform_code


def _enforce_ssh_key_settings(terraform_code: str) -> str:
    """
    Hardcode the EC2 key pair and local PEM path used by ssh_command.

    Terraform needs `key_name` for AWS, while users need the local PEM path for
    SSH. The LLM has previously emitted `<your-key.pem>`, so this pass rewrites
    both the variable defaults and the output block deterministically.
    """
    key_name = settings.AWS_EC2_KEY_NAME or "aws-deployment-devops"
    key_path = _normalize_local_key_path(settings.AWS_SSH_PRIVATE_KEY_PATH)
    terraform_code = _set_variable_default(terraform_code, "key_name", key_name)
    terraform_code = _set_variable_default(terraform_code, "ssh_private_key_path", key_path)

    instance_match = re.search(r'resource\s+"aws_instance"\s+"([^"]+)"', terraform_code)
    instance_name = instance_match.group(1) if instance_match else "main"
    ssh_output = (
        'output "ssh_command" {\n'
        '  description = "SSH command for the EC2 instance"\n'
        f'  value       = "ssh -i ${{var.ssh_private_key_path}} ec2-user@${{aws_instance.{instance_name}.public_ip}}"\n'
        '}'
    )
    terraform_code, replaced = _replace_hcl_block(
        terraform_code,
        r'output\s+"ssh_command"\s*{',
        ssh_output,
    )
    if not replaced:
        terraform_code = terraform_code.rstrip() + "\n\n" + ssh_output + "\n"
    return terraform_code


def _extract_compose_host_ports(terraform_code: str) -> List[int]:
    ports = []
    for match in re.finditer(
        r"""["'](?:(?:\d{1,3}\.){3}\d{1,3}:)?(\d{1,5}):\d{1,5}(?:/(?:tcp|udp))?["']""",
        terraform_code,
    ):
        port = int(match.group(1))
        if 1 <= port <= 65535:
            ports.append(port)
    return ports


def _extract_heredoc_blocks(text: str, header_pattern: str) -> List[str]:
    blocks: List[str] = []
    for match in re.finditer(header_pattern, text, flags=re.MULTILINE):
        marker = match.group("marker")
        end_match = re.search(
            rf"^[ \t]*{re.escape(marker)}[ \t]*$",
            text[match.end():],
            flags=re.MULTILINE,
        )
        if end_match:
            blocks.append(text[match.end():match.end() + end_match.start()])
    return blocks


def _extract_user_data_blocks(terraform_code: str) -> List[str]:
    return _extract_heredoc_blocks(
        terraform_code,
        r"user_data\s*=\s*<<-?\s*[\"']?(?P<marker>[A-Za-z_][A-Za-z0-9_]*)[\"']?[ \t]*\n",
    )


def _extract_embedded_compose_documents(terraform_code: str) -> List[str]:
    compose_docs: List[str] = []
    for user_data in _extract_user_data_blocks(terraform_code):
        compose_docs.extend(
            _extract_heredoc_blocks(
                user_data,
                r"^[^\n]*docker-compose\.ya?ml[^\n]*<<\s*[\"']?(?P<marker>[A-Za-z_][A-Za-z0-9_]*)[\"']?[ \t]*\n",
            )
        )
    return compose_docs


def _compose_port_host_side(port_mapping: Any) -> Optional[int]:
    if isinstance(port_mapping, dict):
        published = port_mapping.get("published")
        if published is None:
            published = port_mapping.get("host_port")
        if published is None:
            return None
        value = str(published).strip().strip('"\'')
    else:
        value = str(port_mapping).strip().strip('"\'')

    if "/" in value:
        value = value.split("/", 1)[0]
    parts = value.split(":")
    if len(parts) < 2:
        return None
    host_port = parts[-2].strip()
    if not host_port.isdigit():
        return None
    port = int(host_port)
    return port if 1 <= port <= 65535 else None


def _output_name_for_compose_service(service_name: str, image_name: str) -> Optional[str]:
    combined = f"{service_name} {image_name}".lower()
    if any(token in combined for token in ("frontend", "front-end", "client", "web", "ui")):
        return "frontend_url"
    if any(token in combined for token in ("backend", "back-end", "server", "api")):
        return "backend_url"
    return None


def _extract_compose_output_url_ports(terraform_code: str) -> Dict[str, int]:
    output_ports: Dict[str, int] = {}
    for compose_content in _extract_embedded_compose_documents(terraform_code):
        try:
            compose_data = yaml.safe_load(compose_content) or {}
        except yaml.YAMLError:
            continue
        services = compose_data.get("services") if isinstance(compose_data, dict) else None
        if not isinstance(services, dict):
            continue

        for service_name, service in services.items():
            if not isinstance(service, dict):
                continue
            image_name = str(service.get("image") or "")
            if _is_database_compose_service(str(service_name), image_name):
                continue
            output_name = _output_name_for_compose_service(str(service_name), image_name)
            if not output_name or output_name in output_ports:
                continue

            for port_mapping in service.get("ports") or []:
                host_port = _compose_port_host_side(port_mapping)
                if host_port is not None:
                    output_ports[output_name] = host_port
                    break
    return output_ports


def _inject_url_port(value: str, port: int) -> str:
    url_pattern = re.compile(
        r"(?P<base>https?://\$\{aws_instance\.[^}]+\.public_ip\})(?::(?P<port>\$\{[^}]+\}|\d{1,5}))?"
    )

    def replace(match: re.Match) -> str:
        if match.group("port") == str(port):
            return match.group(0)
        return f"{match.group('base')}:{port}"

    return url_pattern.sub(replace, value, count=1)


def _ensure_output_url_port(terraform_code: str, output_name: str, port: int) -> str:
    match = re.search(rf'output\s+"{re.escape(output_name)}"\s*{{', terraform_code)
    if not match:
        return terraform_code
    open_index = terraform_code.find("{", match.start(), match.end())
    close_index = _find_matching_brace(terraform_code, open_index)
    if close_index is None:
        return terraform_code

    block = terraform_code[match.start():close_index + 1]
    updated_block = _inject_url_port(block, port)
    if updated_block == block:
        return terraform_code
    return terraform_code[:match.start()] + updated_block + terraform_code[close_index + 1:]


def _ensure_output_urls_include_compose_ports(terraform_code: str) -> str:
    """
    Make frontend_url/backend_url outputs match host ports from embedded compose.

    Gemini may emit `frontend_url` without `:5173` even when user_data maps
    `5173:80`. This pass reads only the docker-compose.yml heredoc inside
    user_data and injects the matching host-side service port into the URL.
    """
    for output_name, port in _extract_compose_output_url_ports(terraform_code).items():
        terraform_code = _ensure_output_url_port(terraform_code, output_name, port)
    return terraform_code


def _parse_variable_defaults(terraform_code: str) -> Dict[str, str]:
    """Return simple Terraform variable defaults as unquoted strings."""
    defaults: Dict[str, str] = {}
    for match in re.finditer(
        r'variable\s+"([^"]+)"\s*\{(?P<body>.*?)\}',
        terraform_code,
        flags=re.DOTALL,
    ):
        default_match = re.search(
            r"default\s*=\s*(?P<value>\"[^\"]*\"|'[^']*'|[^\s#]+)",
            match.group("body"),
        )
        if default_match:
            defaults[match.group(1)] = _strip_hcl_quotes(default_match.group("value"))
    return defaults


def _strip_hcl_quotes(value: str) -> str:
    value = value.strip().rstrip(",")
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def _resolve_hcl_scalar(value: str, var_defaults: Dict[str, str]) -> str:
    value = _strip_hcl_quotes(value)
    var_match = re.fullmatch(r"var\.(\w+)", value)
    if var_match:
        return var_defaults.get(var_match.group(1), value)
    return value


def _resolve_hcl_port(value: str, var_defaults: Dict[str, str]) -> Optional[int]:
    resolved = _resolve_hcl_scalar(value, var_defaults)
    return int(resolved) if resolved.isdigit() else None


def _extract_hcl_attr(body: str, attr: str) -> Optional[str]:
    match = re.search(rf"\b{re.escape(attr)}\s*=\s*(?P<value>[^\n#]+)", body)
    return match.group("value").strip() if match else None


def _extract_hcl_list_attr(
    body: str,
    attr: str,
    var_defaults: Dict[str, str],
) -> tuple:
    match = re.search(
        rf"\b{re.escape(attr)}\s*=\s*\[(?P<value>.*?)\]",
        body,
        flags=re.DOTALL,
    )
    if not match:
        return ()
    values = [
        _resolve_hcl_scalar(item, var_defaults)
        for item in match.group("value").split(",")
        if item.strip()
    ]
    return tuple(sorted(values))


def _ingress_permission_key(
    body: str,
    var_defaults: Dict[str, str],
) -> Optional[tuple]:
    """
    Build the AWS permission identity for an ingress block.

    AWS ignores descriptions when deciding duplicate ingress permissions. A
    block using `var.app_port` with default `5000` is the same permission as a
    literal `5000` block when protocol and source match.
    """
    from_expr = _extract_hcl_attr(body, "from_port")
    to_expr = _extract_hcl_attr(body, "to_port")
    protocol_expr = _extract_hcl_attr(body, "protocol")
    if not (from_expr and to_expr and protocol_expr):
        return None

    from_port = _resolve_hcl_port(from_expr, var_defaults)
    to_port = _resolve_hcl_port(to_expr, var_defaults)
    if from_port is None or to_port is None:
        return None

    protocol = _resolve_hcl_scalar(protocol_expr, var_defaults).lower()
    sources = []
    for attr in ("cidr_blocks", "ipv6_cidr_blocks", "prefix_list_ids", "security_groups"):
        values = _extract_hcl_list_attr(body, attr, var_defaults)
        if values:
            sources.append((attr, values))

    self_expr = _extract_hcl_attr(body, "self")
    if self_expr:
        sources.append(("self", (_resolve_hcl_scalar(self_expr, var_defaults).lower(),)))

    if not sources:
        return None
    return (from_port, to_port, protocol, tuple(sorted(sources)))


def _find_matching_brace(text: str, open_index: int) -> Optional[int]:
    depth = 0
    for index in range(open_index, len(text)):
        char = text[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index
    return None


def _dedupe_ingress_blocks_in_security_groups(terraform_code: str) -> str:
    """
    Remove duplicate ingress blocks from generated aws_security_group resources.

    This is a post-LLM safety pass for AWS EC2's "same permission must not
    appear multiple times" error. It preserves the first matching permission
    and only removes later exact duplicates after resolving simple variable
    defaults.
    """
    var_defaults = _parse_variable_defaults(terraform_code)
    resource_pattern = re.compile(r'resource\s+"aws_security_group"\s+"[^"]+"\s*{')
    ingress_pattern = re.compile(
        r"(?P<block>\n?[ \t]*ingress\s*\{(?P<body>.*?)\n[ \t]*\})",
        flags=re.DOTALL,
    )

    result = []
    last_index = 0
    for match in resource_pattern.finditer(terraform_code):
        open_index = terraform_code.find("{", match.start(), match.end())
        close_index = _find_matching_brace(terraform_code, open_index)
        if close_index is None:
            continue

        result.append(terraform_code[last_index:match.start()])
        resource_block = terraform_code[match.start():close_index + 1]
        seen_permissions = set()
        block_parts = []
        block_last_index = 0

        for ingress_match in ingress_pattern.finditer(resource_block):
            block_parts.append(resource_block[block_last_index:ingress_match.start()])
            key = _ingress_permission_key(ingress_match.group("body"), var_defaults)
            if key is None or key not in seen_permissions:
                block_parts.append(ingress_match.group("block"))
                if key is not None:
                    seen_permissions.add(key)
            block_last_index = ingress_match.end()

        block_parts.append(resource_block[block_last_index:])
        result.append("".join(block_parts))
        last_index = close_index + 1

    if not result:
        return terraform_code
    result.append(terraform_code[last_index:])
    return "".join(result)


def _validate_unique_host_ports(host_ports: List[int]) -> None:
    seen = set()
    duplicates = set()
    for port in host_ports:
        if port in seen:
            duplicates.add(port)
        seen.add(port)
    if duplicates:
        duplicate_list = ", ".join(str(port) for port in sorted(duplicates))
        raise HTTPException(
            status_code=400,
            detail=(
                "Generated docker-compose.yml contains duplicate host port mappings: "
                f"{duplicate_list}. Fix Docker compose ports before AWS deployment."
            ),
        )


def _existing_ingress_ranges(terraform_code: str) -> List[tuple]:
    """
    Return (from_port, to_port) tuples for every ingress block already in the
    security group.  Handles both literal port numbers and Terraform variable
    references like `from_port = var.app_port`.  Variable references are
    resolved by looking up the `default` value in the matching variable block.
    If a default cannot be determined the block is skipped (safe: may add a
    redundant rule but never a duplicate).
    """
    var_defaults = _parse_variable_defaults(terraform_code)
    ranges = []
    for match in re.finditer(r'ingress\s*\{(?P<body>.*?)\}', terraform_code, flags=re.DOTALL):
        body = match.group('body')
        from_expr = _extract_hcl_attr(body, "from_port")
        to_expr = _extract_hcl_attr(body, "to_port")
        if from_expr and to_expr:
            from_port = _resolve_hcl_port(from_expr, var_defaults)
            to_port = _resolve_hcl_port(to_expr, var_defaults)
            if from_port is not None and to_port is not None:
                ranges.append((from_port, to_port))
    return ranges


def _port_is_allowed(port: int, ranges: List[tuple]) -> bool:
    return any(start <= port <= end for start, end in ranges)



def _find_security_group_close(terraform_code: str) -> Optional[int]:
    match = re.search(r'resource\s+"aws_security_group"\s+"[^"]+"\s*{', terraform_code)
    if not match:
        return None
    depth = 0
    for index in range(match.start(), len(terraform_code)):
        char = terraform_code[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index
    return None


def _ensure_compose_host_ports_allowed(terraform_code: str) -> str:
    host_ports = _extract_compose_host_ports(terraform_code)
    if not host_ports:
        return terraform_code

    _validate_unique_host_ports(host_ports)

    ingress_ranges = _existing_ingress_ranges(terraform_code)
    missing_ports = [
        port for port in sorted(set(host_ports))
        if not _port_is_allowed(port, ingress_ranges)
    ]
    if not missing_ports:
        return terraform_code

    close_index = _find_security_group_close(terraform_code)
    if close_index is None:
        raise HTTPException(
            status_code=400,
            detail="Generated Terraform is missing an aws_security_group resource.",
        )

    blocks = []
    for port in missing_ports:
        blocks.append(
            "\n"
            "  ingress {\n"
            f'    description = "App port {port}"\n'
            f"    from_port   = {port}\n"
            f"    to_port     = {port}\n"
            "    protocol    = \"tcp\"\n"
            "    cidr_blocks = [\"0.0.0.0/0\"]\n"
            "  }\n"
        )
    return terraform_code[:close_index] + "".join(blocks) + terraform_code[close_index:]


def _validate_key_name(terraform_code: str) -> None:
    """Raise HTTPException if key_name is missing from the aws_instance block."""
    if not re.search(r'resource\s+"aws_instance"', terraform_code):
        return  # No instance block yet; other validators will catch it
    if not re.search(r'key_name\s*=', terraform_code):
        raise HTTPException(
            status_code=400,
            detail=(
                "Generated Terraform is missing key_name on the aws_instance resource. "
                "SSH access will be impossible without a key pair."
            ),
        )


def _validate_ssh_ingress(terraform_code: str) -> None:
    """Raise HTTPException if no ingress rule allows port 22."""
    ingress_ranges = _existing_ingress_ranges(terraform_code)
    if not _port_is_allowed(22, ingress_ranges):
        raise HTTPException(
            status_code=400,
            detail="Generated Terraform security group is missing an ingress rule for SSH port 22.",
        )


def _validate_egress(terraform_code: str) -> None:
    """Raise HTTPException if no egress block is present in the security group."""
    if re.search(r'resource\s+"aws_security_group"', terraform_code):
        if not re.search(r'egress\s*{', terraform_code):
            raise HTTPException(
                status_code=400,
                detail="Generated Terraform security group is missing an egress (outbound) rule.",
            )


def _validate_user_data(terraform_code: str) -> None:
    """Raise HTTPException if user_data block is absent or contains no startup commands."""
    if not re.search(r'user_data\s*=', terraform_code):
        raise HTTPException(
            status_code=400,
            detail="Generated Terraform is missing a user_data startup script on the EC2 instance.",
        )
    if not re.search(r'docker', terraform_code, re.IGNORECASE):
        raise HTTPException(
            status_code=400,
            detail="Generated Terraform user_data does not appear to install or start Docker.",
        )


def _validate_required_outputs(terraform_code: str) -> None:
    """Raise HTTPException if any of the required output blocks are missing."""
    required = ["app_url", "ssh_command"]
    for name in required:
        if not re.search(rf'output\s+"{re.escape(name)}"', terraform_code):
            raise HTTPException(
                status_code=400,
                detail=f'Generated Terraform is missing required output "{name}".',
            )


def _validate_ssh_command_output(terraform_code: str) -> None:
    """Reject placeholder SSH output values before Terraform is written."""
    key_path = _normalize_local_key_path(settings.AWS_SSH_PRIVATE_KEY_PATH)
    match = re.search(r'output\s+"ssh_command"\s*{', terraform_code)
    if not match:
        return
    open_index = terraform_code.find("{", match.start(), match.end())
    close_index = _find_matching_brace(terraform_code, open_index)
    ssh_output_block = (
        terraform_code[match.start():close_index + 1]
        if close_index is not None
        else terraform_code[match.start():]
    )
    if "<your-key.pem>" in ssh_output_block or "<key.pem>" in ssh_output_block:
        raise HTTPException(
            status_code=400,
            detail="Generated Terraform ssh_command still contains a placeholder key path.",
        )
    if "var.ssh_private_key_path" not in ssh_output_block and key_path not in ssh_output_block:
        raise HTTPException(
            status_code=400,
            detail="Generated Terraform ssh_command does not reference the configured SSH private key path.",
        )


def _validate_root_volume(terraform_code: str, min_gb: int = 20) -> None:
    """Raise HTTPException if root_block_device is missing or volume_size is below min_gb."""
    if not re.search(r'root_block_device\s*{', terraform_code):
        raise HTTPException(
            status_code=400,
            detail=(
                f"Generated Terraform is missing root_block_device on the EC2 instance. "
                f"Docker deployments require at least {min_gb} GB."
            ),
        )
    match = re.search(r'volume_size\s*=\s*(\d+)', terraform_code)
    if match and int(match.group(1)) < min_gb:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Generated Terraform root volume is {match.group(1)} GB, "
                f"which is below the minimum {min_gb} GB required for Docker deployments."
            ),
        )


def _run_terraform_validations(terraform_code: str) -> None:
    """
    Run all post-LLM Terraform validations in order.
    Each validator raises HTTPException on failure.
    """
    _validate_key_name(terraform_code)
    _validate_ssh_ingress(terraform_code)
    _validate_egress(terraform_code)
    _validate_user_data(terraform_code)
    _validate_required_outputs(terraform_code)
    _validate_ssh_command_output(terraform_code)
    _validate_root_volume(terraform_code)



def _resolve_project_root(project: Dict, detail: str = "Project path not found") -> str:
    extracted_path = project.get("extracted_path")
    if not extracted_path:
        raise HTTPException(status_code=400, detail=detail)
    real_path = os.path.abspath(extracted_path)
    if not os.path.exists(real_path):
        raise HTTPException(status_code=400, detail=detail)
    return find_project_root(real_path)


def _optional_project_root(project: Dict) -> Optional[str]:
    try:
        return _resolve_project_root(project)
    except HTTPException:
        return None


def get_compose_and_env_for_terraform(
    extracted_path: str,
    services: List[Dict],
    image_repo: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Read existing docker-compose.yml and .env files from the project.
    
    Args:
        extracted_path: Path to the extracted project
        services: List of service dicts with 'name' and 'path'
    
    Returns:
        Dict with compose_content (str or None), env_files (dict of service_name -> env content)
    """
    result = {"compose_content": None, "env_files": {}}
    
    # Read docker-compose.yml
    compose_path = os.path.join(extracted_path, "docker-compose.yml")
    if os.path.exists(compose_path):
        try:
            with open(compose_path, 'r', encoding='utf-8') as f:
                result["compose_content"] = f.read()
            if image_repo:
                result["compose_content"] = _normalize_compose_images_for_aws(
                    result["compose_content"],
                    image_repo,
                )
            print(f"📄 Read docker-compose.yml: {len(result['compose_content'])} bytes")
        except Exception as e:
            print(f"⚠️ Error reading docker-compose.yml: {e}")
    
    # Read .env files for each service
    for svc in services:
        svc_name = svc.get("name", "app")
        svc_path = svc.get("path", ".")
        
        # Normalize service path
        if svc_path.endswith("/"):
            svc_path = svc_path[:-1]
        
        # Try to find .env file
        for env_name in [".env", ".env.local", ".env.production"]:
            env_path = os.path.join(extracted_path, svc_path, env_name)
            if os.path.exists(env_path):
                try:
                    with open(env_path, 'r', encoding='utf-8') as f:
                        result["env_files"][svc_name] = f.read()
                    print(f"📄 Read {svc_name} env file: {env_path}")
                    break
                except Exception as e:
                    print(f"⚠️ Error reading {env_path}: {e}")
    
    return result

async def check_aws_prerequisites(project_id: str, current_user: dict) -> Dict[str, Any]:
    """
    Check prerequisites for AWS deployment.
    
    Returns:
        Dict with can_deploy, missing items, and project info
    """
    collection = get_projects_collection()
    project = await collection.find_one({"_id": ObjectId(project_id)})
    
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    if project.get("user_id") != str(current_user["_id"]):
        raise HTTPException(status_code=403, detail="Access denied: Not project owner")
    
    issues = []
    
    # Check if Docker build succeeded
    docker_push_success = project.get("docker_push_success", False)
    if not docker_push_success:
        issues.append("Docker images must be built and pushed first")
    
    # Check AWS credentials
    aws_creds = verify_aws_credentials()
    if not aws_creds["is_valid"]:
        issues.append(aws_creds["message"])
    
    # Check if terraform is installed
    terraform_path = getattr(settings, "TERRAFORM_PATH", "terraform")
    test_service = AWSDeploymentService(".", terraform_path)
    if not test_service.check_terraform_installed():
        issues.append("Terraform CLI not found. Please install Terraform.")
    
    # Check if docker-compose.yml exists (Docker deployment must be done first)
    project_root = _optional_project_root(project)
    compose_path = os.path.join(project_root, "docker-compose.yml") if project_root else ""
    docker_compose_exists = os.path.exists(compose_path) if compose_path else False
    if not project_root:
        issues.append("Extracted project files not found")
    elif not docker_compose_exists:
        issues.append("Run Docker deployment first to generate docker-compose.yml")
    
    return {
        "can_deploy": len(issues) == 0,
        "issues": issues,
        "project_name": project.get("project_name", "app"),
        "aws_region": aws_creds.get("region", "us-east-1"),
        "docker_push_success": docker_push_success,
        "docker_hub_username": settings.DOCKER_HUB_USERNAME or "",
        "terraform_exists": os.path.exists(os.path.join(project_root, "infra", "main.tf")) if project_root else False,
        "aws_deployment_status": project.get("aws_deployment_status", "not_deployed"),
    }


async def generate_terraform_handler(
    project_id: str,
    current_user: dict,
    aws_config: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Generate Terraform configuration using LLM.
    
    Args:
        project_id: Project ID
        current_user: Current authenticated user
        aws_config: Dict with aws_region, docker_repo_prefix, db_engine, db_url, desired_count
    
    Returns:
        Dict with status, terraform_path, message
    """
    try:
        if not ObjectId.is_valid(project_id):
            raise HTTPException(status_code=400, detail="Invalid project ID format")
        
        collection = get_projects_collection()
        project = await collection.find_one({"_id": ObjectId(project_id)})
        
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        
        if project.get("user_id") != str(current_user["_id"]):
            raise HTTPException(status_code=403, detail="Access denied: Not project owner")
        
        # Extract project info
        project_name = project.get("project_name", "app").replace(" ", "-").lower()
        project_root = _resolve_project_root(project, detail="Extracted project files not found")
        metadata = project.get("metadata", {})
        
        # Build services list from metadata
        services = _build_services_from_metadata(metadata)
        
        # Get environment variables for each service
        service_env_vars = get_service_env_vars_for_terraform(project_root, services)
        
        # Merge any extra env vars from user
        extra_env = aws_config.get("extra_env", {})
        if extra_env:
            # Ensure every service gets extra_env, even if none were detected initially
            for svc in services:
                svc_name = svc.get("name", "app")
                env_dict = service_env_vars.setdefault(svc_name, {})
                env_dict.update(extra_env)
        
        image_repo = build_project_image_repo(
            project.get("project_name", "unnamed"),
            aws_config.get("docker_repo_prefix") or settings.DOCKER_HUB_USERNAME,
            settings.APP_REGISTRY_PREFIX,
        )

        # Read existing docker-compose.yml and .env files (from Docker deployment)
        compose_data = get_compose_and_env_for_terraform(project_root, services, image_repo)
        _validate_docker_hub_manifests_exist(
            _expected_aws_app_images(compose_data.get("compose_content"), services, image_repo)
        )
        
        # Generate Terraform via LLM
        print(f"🏗️ Generating Terraform for project: {project_name}")
        
        # run_terraform_deploy_chat makes a synchronous (requests-based) LLM call
        # that can take a long time; calling it directly here would block the
        # whole event loop for the duration of the call. Offload it to a thread.
        terraform_code = await run_in_threadpool(
            run_terraform_deploy_chat,
            project_name=project_name,
            services=services,
            docker_repo_prefix=aws_config.get("docker_repo_prefix", ""),
            image_repo=image_repo,
            aws_region=aws_config.get("aws_region", "us-east-1"),
            db_engine=aws_config.get("db_engine"),
            db_url=aws_config.get("mongo_db_url") or aws_config.get("rds_db_url"),
            desired_count=aws_config.get("desired_count", 1),
            service_env_vars=service_env_vars,
            existing_compose=compose_data.get("compose_content"),
            existing_env_files=compose_data.get("env_files"),
            key_name=aws_config.get("key_name") or settings.AWS_EC2_KEY_NAME,
            ssh_private_key_path=aws_config.get("ssh_private_key_path") or settings.AWS_SSH_PRIVATE_KEY_PATH,
            allowed_ssh_cidr=aws_config.get("allowed_ssh_cidr", "0.0.0.0/0"),
            app_port=aws_config.get("app_port"),
            root_volume_size=aws_config.get("root_volume_size", 20),
        )
        
        if not terraform_code or terraform_code.startswith("ERROR"):
            raise HTTPException(
                status_code=500,
                detail=f"LLM failed to generate Terraform: {terraform_code[:200]}"
            )
        terraform_code = _dedupe_ingress_blocks_in_security_groups(
            _ensure_output_urls_include_compose_ports(
                _ensure_compose_host_ports_allowed(
                    _enforce_ssh_key_settings(
                        _enforce_ec2_instance_type(_inject_docker_credentials(terraform_code))
                    )
                )
            )
        )
        _run_terraform_validations(terraform_code)
        
        # Write Terraform to project's infra directory
        terraform_path = getattr(settings, "TERRAFORM_PATH", "terraform")
        aws_service = AWSDeploymentService(project_root, terraform_path)
        tf_file_path = aws_service.write_terraform(terraform_code)
        
        # Update project with terraform status
        await collection.update_one(
            {"_id": ObjectId(project_id)},
            {
                "$set": {
                    "aws_deployment_status": "terraform_generated",
                    "aws_region": aws_config.get("aws_region", "us-east-1"),
                    "aws_terraform_path": tf_file_path,
                    "updated_at": datetime.now()
                },
                "$push": {
                    "logs": {
                        "message": f"Terraform configuration generated: main.tf -> {tf_file_path}",
                        "timestamp": datetime.now()
                    }
                }
            }
        )
        
        print(f"Terraform generated: main.tf -> {tf_file_path}")
        
        return {
            "status": "generated",
            "terraform_path": tf_file_path,
            "message": "Terraform configuration generated successfully"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Terraform generation failed: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Terraform generation failed: {str(e)}")


async def apply_terraform_handler(
    project_id: str,
    current_user: dict,
    variables: Optional[Dict[str, Any]] = None
):
    """
    Apply Terraform configuration and stream progress.
    
    Returns:
        StreamingResponse with SSE events for progress
    """
    if not ObjectId.is_valid(project_id):
        raise HTTPException(status_code=400, detail="Invalid project ID format")
    
    collection = get_projects_collection()
    project = await collection.find_one({"_id": ObjectId(project_id)})
    
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    if project.get("user_id") != str(current_user["_id"]):
        raise HTTPException(status_code=403, detail="Access denied: Not project owner")
    
    project_root = _resolve_project_root(project)
    
    # Initialize AWS service
    terraform_path = getattr(settings, "TERRAFORM_PATH", "terraform")
    aws_service = AWSDeploymentService(project_root, terraform_path)
    
    # Update status
    await collection.update_one(
        {"_id": ObjectId(project_id)},
        {
            "$set": {
                "aws_deployment_status": "deploying",
                "updated_at": datetime.now()
            }
        }
    )
    
    async def event_generator():
        """Generate SSE events for terraform apply progress."""
        import json
        
        try:
            # Run terraform init first
            for event in aws_service.terraform_init():
                yield f"data: {json.dumps(event)}\n\n"
                if event.get("exit_code", 0) != 0:
                    # Init failed
                    await _update_aws_status(project_id, "failed")
                    return
            
            # Run terraform apply
            for event in aws_service.terraform_apply(variables=variables):
                yield f"data: {json.dumps(event)}\n\n"
                
                if "exit_code" in event:
                    if event["exit_code"] == 0:
                        # Apply succeeded - get outputs
                        outputs = aws_service.get_deployment_status()
                        await _update_aws_status(
                            project_id,
                            "deployed",
                            frontend_url=outputs.get("frontend_url"),
                            backend_url=outputs.get("backend_url")
                        )
                        yield f"data: {json.dumps({'type': 'complete', 'outputs': outputs})}\n\n"
                    else:
                        await _update_aws_status(project_id, "failed")
            
        except Exception as e:
            await _update_aws_status(project_id, "failed")
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
    
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream"
    )


async def destroy_terraform_handler(
    project_id: str,
    current_user: dict
):
    """
    Destroy Terraform infrastructure and stream progress.
    
    Returns:
        StreamingResponse with SSE events for progress
    """
    if not ObjectId.is_valid(project_id):
        raise HTTPException(status_code=400, detail="Invalid project ID format")
    
    collection = get_projects_collection()
    project = await collection.find_one({"_id": ObjectId(project_id)})
    
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    if project.get("user_id") != str(current_user["_id"]):
        raise HTTPException(status_code=403, detail="Access denied: Not project owner")
    
    project_root = _resolve_project_root(project)
    
    terraform_path = getattr(settings, "TERRAFORM_PATH", "terraform")
    aws_service = AWSDeploymentService(project_root, terraform_path)
    
    await collection.update_one(
        {"_id": ObjectId(project_id)},
        {"$set": {"aws_deployment_status": "destroying", "updated_at": datetime.now()}}
    )
    
    async def event_generator():
        import json
        
        try:
            for event in aws_service.terraform_destroy():
                yield f"data: {json.dumps(event)}\n\n"
                
                if "exit_code" in event:
                    if event["exit_code"] == 0:
                        await _update_aws_status(project_id, "not_deployed")
                        yield f"data: {json.dumps({'type': 'complete', 'message': 'Infrastructure destroyed'})}\n\n"
                    else:
                        await _update_aws_status(project_id, "destroy_failed")
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
    
    return StreamingResponse(event_generator(), media_type="text/event-stream")


async def scale_to_zero_handler(project_id: str, current_user: dict):
    """
    Stop the EC2 instance (cost savings - no compute charges when stopped).
    
    Returns:
        StreamingResponse with SSE events
    """
    if not ObjectId.is_valid(project_id):
        raise HTTPException(status_code=400, detail="Invalid project ID format")
    
    collection = get_projects_collection()
    project = await collection.find_one({"_id": ObjectId(project_id)})
    
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    if project.get("user_id") != str(current_user["_id"]):
        raise HTTPException(status_code=403, detail="Access denied")
    
    project_root = _resolve_project_root(project)
    terraform_path = getattr(settings, "TERRAFORM_PATH", "terraform")
    aws_service = AWSDeploymentService(project_root, terraform_path)
    
    async def event_generator():
        import json
        for event in aws_service.scale_to_zero():
            yield f"data: {json.dumps(event)}\n\n"
            if event.get("exit_code") == 0:
                await _update_aws_status(project_id, "scaled_to_zero")
    
    return StreamingResponse(event_generator(), media_type="text/event-stream")


async def scale_up_handler(project_id: str, current_user: dict):
    """
    Start a stopped EC2 instance.

    Returns:
        StreamingResponse with SSE events
    """
    if not ObjectId.is_valid(project_id):
        raise HTTPException(status_code=400, detail="Invalid project ID format")

    collection = get_projects_collection()
    project = await collection.find_one({"_id": ObjectId(project_id)})

    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    if project.get("user_id") != str(current_user["_id"]):
        raise HTTPException(status_code=403, detail="Access denied")

    project_root = _resolve_project_root(project)
    terraform_path = getattr(settings, "TERRAFORM_PATH", "terraform")
    aws_service = AWSDeploymentService(project_root, terraform_path)

    async def event_generator():
        import json
        for event in aws_service.scale_up():
            yield f"data: {json.dumps(event)}\n\n"
            if event.get("exit_code") == 0:
                await _update_aws_status(project_id, "deployed")

    return StreamingResponse(event_generator(), media_type="text/event-stream")


async def get_aws_status_handler(project_id: str, current_user: dict) -> Dict[str, Any]:
    """
    Get AWS deployment status.
    
    Returns:
        Dict with status, frontend_url (public IP), instance info, etc.
    """
    if not ObjectId.is_valid(project_id):
        raise HTTPException(status_code=400, detail="Invalid project ID format")
    
    collection = get_projects_collection()
    project = await collection.find_one({"_id": ObjectId(project_id)})
    
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    if project.get("user_id") != str(current_user["_id"]):
        raise HTTPException(status_code=403, detail="Access denied")
    
    # Get basic status from DB
    status = {
        "aws_deployment_status": project.get("aws_deployment_status", "not_deployed"),
        "aws_region": project.get("aws_region"),
        "aws_frontend_url": project.get("aws_frontend_url"),
        "aws_backend_url": project.get("aws_backend_url"),
        "aws_instance_id": project.get("aws_instance_id"),
        "aws_last_deployed": project.get("aws_last_deployed"),
        "docker_push_success": project.get("docker_push_success", False)
    }
    
    # If deployed, try to get live status from terraform
    project_root = _optional_project_root(project)
    if project_root and status["aws_deployment_status"] == "deployed":
        terraform_path = getattr(settings, "TERRAFORM_PATH", "terraform")
        aws_service = AWSDeploymentService(project_root, terraform_path)
        live_status = aws_service.get_deployment_status()
        status.update({
            "live_public_ip": live_status.get("public_ip"),
            "live_frontend_url": live_status.get("frontend_url"),
            "live_backend_url": live_status.get("backend_url"),
            "live_instance_id": live_status.get("instance_id"),
            "live_vpc_id": live_status.get("vpc_id")
        })
    
    return status


async def fix_terraform_handler(
    project_id: str,
    current_user: dict,
    error_output: str
) -> Dict[str, Any]:
    """
    Fix Terraform errors using LLM.
    
    Reads the current main.tf, sends it with the error to LLM,
    and writes the corrected version.
    
    Args:
        project_id: Project ID
        current_user: Current authenticated user
        error_output: The error message from terraform
    
    Returns:
        Dict with status and message
    """
    if not ObjectId.is_valid(project_id):
        raise HTTPException(status_code=400, detail="Invalid project ID format")
    
    collection = get_projects_collection()
    project = await collection.find_one({"_id": ObjectId(project_id)})
    
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    if project.get("user_id") != str(current_user["_id"]):
        raise HTTPException(status_code=403, detail="Access denied")
    
    project_root = _resolve_project_root(project)
    
    # Ensure terraform is available before attempting a fix
    terraform_path = getattr(settings, "TERRAFORM_PATH", "terraform")
    if not AWSDeploymentService(".", terraform_path).check_terraform_installed():
        raise HTTPException(
            status_code=400,
            detail=f"Terraform CLI not found at: {terraform_path}. Install Terraform or set TERRAFORM_PATH."
        )
    
    # Read current Terraform
    tf_path = os.path.join(project_root, "infra", "main.tf")
    if not os.path.exists(tf_path):
        raise HTTPException(status_code=400, detail="No Terraform file found. Generate first.")
    
    with open(tf_path, "r", encoding="utf-8") as f:
        current_terraform = f.read()
    
    project_name = project.get("project_name", "app")
    
    print(f"🔧 Fixing Terraform for {project_name}...")
    print(f"   Error: {error_output[:200]}...")
    
    # Call LLM to fix. fix_terraform_error is a synchronous (requests-based) LLM
    # call; offload it to a thread so it doesn't block the event loop.
    fixed_terraform = await run_in_threadpool(
        fix_terraform_error,
        current_terraform=current_terraform,
        error_output=error_output,
        project_name=project_name
    )
    
    if not fixed_terraform or len(fixed_terraform) < 100:
        raise HTTPException(status_code=500, detail="LLM failed to generate fix")
    fixed_terraform = _dedupe_ingress_blocks_in_security_groups(
        _ensure_output_urls_include_compose_ports(
            _ensure_compose_host_ports_allowed(
                _enforce_ssh_key_settings(
                    _enforce_ec2_instance_type(_inject_docker_credentials(fixed_terraform))
                )
            )
        )
    )
    _run_terraform_validations(fixed_terraform)
    
    # Write fixed Terraform
    with open(tf_path, "w", encoding="utf-8") as f:
        f.write(fixed_terraform)
    
    # Update status
    await collection.update_one(
        {"_id": ObjectId(project_id)},
        {
            "$set": {"updated_at": datetime.now()},
            "$push": {
                "logs": {
                    "message": "Terraform fixed by LLM",
                    "timestamp": datetime.now()
                }
            }
        }
    )
    
    print(f"✅ Terraform fixed and saved!")
    
    return {
        "status": "fixed",
        "message": "Terraform configuration fixed. Try deploying again."
    }


# ============ Helper Functions ============

def _build_services_from_metadata(metadata: Dict) -> List[Dict]:
    """
    Build a list of service definitions from project metadata.
    
    Returns:
        List of dicts with {name, port, path, type} for each service
    """
    # Prefer the detection pipeline's services list when available
    detected = metadata.get("services")
    if detected:
        result = []
        for svc in detected:
            svc_type = str(svc.get("type", "backend")).lower()
            if svc_type == "database":
                continue  # Skip database services (handled via env vars)
            result.append({
                "name": svc.get("name", "app"),
                "port": svc.get("container_port") or svc.get("runtime_port") or svc.get("port", 3000),
                "path": svc.get("path", "."),
                "type": svc_type,
            })
        if result:
            return result

    # Legacy fallback for metadata without a services list
    services = []
    
    # Check for fullstack project (has both backend and frontend)
    is_fullstack = metadata.get("is_fullstack", False)
    
    if is_fullstack:
        # Backend service
        backend_port = metadata.get("backend_port", 3000)
        services.append({
            "name": "backend",
            "port": backend_port,
            "path": "backend",
            "type": "backend"
        })
        
        # Frontend service (nginx serves on 80)
        services.append({
            "name": "frontend",
            "port": 80,  # nginx container port
            "path": "frontend",
            "type": "frontend"
        })
    else:
        # Single service - determine type
        framework = metadata.get("framework", "").lower()
        
        if any(fw in framework for fw in ["react", "vue", "angular", "next", "vite"]):
            # Frontend-only project
            services.append({
                "name": "frontend",
                "port": 80,
                "path": ".",
                "type": "frontend"
            })
        else:
            # Backend-only project
            port = metadata.get("port", metadata.get("backend_port", 3000))
            services.append({
                "name": "backend",
                "port": port,
                "path": ".",
                "type": "backend"
            })
    
    return services


async def _update_aws_status(
    project_id: str,
    status: str,
    frontend_url: Optional[str] = None,
    backend_url: Optional[str] = None
):
    """Update AWS deployment status in database."""
    collection = get_projects_collection()
    
    update_data = {
        "aws_deployment_status": status,
        "updated_at": datetime.now()
    }
    
    if frontend_url:
        update_data["aws_frontend_url"] = frontend_url
        update_data["aws_last_deployed"] = datetime.now()
    if backend_url:
        update_data["aws_backend_url"] = backend_url
        update_data["aws_last_deployed"] = datetime.now()
    
    await collection.update_one(
        {"_id": ObjectId(project_id)},
        {"$set": update_data}
    )
