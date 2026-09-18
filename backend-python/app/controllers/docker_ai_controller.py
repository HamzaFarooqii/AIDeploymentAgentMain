import os
import shutil
from typing import Dict, List, Optional, Tuple

from bson import ObjectId
from fastapi import HTTPException
from starlette.concurrency import run_in_threadpool

from ..LLM.docker_deploy_agent import run_docker_deploy_chat, run_docker_deploy_chat_stream
from ..config.database import get_projects_collection
from ..config.settings import settings
from ..utils.auth import decode_access_token
from ..utils.file_system import read_file
from ..utils.detector import find_project_root
from ..utils.detection_constants import PORT_SCHEMA_VERSION, SSR_FRONTEND_BUILD_OUTPUTS
from ..utils.detection_services import infer_service_runtime_image_from_code
from ..utils.image_naming import build_project_image_repo

from ..services.docker_service import (
    build_project_stream,
    run_project_stream,
    push_image_stream,
    k8s_deploy_stream,
    _collect_source_files_for_llm,
)

_ENV_FILE_CANDIDATES = (".env", ".env.local", ".env.production")
_NODE_ENTRY_CANDIDATES = ("server.js", "index.js", "app.js", "main.js")


async def _validate_project(project_id: str, current_user: dict) -> Dict:
    if not ObjectId.is_valid(project_id):
        raise HTTPException(status_code=400, detail="Invalid project ID format")

    collection = get_projects_collection()
    project = await collection.find_one({"_id": ObjectId(project_id)})

    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    if project.get("user_id") != str(current_user["_id"]):
        raise HTTPException(status_code=403, detail="Access denied: Not project owner")

    return project


def _safe_project_path(project: Dict) -> str:
    extracted_path = project.get("extracted_path") or settings.EXTRACTED_DIR
    real_path = os.path.abspath(extracted_path)
    if not os.path.exists(real_path):
        raise HTTPException(status_code=400, detail="Extracted project files not found")
    return real_path


def _resolve_project_root(project: Dict) -> str:
    return find_project_root(_safe_project_path(project))


def _resolve_service_dir(project_root: str, service_path: str) -> str:
    svc_path = str(service_path or ".").rstrip("/\\")
    if svc_path in ("", "."):
        return project_root
    return os.path.join(project_root, svc_path)


def _augment_services_runtime_hints(
    services: List[Dict],
    project_root: str,
    refresh_env: bool = False,
) -> List[Dict]:
    from ..utils.command_extractor import extract_nodejs_commands

    def _to_int(value: object) -> Optional[int]:
        try:
            if value is None:
                return None
            return int(str(value))
        except (TypeError, ValueError):
            return None

    for svc in services:
        if not isinstance(svc, dict):
            continue

        svc_path = str(svc.get("path", ".")).rstrip("/\\")
        svc_dir = _resolve_service_dir(project_root, svc_path)

        if refresh_env or not svc.get("env_file"):
            env_found = False
            for env_name in _ENV_FILE_CANDIDATES:
                env_path = os.path.join(svc_dir, env_name)
                root_env_path = os.path.join(project_root, env_name)
                
                # Check service dir first
                if os.path.exists(env_path):
                    if svc_path and svc_path != ".":
                        normalized_path = svc_path.replace("\\", "/")
                        svc["env_file"] = f"./{normalized_path}/{env_name}"
                    else:
                        svc["env_file"] = f"./{env_name}"
                    env_found = True
                    break
                # Fallback to root dir if different
                elif svc_path and svc_path != "." and os.path.exists(root_env_path):
                    svc["env_file"] = f"./{env_name}"
                    env_found = True
                    break

        if svc.get("type") in ("backend", "monolith") and not svc.get("entry_point"):
            backend_cmds = extract_nodejs_commands(svc_dir)
            entry_point = backend_cmds.get("entry_point")
            if not entry_point:
                for candidate in _NODE_ENTRY_CANDIDATES:
                    if os.path.exists(os.path.join(svc_dir, candidate)):
                        entry_point = candidate
                        break
            if entry_point:
                svc["entry_point"] = entry_point

        if not svc.get("runtime"):
            svc_type = str(svc.get("type") or "other").strip().lower()
            svc_language = svc.get("language")
            if not svc_language:
                if os.path.exists(os.path.join(svc_dir, "package.json")):
                    svc_language = "JavaScript"
                elif any(
                    os.path.exists(os.path.join(svc_dir, marker))
                    for marker in ("requirements.txt", "pyproject.toml", "manage.py")
                ):
                    svc_language = "Python"
            svc_framework = svc.get("framework")

            frontend_mode = None
            if svc_type == "frontend":
                frontend_mode = str(svc.get("frontend_mode") or "").strip().lower() or None
                if not frontend_mode:
                    container_source = str(svc.get("container_port_source") or "").strip().lower()
                    build_output = str(svc.get("build_output") or "").strip().lower()
                    runtime_port = _to_int(
                        svc.get("runtime_port")
                        if svc.get("runtime_port") is not None
                        else svc.get("port")
                    )
                    container_port = _to_int(svc.get("container_port"))

                    if container_source == "dev_server":
                        frontend_mode = "dev_server"
                    elif (
                        container_source in {"next_default", "ssr_default"}
                        or build_output in SSR_FRONTEND_BUILD_OUTPUTS
                    ):
                        frontend_mode = "ssr"
                    elif (
                        runtime_port is not None
                        and container_port is not None
                        and runtime_port == container_port
                    ):
                        frontend_mode = "dev_server"
                    else:
                        frontend_mode = "static_nginx"

            inferred_runtime = infer_service_runtime_image_from_code(
                svc_abs_path=svc_dir,
                svc_type=svc_type,
                svc_language=str(svc_language or "Unknown"),
                svc_framework=str(svc_framework or "Unknown"),
                frontend_mode=frontend_mode,
            )
            if inferred_runtime:
                svc["runtime"] = inferred_runtime

    return services


async def _get_user_from_token(token: Optional[str]) -> Optional[Dict]:
    """
    Decode a JWT token and fetch the user document. Returns None if token is not provided.
    """
    if not token:
        return None
    payload = decode_access_token(token)
    user_id = payload.get("sub")
    if not user_id or not ObjectId.is_valid(user_id):
        raise HTTPException(status_code=401, detail="Invalid token")
    collection = get_projects_collection().database.get_collection("users")
    user = await collection.find_one({"_id": ObjectId(user_id)})
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user


def _collect_docker_files(project_root: str) -> Tuple[List[Dict[str, str]], List[Dict[str, str]]]:
    dockerfiles: List[Dict[str, str]] = []
    compose_files: List[Dict[str, str]] = []

    targets = ["Dockerfile", "dockerfile"]
    compose_targets = ["docker-compose.yml", "docker-compose.yaml"]

    for root, _, files in os.walk(project_root):
        for name in files:
            full_path = os.path.join(root, name)
            rel_path = os.path.relpath(full_path, project_root)

            if name in targets:
                content = read_file(full_path) or ""
                dockerfiles.append({"path": rel_path, "content": content})
            if name in compose_targets:
                content = read_file(full_path) or ""
                compose_files.append({"path": rel_path, "content": content})

    return dockerfiles, compose_files


def _collect_source_files(project_root: str, max_files: int = 5) -> List[Dict[str, str]]:
    source_files: List[Dict[str, str]] = []
    
    # Priority targets for understanding dependencies and endpoints
    targets = ["package.json", "server.js", "app.js", "index.js", "main.py", "requirements.txt"]
    
    collected = 0
    for root, _, files in os.walk(project_root):
        if "node_modules" in root or ".venv" in root or "venv" in root or "__pycache__" in root:
            continue
            
        for name in files:
            if collected >= max_files:
                break
            # Match directly or by typical entrypoint names
            if name in targets or name.endswith(".js") or name.endswith(".ts") or name.endswith(".py"):
                full_path = os.path.join(root, name)
                rel_path = os.path.relpath(full_path, project_root)
                
                content = read_file(full_path) or ""
                # Prevent massive minified files
                if len(content) > 0 and len(content) < 50000:
                    source_files.append({"path": rel_path, "content": content})
                    collected += 1

    return source_files


def _build_file_tree(project_root: str, max_depth: int = 4, max_entries: int = 200) -> Tuple[str, List[Dict]]:
    """
    Returns a simple indented text tree for Llama context and a structured tree for the UI.
    """
    lines: List[str] = []
    structured: List[Dict] = []
    count = 0

    def walk(current: str, depth: int) -> Optional[List[Dict]]:
        nonlocal count
        if depth > max_depth or count >= max_entries:
            return []

        entries = []
        try:
            with os.scandir(current) as it:
                for entry in it:
                    if count >= max_entries:
                        break
                    if entry.name.startswith(".") and entry.name not in [".env", ".env.example"]:
                        continue
                    count += 1
                    rel_path = os.path.relpath(entry.path, project_root)
                    prefix = "  " * depth + ("[dir] " if entry.is_dir() else "[file] ")
                    lines.append(f"{prefix}{rel_path}")

                    node = {
                        "name": entry.name,
                        "path": rel_path.replace("\\", "/"),
                        "is_dir": entry.is_dir(),
                    }
                    if entry.is_dir():
                        node["children"] = walk(entry.path, depth + 1)
                    entries.append(node)
        except Exception:
            return entries
        return entries

    structured = walk(project_root, 0) or []
    tree_text = "\n".join(lines)
    if count >= max_entries:
        tree_text += "\n...truncated"
    return tree_text, structured


def _collect_k8s_files(project_root: str) -> List[Dict[str, str]]:
    """Collect k8s manifest files from project_root/k8s/."""
    k8s_dir = os.path.join(project_root, "k8s")
    k8s_files: List[Dict[str, str]] = []
    if not os.path.isdir(k8s_dir):
        return k8s_files
    for name in sorted(os.listdir(k8s_dir)):
        if name.endswith(".yaml") or name.endswith(".yml"):
            full_path = os.path.join(k8s_dir, name)
            content = read_file(full_path) or ""
            k8s_files.append({"path": f"k8s/{name}", "content": content})
    return k8s_files


def _ensure_analyzed(project: Dict):
    if project.get("status") not in ["analyzed", "completed"]:
        raise HTTPException(status_code=400, detail="Project must be analyzed before Docker deploy")


async def get_docker_context_handler(project_id: str, current_user: dict) -> Dict:
    project = await _validate_project(project_id, current_user)
    _ensure_analyzed(project)
    project_root = _resolve_project_root(project)

    dockerfiles, compose_files = _collect_docker_files(project_root)
    file_tree_text, file_tree_struct = _build_file_tree(project_root)

    # Get stored metadata
    metadata = dict(project.get("metadata", {}))
    metadata.setdefault("schema_version", PORT_SCHEMA_VERSION)
    services = metadata.get("services", [])

    # Re-check for env files on page load to handle newly added files.
    services = _augment_services_runtime_hints(services, project_root, refresh_env=True)

    # Recalculate deploy_blocked based on current env_file status
    backend_services = [s for s in services if s.get("type") in ("backend", "monolith")]
    backend_missing_env = any(
        svc.get("type") in ("backend", "monolith") and not svc.get("env_file")
        for svc in services
    )
    
    if backend_services and backend_missing_env:
        # No longer block — the build pipeline auto-generates .env via Gemini
        metadata["deploy_blocked"] = False
        metadata["deploy_blocked_reason"] = None
        metadata["backend_env_missing"] = True
        if metadata.get("database") != "Unknown":
            metadata["deploy_warning"] = (
                "No .env file found. One will be auto-generated before build using Gemini AI."
            )
        else:
            metadata["deploy_warning"] = (
                "No .env detected. Auto-generating template — update with real secrets if needed."
            )
    else:
        metadata["deploy_blocked"] = False
        metadata["deploy_blocked_reason"] = None
        metadata["backend_env_missing"] = False
        metadata["deploy_warning"] = None
    
    # Update services in metadata
    metadata["services"] = services

    # Collect k8s manifests
    k8s_files = _collect_k8s_files(project_root)

    # Compute missing deployment files for the UI
    has_dockerfile = bool(dockerfiles)
    has_compose = bool(compose_files)
    has_k8s = bool(k8s_files)
    has_env = any(
        svc.get("env_file") for svc in services
    ) or os.path.exists(os.path.join(project_root, ".env"))

    missing_files = []
    if not has_dockerfile:
        missing_files.append("Dockerfile")
    if not has_compose:
        missing_files.append("docker-compose.yml")
    if not has_k8s:
        missing_files.append("k8s/deployment.yaml")
        missing_files.append("k8s/service.yaml")
    if not has_env:
        missing_files.append(".env")

    image_repo = build_project_image_repo(
        project.get("project_name", "project"),
        settings.DOCKER_HUB_USERNAME,
        settings.APP_REGISTRY_PREFIX,
    )

    node_port = 30000 + (hash(project_root) % 2767)
    node_port = max(30000, min(32767, node_port))

    return {
        "success": True,
        "project": {
            "id": str(project["_id"]),
            "project_name": project.get("project_name"),
            "status": project.get("status"),
        },
        "metadata": metadata,
        "dockerfiles": dockerfiles,
        "compose_files": compose_files,
        "k8s_files": k8s_files,
        "file_tree": {
            "text": file_tree_text,
            "tree": file_tree_struct,
        },
        "docker_compose_present": bool(compose_files),
        "k8s_manifests_present": has_k8s,
        "missing_files": missing_files,
        "deployment_readiness": {
            "has_dockerfile": has_dockerfile,
            "has_docker_compose": has_compose,
            "has_k8s_manifests": has_k8s,
            "has_env": has_env,
            "is_ready": len(missing_files) == 0,
            "missing": missing_files,
        },
        "image_repo": image_repo,
        "k8s_node_port": node_port,
    }


async def docker_chat_handler(
    project_id: str,
    current_user: dict,
    user_message: str,
    logs: Optional[List[str]] = None,
    instructions: Optional[str] = None,
) -> Dict:
    project = await _validate_project(project_id, current_user)
    _ensure_analyzed(project)
    project_root = _resolve_project_root(project)

    dockerfiles, compose_files = _collect_docker_files(project_root)
    file_tree_text, _ = _build_file_tree(project_root)
    metadata = project.get("metadata", {}) or {}
    metadata.setdefault("schema_version", PORT_SCHEMA_VERSION)
    services = metadata.get("services") or []

    services = _augment_services_runtime_hints(services, project_root, refresh_env=False)

    # run_docker_deploy_chat makes a synchronous (requests-based) LLM call that can
    # take a long time; calling it directly here would block the whole event loop
    # (and therefore every other concurrent request, including health checks) for
    # the duration of the call. Offload it to a worker thread instead.
    reply = await run_in_threadpool(
        run_docker_deploy_chat,
        project_name=project.get("project_name", "project"),
        metadata=metadata,
        dockerfiles=dockerfiles,
        compose_files=compose_files,
        source_files=_collect_source_files_for_llm(project_root),
        file_tree=file_tree_text,
        user_message=user_message,
        logs=logs,
        extra_instructions=instructions,
        services=services,
    )

    return {
        "success": True,
        "reply": reply,
        "model": "qwen2.5-coder",
        "dockerfiles_found": bool(dockerfiles),
        # Frontend should append ?action=build|run|push as needed
        "log_stream_base_url": f"/api/docker/{project_id}/logs",
    }


def docker_chat_stream_handler(
    project_id: str,
    current_user: dict,
    user_message: str,
    logs: Optional[List[str]] = None,
    instructions: Optional[str] = None,
):
    """
    Generator that yields SSE events with LLM tokens.
    Synchronous generator for use with StreamingResponse.
    """
    import asyncio
    
    # We need to run async validation synchronously for the generator
    loop = asyncio.new_event_loop()
    try:
        project = loop.run_until_complete(_validate_project(project_id, current_user))
    finally:
        loop.close()
    
    _ensure_analyzed(project)
    project_root = _resolve_project_root(project)

    dockerfiles, compose_files = _collect_docker_files(project_root)
    file_tree_text, _ = _build_file_tree(project_root)
    metadata = project.get("metadata", {}) or {}
    metadata.setdefault("schema_version", PORT_SCHEMA_VERSION)
    services = metadata.get("services") or []

    services = _augment_services_runtime_hints(services, project_root, refresh_env=False)

    # Yield tokens from streaming LLM
    for chunk in run_docker_deploy_chat_stream(
        project_name=project.get("project_name", "project"),
        metadata=metadata,
        dockerfiles=dockerfiles,
        compose_files=compose_files,
        file_tree=file_tree_text,
        user_message=user_message,
        logs=logs,
        extra_instructions=instructions,
        services=services,
    ):
        yield chunk


async def docker_chat_stream_setup(
    project_id: str,
    current_user: dict,
    user_message: str,
    logs: Optional[List[str]] = None,
    instructions: Optional[str] = None,
) -> Dict:
    """
    Async function to validate and prepare data for streaming.
    Returns all the data needed by the sync generator.
    """
    project = await _validate_project(project_id, current_user)
    _ensure_analyzed(project)
    project_root = _resolve_project_root(project)

    dockerfiles, compose_files = _collect_docker_files(project_root)
    source_files = _collect_source_files(project_root)
    file_tree_text, _ = _build_file_tree(project_root)
    metadata = project.get("metadata", {}) or {}
    metadata.setdefault("schema_version", PORT_SCHEMA_VERSION)
    services = metadata.get("services") or []

    services = _augment_services_runtime_hints(services, project_root, refresh_env=False)

    return {
        "project_name": project.get("project_name", "project"),
        "metadata": metadata,
        "dockerfiles": dockerfiles,
        "compose_files": compose_files,
        "source_files": source_files,
        "file_tree": file_tree_text,
        "user_message": user_message,
        "logs": logs,
        "instructions": instructions,
        "services": services,
    }


def docker_chat_stream_generator(prepared_data: Dict):
    """
    Sync generator that yields SSE events with LLM tokens.
    Takes pre-validated data from docker_chat_stream_setup.
    """
    for chunk in run_docker_deploy_chat_stream(
        project_name=prepared_data["project_name"],
        metadata=prepared_data["metadata"],
        dockerfiles=prepared_data["dockerfiles"],
        compose_files=prepared_data["compose_files"],
        source_files=prepared_data.get("source_files", []),
        file_tree=prepared_data["file_tree"],
        user_message=prepared_data["user_message"],
        logs=prepared_data["logs"],
        extra_instructions=prepared_data["instructions"],
        services=prepared_data["services"],
    ):
        yield chunk


async def read_project_file_handler(
    project_id: str, current_user: dict, relative_path: str
) -> Dict:
    project = await _validate_project(project_id, current_user)
    project_root = _resolve_project_root(project)

    normalized = os.path.abspath(os.path.join(project_root, relative_path))
    if not normalized.startswith(os.path.abspath(project_root)):
        raise HTTPException(status_code=400, detail="Invalid path")

    if not os.path.exists(normalized):
        raise HTTPException(status_code=404, detail="File not found")

    # Read as binary then decode with replacement to avoid binary decode errors (e.g., favicon.ico)
    try:
        with open(normalized, "rb") as f:
            raw = f.read()
        content = raw.decode("utf-8", errors="replace")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Unable to read file: {exc}")

    return {
        "success": True,
        "path": relative_path.replace("\\", "/"),
        "content": content,
    }


async def write_project_file_handler(
    project_id: str, current_user: dict, relative_path: str, content: str
) -> Dict:
    project = await _validate_project(project_id, current_user)
    project_root = _resolve_project_root(project)

    normalized = os.path.abspath(os.path.join(project_root, relative_path))
    if not normalized.startswith(os.path.abspath(project_root)):
        raise HTTPException(status_code=400, detail="Invalid path")

    if not relative_path.strip():
        raise HTTPException(status_code=400, detail="Path is required")

    try:
        os.makedirs(os.path.dirname(normalized), exist_ok=True)
        with open(normalized, "w", encoding="utf-8") as f:
            f.write(content)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Unable to write file: {exc}")

    return {
        "success": True,
        "path": relative_path.replace("\\", "/"),
        "location": normalized,
    }


async def create_project_folder_handler(
    project_id: str, current_user: dict, relative_path: str
) -> Dict:
    """
    Create a folder (and parents) under the project root.
    """
    project = await _validate_project(project_id, current_user)
    project_root = _resolve_project_root(project)

    if not relative_path or not relative_path.strip():
        raise HTTPException(status_code=400, detail="Folder path is required")

    normalized = os.path.abspath(os.path.join(project_root, relative_path))
    if not normalized.startswith(os.path.abspath(project_root)):
        raise HTTPException(status_code=400, detail="Invalid path")

    try:
        os.makedirs(normalized, exist_ok=True)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Unable to create folder: {exc}")

    return {"success": True, "path": relative_path.replace("\\", "/")}


async def delete_project_path_handler(
    project_id: str, current_user: dict, relative_path: str
) -> Dict:
    """
    Delete a file or directory (recursively for directories) under the project root.
    """
    project = await _validate_project(project_id, current_user)
    project_root = _resolve_project_root(project)

    if not relative_path or not relative_path.strip():
        raise HTTPException(status_code=400, detail="Path is required")

    normalized_root = os.path.abspath(project_root)
    normalized = os.path.abspath(os.path.join(project_root, relative_path))
    if not normalized.startswith(normalized_root):
        raise HTTPException(status_code=400, detail="Invalid path")

    if normalized == normalized_root:
        raise HTTPException(status_code=400, detail="Cannot delete project root")

    if not os.path.exists(normalized):
        raise HTTPException(status_code=404, detail="File or folder not found")

    try:
        if os.path.isdir(normalized):
            shutil.rmtree(normalized)
        else:
            os.remove(normalized)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Unable to delete path: {exc}")

    return {"success": True, "path": relative_path.replace("\\", "/")}


async def stream_docker_logs_handler(
    project_id: str,
    action: str,
    token: Optional[str],
    authorization_header: Optional[str],
):
    bearer_token = token
    if not bearer_token and authorization_header:
        if authorization_header.lower().startswith("bearer "):
            bearer_token = authorization_header.split(" ", 1)[1].strip()

    user = await _get_user_from_token(bearer_token)
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")

    project = await _validate_project(project_id, user)
    _ensure_analyzed(project)
    project_root = _resolve_project_root(project)

    metadata = project.get("metadata", {}) or {}
    backend_host_port = metadata.get("backend_port") or metadata.get("port") or 8000

    image_repo = build_project_image_repo(
        project.get("project_name", "unnamed"),
        settings.DOCKER_HUB_USERNAME,
        settings.APP_REGISTRY_PREFIX,
    )

    if action == "build":
        generator = build_project_stream(project_root, image_repo, metadata)
    elif action == "run":
        generator = run_project_stream(project_root, image_repo, backend_host_port, metadata)
    elif action == "push":
        generator = push_image_stream(project_root, image_repo, metadata)
    elif action == "k8s_deploy":
        generator = k8s_deploy_stream(project_root, image_repo, metadata)
    else:
        raise HTTPException(status_code=400, detail="Invalid action")

    return generator
