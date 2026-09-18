"""
Deployment Readiness Controller
================================
Scans a project for all deployment-critical files, uses Gemini to auto-generate
any missing ones, and returns a structured readiness report.

Files checked:
  - Dockerfile (or per-service Dockerfiles)
  - docker-compose.yml
  - k8s/deployment.yaml
  - k8s/service.yaml
  - .env (template if missing — not auto-filled with secrets)
"""
from __future__ import annotations

import os
import re
from typing import Dict, List, Optional, Tuple

from bson import ObjectId
from fastapi import HTTPException
from starlette.concurrency import run_in_threadpool

from ..config.database import get_projects_collection
from ..utils.auth import decode_access_token
from ..utils.detector import find_project_root
from ..utils.file_system import read_file
from ..utils.image_naming import build_project_image_repo
from ..LLM.docker_deploy_agent import (
    run_docker_deploy_chat,
    parse_and_validate_generated_docker_response,
    run_k8s_manifest_generation,
    parse_generated_k8s_files,
)
from ..config.settings import settings


# ─── Internal helpers ────────────────────────────────────────────────────────

def _safe_read(path: str) -> str:
    try:
        return read_file(path) or ""
    except Exception:
        return ""


def _sanitize_name(name: str) -> str:
    return re.sub(r"[^a-z0-9_-]", "-", name.lower()).strip("-")[:50]


def _node_port_for(project_root: str) -> int:
    raw = 30000 + (hash(project_root) % 2767)
    return max(30000, min(32767, raw))


def _image_repo_for(project_name: str) -> str:
    return build_project_image_repo(
        project_name,
        settings.DOCKER_HUB_USERNAME,
        settings.APP_REGISTRY_PREFIX,
    )


# ─── File scanning ────────────────────────────────────────────────────────────

def _scan_deployment_files(project_root: str, services: List[Dict]) -> Dict[str, bool]:
    """
    Return a dict of {file_label: bool} indicating presence of each critical file.
    """
    k8s_dir = os.path.join(project_root, "k8s")

    # Check Dockerfiles against service paths when metadata is available.
    app_services = [
        svc for svc in services
        if str(svc.get("type", "")).lower() != "database"
    ]
    expected_dockerfiles = []
    for svc in app_services:
        svc_path = str(svc.get("path") or ".").strip("./\\")
        expected_dockerfiles.append(
            os.path.join(project_root, svc_path, "Dockerfile")
            if svc_path
            else os.path.join(project_root, "Dockerfile")
        )

    if expected_dockerfiles:
        has_dockerfile = all(os.path.exists(path) for path in expected_dockerfiles)
    else:
        has_dockerfile = False
        for root, _, files in os.walk(project_root):
            for name in files:
                if name.lower() == "dockerfile":
                    has_dockerfile = True
                    break
            if has_dockerfile:
                break

    # Check docker-compose
    has_compose = any(
        os.path.exists(os.path.join(project_root, n))
        for n in ("docker-compose.yml", "docker-compose.yaml")
    )

    # Check k8s manifests
    has_k8s_deployment = os.path.exists(os.path.join(k8s_dir, "deployment.yaml"))
    has_k8s_service = os.path.exists(os.path.join(k8s_dir, "service.yaml"))

    # Check .env (at root or in any service dir)
    has_env = os.path.exists(os.path.join(project_root, ".env"))
    if not has_env:
        for svc in services:
            svc_path = str(svc.get("path") or ".").strip("./\\")
            svc_dir = os.path.join(project_root, svc_path) if svc_path else project_root
            if os.path.exists(os.path.join(svc_dir, ".env")):
                has_env = True
                break

    return {
        "Dockerfile": has_dockerfile,
        "docker-compose.yml": has_compose,
        "k8s/deployment.yaml": has_k8s_deployment,
        "k8s/service.yaml": has_k8s_service,
        ".env": has_env,
    }


def _collect_existing_dockerfiles(project_root: str) -> Tuple[List[Dict], List[Dict]]:
    dockerfiles: List[Dict] = []
    compose_files: List[Dict] = []
    for root, _, files in os.walk(project_root):
        for name in files:
            full = os.path.join(root, name)
            rel = os.path.relpath(full, project_root).replace("\\", "/")
            if name.lower() == "dockerfile":
                dockerfiles.append({"path": rel, "content": _safe_read(full)})
            if name.lower() in ("docker-compose.yml", "docker-compose.yaml"):
                compose_files.append({"path": rel, "content": _safe_read(full)})
    return dockerfiles, compose_files


def _collect_source_files(project_root: str, max_files: int = 5) -> List[Dict]:
    targets = ["package.json", "requirements.txt", "main.py", "server.js", "app.js", "index.js"]
    source_files: List[Dict] = []
    collected = 0
    for root, _, files in os.walk(project_root):
        if any(skip in root for skip in ("node_modules", "__pycache__", "venv", ".git")):
            continue
        for name in files:
            if collected >= max_files:
                break
            if name in targets or name.endswith((".py", ".js", ".ts")):
                full = os.path.join(root, name)
                content = _safe_read(full)
                if 0 < len(content) < 50_000:
                    rel = os.path.relpath(full, project_root).replace("\\", "/")
                    source_files.append({"path": rel, "content": content})
                    collected += 1
    return source_files


def _build_file_tree_text(project_root: str) -> str:
    lines: List[str] = []
    count = 0

    def walk(current: str, depth: int) -> None:
        nonlocal count
        if depth > 4 or count >= 150:
            return
        try:
            with os.scandir(current) as it:
                for entry in it:
                    if count >= 150:
                        break
                    if entry.name.startswith(".") and entry.name not in (".env", ".env.example"):
                        continue
                    if entry.name in ("node_modules", "__pycache__", "venv", ".git"):
                        continue
                    count += 1
                    rel = os.path.relpath(entry.path, project_root)
                    prefix = "  " * depth + ("[dir] " if entry.is_dir() else "[file] ")
                    lines.append(f"{prefix}{rel}")
                    if entry.is_dir():
                        walk(entry.path, depth + 1)
        except Exception:
            pass

    walk(project_root, 0)
    return "\n".join(lines)


# ─── Auto-generation ─────────────────────────────────────────────────────────

def _generate_docker_files(
    project_root: str,
    project_name: str,
    metadata: Dict,
    services: List[Dict],
    has_dockerfile: bool,
    has_compose: bool,
) -> Dict[str, str]:
    """
    Ask Gemini to generate missing Dockerfile(s) and/or docker-compose.yml.
    Returns dict of {relative_path -> content}.
    """
    existing_dockerfiles, existing_compose = _collect_existing_dockerfiles(project_root)
    source_files = _collect_source_files(project_root)
    file_tree = _build_file_tree_text(project_root)

    if has_dockerfile and has_compose:
        return {}  # Nothing to generate

    missing_labels: List[str] = []
    if not has_dockerfile:
        missing_labels.append("Dockerfile for every service")
    if not has_compose:
        missing_labels.append("docker-compose.yml")

    user_msg = (
        f"Generate the following missing deployment files: {', '.join(missing_labels)}. "
        "Use EXACT values from the project metadata and service definitions. "
        "Provide complete file contents with no placeholders."
    )

    response = run_docker_deploy_chat(
        project_name=project_name,
        metadata=metadata,
        dockerfiles=existing_dockerfiles,
        compose_files=existing_compose,
        source_files=source_files,
        file_tree=file_tree,
        user_message=user_msg,
        logs=None,
        extra_instructions=None,
        services=services,
    )

    files, errors = parse_and_validate_generated_docker_response(
        response,
        metadata,
        services,
        require_dockerfiles=not has_dockerfile,
        require_compose=not has_compose,
    )
    if errors:
        raise ValueError("; ".join(errors))
    return files


def _generate_k8s_files(
    project_root: str,
    project_name: str,
    metadata: Dict,
    services: List[Dict],
) -> Dict[str, str]:
    """Ask Gemini to generate k8s/deployment.yaml and k8s/service.yaml."""
    image_repo = _image_repo_for(project_name)
    node_port = _node_port_for(project_root)
    return run_k8s_manifest_generation(
        project_name=project_name,
        deployment_name=_sanitize_name(project_name),
        image_repo=image_repo,
        node_port=node_port,
        services=services,
        metadata=metadata,
    )


def _generate_env_template(services: List[Dict], metadata: Dict) -> str:
    """Generate a .env template showing required variables without real values."""
    lines = [
        "# Auto-generated .env template — fill in your actual values before deploying",
        "",
    ]

    database = str(metadata.get("database") or "").strip()
    if database and database.lower() not in ("unknown", "none", ""):
        db_lower = database.lower()
        if "mongo" in db_lower:
            lines += ["# MongoDB connection", "MONGODB_URL=mongodb://localhost:27017/myapp", ""]
        elif "postgres" in db_lower:
            lines += ["# PostgreSQL connection", "DATABASE_URL=postgresql://user:password@localhost:5432/myapp", ""]
        elif "mysql" in db_lower:
            lines += ["# MySQL connection", "DATABASE_URL=mysql://user:password@localhost:3306/myapp", ""]
        elif "redis" in db_lower:
            lines += ["# Redis connection", "REDIS_URL=redis://localhost:6379", ""]

    env_vars = metadata.get("env_variables") or []
    if env_vars:
        lines.append("# Detected application environment variables")
        for var in env_vars[:30]:
            lines.append(f"{var}=")
        lines.append("")

    port = metadata.get("port") or metadata.get("backend_port") or 8000
    lines += [f"PORT={port}", "NODE_ENV=production", ""]
    return "\n".join(lines)


def _write_generated_file(project_root: str, rel_path: str, content: str) -> bool:
    """Write a generated file safely under project_root. Returns True on success."""
    root_abs = os.path.abspath(project_root)
    clean = rel_path.replace("\\", "/").lstrip("/")
    dest = os.path.abspath(os.path.join(root_abs, clean))
    if not dest.startswith(root_abs):
        return False
    try:
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "w", encoding="utf-8") as f:
            f.write(content.rstrip() + "\n")
        print(f"[readiness] Gemini wrote {clean} -> {dest}")
        return True
    except Exception as e:
        print(f"[readiness] Failed to write {rel_path}: {e}")
        return False


# ─── Main handler ─────────────────────────────────────────────────────────────

async def check_readiness_handler(project_id: str, current_user: dict) -> Dict:
    """
    Main handler for GET /api/docker/{project_id}/check-readiness.

    Steps:
    1. Load project from DB
    2. Scan for existing deployment files
    3. Auto-generate missing files via Gemini
    4. Save generated files to project directory
    5. Return structured readiness report
    """
    if not ObjectId.is_valid(project_id):
        raise HTTPException(status_code=400, detail="Invalid project ID format")

    collection = get_projects_collection()
    project = await collection.find_one({"_id": ObjectId(project_id)})

    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    if project.get("user_id") != str(current_user["_id"]):
        raise HTTPException(status_code=403, detail="Access denied: Not project owner")

    if project.get("status") not in ("analyzed", "completed"):
        raise HTTPException(
            status_code=400,
            detail="Project must be analyzed before checking readiness",
        )

    # Resolve project root
    from ..utils.detector import find_project_root as _find_root

    extracted_path = project.get("extracted_path") or settings.EXTRACTED_DIR
    real_path = os.path.abspath(extracted_path)
    if not os.path.exists(real_path):
        raise HTTPException(status_code=400, detail="Extracted project files not found")

    project_root = _find_root(real_path)
    project_name = project.get("project_name", "project")
    metadata = dict(project.get("metadata") or {})
    services = metadata.get("services") or []

    # Step 1: Scan
    file_status = _scan_deployment_files(project_root, services)
    missing = [k for k, v in file_status.items() if not v]
    present = [k for k, v in file_status.items() if v]

    generated_files: Dict[str, str] = {}
    generation_errors: List[str] = []
    skipped: List[str] = []

    # Step 2: Generate missing Docker files
    need_docker = not file_status["Dockerfile"]
    need_compose = not file_status["docker-compose.yml"]

    # _generate_docker_files / _generate_k8s_files make a synchronous
    # (requests-based) LLM call under the hood; calling them directly here would
    # block the whole event loop for the duration of the call. Offload to a thread.
    if need_docker or need_compose:
        try:
            docker_gen = await run_in_threadpool(
                _generate_docker_files,
                project_root=project_root,
                project_name=project_name,
                metadata=metadata,
                services=services,
                has_dockerfile=file_status["Dockerfile"],
                has_compose=file_status["docker-compose.yml"],
            )
            generated_files.update(docker_gen)
        except Exception as e:
            generation_errors.append(f"Docker file generation failed: {e}")

    # Step 3: Generate missing k8s manifests
    if not file_status["k8s/deployment.yaml"] or not file_status["k8s/service.yaml"]:
        try:
            k8s_gen = await run_in_threadpool(
                _generate_k8s_files,
                project_root=project_root,
                project_name=project_name,
                metadata=metadata,
                services=services,
            )
            generated_files.update(k8s_gen)
        except Exception as e:
            generation_errors.append(f"k8s manifest generation failed: {e}")

    # Step 4: Generate .env template if missing (do NOT overwrite existing .env)
    if not file_status[".env"] and ".env" not in generated_files:
        env_template = _generate_env_template(services, metadata)
        generated_files[".env.template"] = env_template
        skipped.append(".env (generated .env.template instead — fill in real secrets)")

    # Step 5: Write generated files
    written: List[str] = []
    write_errors: List[str] = []
    for rel_path, content in generated_files.items():
        if _write_generated_file(project_root, rel_path, content):
            written.append(rel_path)
        else:
            write_errors.append(rel_path)

    # Re-scan after generation
    file_status_after = _scan_deployment_files(project_root, services)
    still_missing = [k for k, v in file_status_after.items() if not v and k != ".env"]

    # Build full readiness summary
    is_ready = len(still_missing) == 0 or still_missing == [".env"]

    return {
        "success": True,
        "project_id": project_id,
        "project_name": project_name,
        "is_ready": is_ready,
        "files": {
            "present_before": present,
            "missing_before": missing,
            "generated": written,
            "still_missing": still_missing,
            "skipped": skipped,
            "write_errors": write_errors,
        },
        "generation_errors": generation_errors,
        "project_root": project_root,
        "k8s_node_port": _node_port_for(project_root),
        "image_repo": _image_repo_for(project_name),
        "message": (
            "All deployment files are ready. You can now build, push, and deploy."
            if is_ready
            else f"Some files could not be generated: {still_missing}. Please add them manually."
        ),
    }
