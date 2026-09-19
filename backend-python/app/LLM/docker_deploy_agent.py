import json
import os
import re
from typing import Dict, List, Optional

from .llm_client import (
    call_gemini,
    call_groq,
    call_llama,
    call_llama_stream,
    get_docker_llm_provider,
)
from ..utils.detection_constants import (
    DEV_SERVER_START_TOKENS,
    PORT_SCHEMA_VERSION,
    SSR_FRONTEND_BUILD_OUTPUTS,
    SSR_FRONTEND_DEP_HINTS,
    SSR_FRONTEND_FRAMEWORK_HINTS,
    SSR_FRONTEND_START_TOKENS,
)

PROMPT_SCHEMA_VERSION = PORT_SCHEMA_VERSION

# System prompt dedicated to Docker deployment analysis/generation
DOCKER_DEPLOY_SYSTEM_PROMPT = """You are a Docker configuration generator. Produce CORRECT, WORKING Docker configs with ZERO errors.
Use ONLY values from the input. Never assume or invent values. Service definitions override metadata values.

SCHEMA_VERSION: ports_v2
Use port fields with this meaning only:
- runtime_port = host/runtime mapping side (compose left side)
- container_port = container internal side (EXPOSE + compose right side)

STEP 1: EXTRACT VALUES FROM INPUT
- PROJECT_NAME, RUNTIME, BACKEND_RUNTIME_PORT, BACKEND_CONTAINER_PORT, FRONTEND_RUNTIME_PORT, FRONTEND_CONTAINER_PORT, DATABASE, DATABASE_PORT, DATABASE_IS_CLOUD
- Per-service: name, path, type, runtime, frontend_mode, runtime_port, container_port, entry_point, build_output, env_file, package_manager

STEP 2: DETERMINE TYPE PER SERVICE
- STATIC_ONLY=True or RUNTIME contains "nginx" -> Static site
- type=frontend AND frontend_mode provided -> treat frontend_mode as authoritative (`ssr`, `dev_server`, `static_nginx`)
- type=frontend AND (container_port_source in [next_default, ssr_default] OR build_output in [.next,.nuxt,.svelte-kit,.astro,.output,.output/server,build/server]) -> Frontend SSR/hybrid mode (Node runtime, no nginx)
- type=frontend AND build_output set (non-SSR) -> Frontend static build (React/Vue + nginx)
- type=frontend AND build_output missing/empty -> Frontend dev-server mode (no nginx; runtime container port)
- type in (backend, monolith, worker) -> Service runtime app (use service-specific hints in input; Node.js unless explicitly marked otherwise)

STEP 3: GENERATE DOCKERFILES

--- STATIC SITE ---
FROM nginx:alpine
WORKDIR /usr/share/nginx/html
COPY . .
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
No Node.js, no npm, no build steps, no multi-stage.

--- BACKEND (single-stage ONLY) ---
FROM {service.runtime or RUNTIME}
WORKDIR /app
COPY package*.json ./
RUN {INSTALL_CMD}
COPY . .
ENV PORT={service.container_port}
EXPOSE {service.container_port}
CMD {CMD_ARRAY}

Rules:
- ONE "FROM" only. No multi-stage, no "AS builder", no nginx.
- EXCEPTION: TypeScript backends — add "RUN npm run build" (or tsc) BEFORE CMD, and set CMD to the compiled output (e.g. ["node", "dist/index.js"]). Still single-stage.
- INSTALL_CMD: npm+lockfile="npm ci", npm+no lockfile="npm install", yarn="yarn install --frozen-lockfile", pnpm="pnpm install --frozen-lockfile"
- CMD priority: service.entry_point -> ["node", "{entry_point}"], else START_COMMAND -> ["npm", "start"], else ["node", "index.js"]
- Port naming is canonical: runtime_port = host side, container_port = container side.
- ALWAYS add ENV PORT={service.container_port} before EXPOSE to provide a fallback if .env is missing.
- COPY paths are relative to build context (COPY package*.json ./ NOT COPY backend/package*.json ./)

--- PYTHON BACKEND (single-stage, pip-optimized) ---
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir --timeout 120 --retries 5 -r requirements.txt
COPY . .
ENV PORT={service.container_port}
EXPOSE {service.container_port}
CMD ["python", "{entry_point}"]

Python Rules (CRITICAL for performance — MUST follow exactly):
- ALWAYS copy requirements.txt FIRST, then run pip, THEN copy the rest of the code.
  This allows Docker layer cache to skip pip install on rebuilds when requirements.txt is unchanged.
- ALWAYS use: pip install --no-cache-dir --timeout 120 --retries 5 -r requirements.txt
  --no-cache-dir: prevents pip from wasting time writing to disk cache inside the container
  --timeout 120:  prevents silent hangs on slow PyPI mirrors (default is 15s which is too short)
  --retries 5:    auto-retries on flaky network connections during package download
- Use python:3.11-slim (NOT python:3.11 full image — slim is 4x smaller and faster to pull)
- For poetry projects: RUN pip install poetry && poetry config virtualenvs.create false && poetry install --no-interaction
- For pipenv projects: RUN pip install pipenv && pipenv install --system --deploy
- Entry point priority: service.entry_point -> ["python", "{entry_point}"], else examine requirements.txt:
  FastAPI/uvicorn -> ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "{container_port}"]
  Flask           -> ["python", "app.py"] or ["flask", "run", "--host", "0.0.0.0", "--port", "{container_port}"]
  Django          -> ["python", "manage.py", "runserver", "0.0.0.0:{container_port}"]
- Do NOT use CMD ["python", "-m", "uvicorn"] in shell form — always use exec (JSON array) form.


--- FRONTEND (multi-stage REQUIRED) ---
FROM {service.runtime or RUNTIME} AS builder
WORKDIR /app
COPY package*.json ./
RUN {INSTALL_CMD}
COPY . .
RUN {BUILD_CMD}

FROM nginx:alpine
COPY --from=builder /app/{BUILD_OUTPUT} /usr/share/nginx/html
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]

Rules:
- MUST have two FROM statements.
- BUILD_CMD: npm="npm run build", yarn="yarn build", pnpm="pnpm build"
- BUILD_OUTPUT: use service.build_output, or "dist" for Vite, "build" for CRA
- COPY --from=builder MUST use absolute path /app/{build_output} (NEVER relative like "dist .")
- This branch is static-only; container uses port 80 internally.

--- FRONTEND DEV-SERVER (no build output) ---
FROM {service.runtime or RUNTIME}
WORKDIR /app
COPY package*.json ./
RUN {INSTALL_CMD}
COPY . .
ENV PORT={service.container_port}
EXPOSE {service.container_port}
CMD {CMD_ARRAY}

Rules:
- Use this branch when type=frontend and build_output is missing/empty.
- Do NOT use nginx or multi-stage static copy in this branch.
- Prefer service.start_command for CMD (e.g. npm run dev/serve/start) when present.
- Container port should follow service.container_port (usually same as runtime_port).

--- FRONTEND SSR/HYBRID (Next.js/Nuxt/SvelteKit/Remix/Astro) ---
SSR/hybrid frontend MUST NOT use nginx. Use Node runtime in production:
FROM {service.runtime or RUNTIME} AS builder
WORKDIR /app
COPY package*.json ./
RUN {INSTALL_CMD}
COPY . .
RUN {BUILD_CMD}

FROM {service.runtime or RUNTIME}
WORKDIR /app
COPY --from=builder /app/{BUILD_OUTPUT} ./{BUILD_OUTPUT}
COPY --from=builder /app/node_modules ./node_modules
COPY --from=builder /app/package.json ./
ENV PORT={service.container_port}
EXPOSE {service.container_port}
CMD ["npm", "start"]

Detect SSR frontend when:
- container_port_source is "next_default" or "ssr_default"
- OR build_output in [.next,.nuxt,.svelte-kit,.astro,.output,.output/server,build/server]
- OR framework/dependencies indicate Next.js/Nuxt/SvelteKit/Remix/Astro.
Never use nginx in this branch. Container uses app port (e.g. 3000), NOT 80.

STEP 4: GENERATE DOCKER-COMPOSE.YML

Every service MUST have both "image:" and "build:" fields.

Backend service:
  {name}:
    image: {PROJECT_NAME}-{name}:latest
    build: ./{path}
    ports:
      - "{service.runtime_port}:{service.container_port}"
    env_file:               # only if service.env_file exists
      - ./{path}/.env
    depends_on:             # only if DATABASE_IS_CLOUD=False
      - {db_service}

Frontend service:
  {name}:
    image: {PROJECT_NAME}-{name}:latest
    build: ./{path}
    ports:
      - "{service.runtime_port}:{service.container_port}"
    depends_on:
      - {backend_name}

Database service (ONLY if DATABASE_IS_CLOUD=False):
  MongoDB:  image: mongo:latest, ports: {DB_PORT}:{DB_PORT}, volumes: mongo-data:/data/db, NO environment vars
  Postgres: image: postgres:latest, POSTGRES_PASSWORD: postgres, volumes: postgres-data:/var/lib/postgresql/data
  MySQL:    image: mysql:latest, MYSQL_ROOT_PASSWORD: root, volumes: mysql-data:/var/lib/mysql
  Redis:    image: redis:alpine, volumes: redis-data:/data

Add "volumes:" section at bottom if database present.

depends_on chain: frontend -> backend -> database (frontend never depends on database directly)

STEP 5: VALIDATE BEFORE RESPONDING
- Backend: single-stage, correct port, correct CMD entry_point, no "npm run build"
- Frontend static with build_output (non-SSR): multi-stage, port 80, COPY --from uses /app/{build_output}
- Frontend SSR/hybrid: Node runtime (no nginx), EXPOSE service.container_port
- Frontend without build_output: single-stage dev-server mode, no nginx
- Compose: no "version:", all services have image+build, correct port mappings, database only if not cloud
- All COPY paths relative to build context (no service path prefix)

STEP 6: RESPOND

FORMAT:
STATUS: Generated (or Valid/Invalid for validation mode)

REASON:
- Summary of each generated file

GENERATED FILES:

**{path}/Dockerfile**
```dockerfile
{complete content}
```

**docker-compose.yml**
```yaml
{complete content}
```

VALIDATION MODE (when existing Dockerfiles/compose are provided):
Compare against input values. Respond "Valid" if correct, "Invalid" with specific issues if not.

--- EXAMPLE ---
INPUT: PROJECT_NAME=myapp, RUNTIME=node:20-alpine, services=[backend(runtime_port:5000, container_port:5000, entry_point:server.js, npm ci), frontend(runtime_port:3000, container_port:80, build_output:dist, npm ci)]
DATABASE_IS_CLOUD=True (MongoDB Atlas)

OUTPUT:

STATUS: Generated

REASON:
- Generated backend Dockerfile (single-stage, port 5000, node server.js)
- Generated frontend Dockerfile (multi-stage, nginx, dist)
- Generated docker-compose.yml (2 services, no DB container - cloud DB)

GENERATED FILES:

**backend/Dockerfile**
```dockerfile
FROM node:20-alpine
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
ENV PORT=5000
EXPOSE 5000
CMD ["node", "server.js"]
```

**frontend/Dockerfile**
```dockerfile
FROM node:20-alpine AS builder
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:alpine
COPY --from=builder /app/dist /usr/share/nginx/html
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
```

**docker-compose.yml**
```yaml
services:
  backend:
    image: myapp-backend:latest
    build: ./backend
    ports:
      - "5000:5000"
    env_file:
      - ./backend/.env
  frontend:
    image: myapp-frontend:latest
    build: ./frontend
    ports:
      - "3000:80"
    depends_on:
      - backend
```
--- END EXAMPLE ---

Provide COMPLETE file contents. No placeholders, no "...", no template variables like ${X}.
"""


def _service_dependency_keys(service: Dict) -> set[str]:
    deps = service.get("dependencies")
    if isinstance(deps, dict):
        raw = deps.keys()
    elif isinstance(deps, (list, tuple, set)):
        raw = deps
    else:
        return set()
    return {str(d).lower().strip() for d in raw if str(d).strip()}


def _frontend_mode_from_service(
    service: Dict,
    build_output: str,
    start_command: str,
) -> str:
    explicit_mode = str(service.get("frontend_mode", "")).strip().lower()
    if explicit_mode in {"ssr", "dev_server", "static_nginx"}:
        return explicit_mode

    if _is_next_frontend_service(service, build_output):
        return "ssr"

    container_src = str(service.get("container_port_source", "")).lower()
    framework = str(service.get("framework", "")).lower()
    dep_keys = _service_dependency_keys(service)

    if container_src in {"next_default", "ssr_default"}:
        return "ssr"
    if build_output in SSR_FRONTEND_BUILD_OUTPUTS:
        return "ssr"
    if dep_keys & SSR_FRONTEND_DEP_HINTS:
        return "ssr"
    if any(token in framework for token in SSR_FRONTEND_FRAMEWORK_HINTS):
        return "ssr"
    if any(token in start_command for token in SSR_FRONTEND_START_TOKENS):
        return "ssr"

    if container_src == "dev_server":
        return "dev_server"
    if not build_output and any(token in start_command for token in DEV_SERVER_START_TOKENS):
        return "dev_server"

    return "static_nginx"


def _is_next_frontend_service(service: Dict, build_output: str) -> bool:
    framework = str(service.get("framework", "")).lower()
    if "next" in framework:
        return True
    if build_output == ".next":
        return True
    dep_keys = _service_dependency_keys(service)
    return "next" in dep_keys


def _is_ssr_frontend_service(service: Dict, build_output: str, start_command: str) -> bool:
    return _frontend_mode_from_service(service, build_output, start_command) == "ssr"


def _is_dev_server_frontend_service(service: Dict, build_output: str, start_command: str) -> bool:
    return _frontend_mode_from_service(service, build_output, start_command) == "dev_server"


def _frontend_default_container_source(service: Dict, build_output: str, start_command: str) -> str:
    frontend_mode = _frontend_mode_from_service(service, build_output, start_command)
    if frontend_mode == "ssr" and _is_next_frontend_service(service, build_output):
        return "next_default"
    if frontend_mode == "ssr":
        return "ssr_default"
    if frontend_mode == "dev_server":
        return "dev_server"
    return "nginx_default"


def _service_path_depth(path_value: object) -> int:
    path = str(path_value or ".").replace("\\", "/").strip("/")
    if not path or path == ".":
        return 0
    return len([p for p in path.split("/") if p])


def _backend_service_sort_key(service: Dict) -> tuple[int, int, str, str]:
    svc_type = str(service.get("type", "")).lower()
    type_rank = 0 if svc_type in ("backend", "monolith") else 1
    path = str(service.get("path", ".")).replace("\\", "/")
    return (
        type_rank,
        -_service_path_depth(path),
        path.lower(),
        str(service.get("name", "")).lower(),
    )


def _frontend_service_sort_key(service: Dict) -> tuple[int, str, str]:
    path = str(service.get("path", ".")).replace("\\", "/")
    return (
        -_service_path_depth(path),
        path.lower(),
        str(service.get("name", "")).lower(),
    )


def _normalize_service_ports_v2(service: Dict) -> Dict:
    """Normalize one service payload to runtime/container ports_v2 fields."""
    svc = dict(service or {})
    svc_type = str(svc.get("type", "")).lower()
    raw_build_output = svc.get("build_output")
    build_output = str(raw_build_output).strip() if raw_build_output is not None else ""
    start_command = str(svc.get("start_command", "")).lower()
    frontend_mode = _frontend_mode_from_service(svc, build_output.lower(), start_command)
    is_ssr_frontend = frontend_mode == "ssr"
    is_dev_server_frontend = frontend_mode == "dev_server"
    if svc_type == "frontend":
        svc["frontend_mode"] = frontend_mode

    runtime_port = (
        svc.get("runtime_port")
        if svc.get("runtime_port") is not None
        else (svc.get("dev_port") if svc.get("dev_port") is not None else svc.get("port"))
    )
    container_port = svc.get("container_port")

    if container_port is None and runtime_port is not None:
        if svc_type == "frontend":
            container_port = runtime_port if (is_ssr_frontend or is_dev_server_frontend) else 80
        else:
            container_port = runtime_port

    if runtime_port is None:
        runtime_port = container_port

    if runtime_port is not None:
        svc["runtime_port"] = runtime_port
        svc["port"] = runtime_port  # keep legacy alias coherent
        if svc_type == "frontend":
            svc["dev_port"] = runtime_port
            svc["frontend_mode"] = frontend_mode

    if container_port is not None:
        svc["container_port"] = container_port
        if not svc.get("container_port_source"):
            if svc_type == "frontend":
                svc["container_port_source"] = _frontend_default_container_source(
                    svc,
                    build_output.lower(),
                    start_command,
                )
            else:
                svc["container_port_source"] = "service"

    return svc


def _normalize_ports_v2_contract(
    metadata: Dict,
    services: Optional[List[Dict]],
) -> tuple[Dict, List[Dict]]:
    """
    Canonicalize metadata + services into ports_v2 fields while preserving aliases.
    """
    normalized_metadata = dict(metadata or {})
    normalized_metadata["schema_version"] = PROMPT_SCHEMA_VERSION

    service_candidates = services if services is not None else (normalized_metadata.get("services") or [])
    normalized_services: List[Dict] = [
        _normalize_service_ports_v2(svc)
        for svc in service_candidates
        if isinstance(svc, dict)
    ]
    backend_candidates = sorted(
        [
            svc for svc in normalized_services
            if svc.get("type") in ("backend", "monolith", "worker")
        ],
        key=_backend_service_sort_key,
    )
    frontend_candidates = sorted(
        [
            svc for svc in normalized_services
            if svc.get("type") == "frontend"
        ],
        key=_frontend_service_sort_key,
    )

    backend_runtime_from_services = next(
        (
            svc.get("runtime_port")
            for svc in backend_candidates
            if svc.get("runtime_port") is not None
        ),
        None,
    )
    frontend_runtime_from_services = next(
        (
            svc.get("runtime_port")
            for svc in frontend_candidates
            if svc.get("runtime_port") is not None
        ),
        None,
    )
    backend_container_from_services = next(
        (
            svc.get("container_port")
            for svc in backend_candidates
            if svc.get("container_port") is not None
        ),
        None,
    )
    frontend_container_from_services = next(
        (
            svc.get("container_port")
            for svc in frontend_candidates
            if svc.get("container_port") is not None
        ),
        None,
    )
    frontend_mode_from_services = next(
        (
            str(svc.get("frontend_mode")).strip().lower()
            for svc in frontend_candidates
            if str(svc.get("frontend_mode", "")).strip().lower() in {"ssr", "dev_server", "static_nginx"}
        ),
        None,
    )

    backend_runtime_port = (
        backend_runtime_from_services
        if backend_runtime_from_services is not None
        else (
            normalized_metadata.get("backend_runtime_port")
            if normalized_metadata.get("backend_runtime_port") is not None
            else (
                normalized_metadata.get("backend_port")
                if normalized_metadata.get("backend_port") is not None
                else normalized_metadata.get("port")
            )
        )
    )

    frontend_runtime_port = (
        frontend_runtime_from_services
        if frontend_runtime_from_services is not None
        else (
            normalized_metadata.get("frontend_runtime_port")
            if normalized_metadata.get("frontend_runtime_port") is not None
            else normalized_metadata.get("frontend_port")
        )
    )

    backend_container_port = (
        backend_container_from_services
        if backend_container_from_services is not None
        else normalized_metadata.get("backend_container_port")
    )
    if backend_container_port is None:
        backend_container_port = backend_runtime_port

    frontend_container_port = (
        frontend_container_from_services
        if frontend_container_from_services is not None
        else normalized_metadata.get("frontend_container_port")
    )
    if frontend_container_port is None and frontend_runtime_port is not None:
        framework_hint = str(normalized_metadata.get("framework", "")).lower()
        build_output_hint = str(normalized_metadata.get("build_output", "")).strip().lower()
        container_src_hint = str(normalized_metadata.get("frontend_container_port_source", "")).lower()
        frontend_mode_hint = str(normalized_metadata.get("frontend_mode", "")).strip().lower()
        is_ssr_or_runtime_frontend = (
            frontend_mode_hint in {"ssr", "dev_server"}
            or
            container_src_hint in {"next_default", "ssr_default", "dev_server"}
            or build_output_hint in SSR_FRONTEND_BUILD_OUTPUTS
            or any(token in framework_hint for token in SSR_FRONTEND_FRAMEWORK_HINTS)
        )
        frontend_container_port = (
            frontend_runtime_port if is_ssr_or_runtime_frontend else 80
        )

    if backend_runtime_port is not None:
        normalized_metadata["backend_runtime_port"] = backend_runtime_port
        normalized_metadata["backend_port"] = backend_runtime_port
        normalized_metadata["port"] = backend_runtime_port
    if frontend_runtime_port is not None:
        normalized_metadata["frontend_runtime_port"] = frontend_runtime_port
        normalized_metadata["frontend_port"] = frontend_runtime_port
    if backend_container_port is not None:
        normalized_metadata["backend_container_port"] = backend_container_port
    if frontend_container_port is not None:
        normalized_metadata["frontend_container_port"] = frontend_container_port
    if frontend_mode_from_services in {"ssr", "dev_server", "static_nginx"}:
        normalized_metadata["frontend_mode"] = frontend_mode_from_services

    if normalized_services:
        normalized_metadata["services"] = normalized_services

    return normalized_metadata, normalized_services


def _format_metadata(metadata: Dict) -> str:
    """Format metadata in a structured way that's easy for LLM to parse."""
    if not metadata:
        return "Metadata: unavailable"

    # Use DIRECT VALUE format - avoid 'metadata.X' pattern that LLM treats as template variable
    # Format: KEY: VALUE ← USE THIS EXACT VALUE
    schema_version = str(metadata.get("schema_version") or PROMPT_SCHEMA_VERSION)
    lines = [
        "=== CONFIGURATION VALUES (USE THESE EXACT VALUES) ===",
        f"SCHEMA_VERSION: {schema_version} ← REQUIRED CONTRACT FOR PORT FIELDS",
    ]
    
    # Add STATIC_ONLY flag FIRST if true (CRITICAL for correct Dockerfile type)
    static_only = metadata.get('static_only', False)
    if static_only:
        lines.append("⚠️ STATIC_ONLY: True ← USE nginx:alpine, NO npm, NO node, NO build step!")
    
    backend_runtime_port = (
        metadata.get("backend_runtime_port")
        if metadata.get("backend_runtime_port") is not None
        else (metadata.get("backend_port") if metadata.get("backend_port") is not None else metadata.get("port"))
    )
    if backend_runtime_port is None:
        backend_runtime_port = 8000

    frontend_runtime_port = (
        metadata.get("frontend_runtime_port")
        if metadata.get("frontend_runtime_port") is not None
        else metadata.get("frontend_port")
    )
    if frontend_runtime_port is None:
        frontend_runtime_port = 3000

    backend_container_port = (
        metadata.get("backend_container_port")
        if metadata.get("backend_container_port") is not None
        else backend_runtime_port
    )
    frontend_container_port = (
        metadata.get("frontend_container_port")
        if metadata.get("frontend_container_port") is not None
        else 80
    )

    lines.extend([
        f"RUNTIME: {metadata.get('runtime', 'node:20-alpine')} ← USE IN: FROM {metadata.get('runtime', 'node:20-alpine')}",
        f"BACKEND_RUNTIME_PORT: {backend_runtime_port} ← USE IN: backend host/runtime port",
        f"BACKEND_CONTAINER_PORT: {backend_container_port} ← USE IN: backend EXPOSE/container side",
        f"FRONTEND_RUNTIME_PORT: {frontend_runtime_port} ← USE IN: frontend host/runtime port",
        f"FRONTEND_CONTAINER_PORT: {frontend_container_port} ← USE IN: frontend container side (nginx usually 80)",
        f"DATABASE: {metadata.get('database', 'Unknown')}",
        f"DATABASE_PORT: {metadata.get('database_port', 27017)}",
        f"FRAMEWORK: {metadata.get('framework', 'Unknown')}",
        f"LANGUAGE: {metadata.get('language', 'Unknown')}",
    ])
    
    # Add database cloud/local info (CRITICAL for compose generation)
    if metadata.get("database_is_cloud") is not None:
        is_cloud = metadata.get("database_is_cloud")
        env_var = metadata.get("database_env_var", "DB_URL")
        if is_cloud:
            lines.append(f"DATABASE_IS_CLOUD: True ← DO NOT add database container! Just pass {env_var} to backend")
        else:
            lines.append(f"DATABASE_IS_CLOUD: False ← Add database container to compose")
        if env_var:
            lines.append(f"DATABASE_ENV_VAR: {env_var}")

    build_cmd = metadata.get("build_command")
    start_cmd = metadata.get("start_command")
    entry_point = metadata.get("entry_point")
    build_output = metadata.get("build_output")
    
    # Check if this is a multi-service project (services with their own entry_point)
    services = metadata.get("services", [])
    has_service_entry_points = any(svc.get("entry_point") for svc in services if svc.get("type") == "backend")
    
    if build_cmd:
        lines.append(f"BUILD_COMMAND: {build_cmd}")
    
    # Only include start_command and entry_point for single-service projects
    # For multi-service, the service definitions have the correct paths
    if start_cmd and not has_service_entry_points:
        lines.append(f"START_COMMAND: {start_cmd}")
    if entry_point and not has_service_entry_points:
        meta_lang = str(metadata.get("language", "")).lower()
        if meta_lang == "python":
            lines.append(f"ENTRY_POINT: {entry_point} ← USE IN: CMD [\"python\", \"{entry_point}\"]")
        else:
            lines.append(f"ENTRY_POINT: {entry_point} ← USE IN: CMD [\"node\", \"{entry_point}\"]")
    elif has_service_entry_points:
        # Show the service entry_points for clarity
        backend_entries = [f"{svc.get('name')}: {svc.get('entry_point')}" 
                          for svc in services if svc.get("type") == "backend" and svc.get("entry_point")]
        lines.append(f"# For multi-service: Use entry_point from Service Definitions ({', '.join(backend_entries)})")
        
    if build_output:
        lines.append(f"BUILD_OUTPUT: {build_output} ← FRONTEND ONLY! NOT for backend!")

    env_vars = metadata.get("env_variables") or []
    if env_vars:
        lines.append(f"Environment variables: {', '.join(env_vars[:15])}")

    deps = metadata.get("dependencies") or []
    if deps:
        shown = ", ".join(deps[:10])
        if len(deps) > 10:
            shown += " ..."
        lines.append(f"Dependencies: {shown}")
    
    lines.append("=== END CONFIGURATION VALUES ===")

    return "\n".join(lines)


def _format_dockerfiles(dockerfiles: List[Dict[str, str]]) -> str:
    if not dockerfiles:
        return "No Dockerfiles detected."

    sections: List[str] = []
    for df in dockerfiles:
        path = df.get("path", "Dockerfile")
        content = df.get("content", "")
        sections.append(f"[Dockerfile: {path}]\n{content}")
    return "\n\n".join(sections)


def _format_compose_files(compose_files: List[Dict[str, str]]) -> str:
    if not compose_files:
        return "No docker-compose files detected."

    sections: List[str] = []
    for cf in compose_files:
        path = cf.get("path", "docker-compose.yml")
        content = cf.get("content", "")
        sections.append(f"[Compose: {path}]\n{content}")
    return "\n\n".join(sections)


def _format_file_tree(file_tree: Optional[str]) -> str:
    return file_tree or "File tree: not provided"


def _format_logs(logs: Optional[List[str]]) -> str:
    if not logs:
        return "Build/Run logs: none yet."
    joined = "\n".join(logs[-20:])
    return f"Build/Run logs (latest tail):\n{joined}"


def build_deploy_message(
    project_name: str,
    metadata: Dict,
    dockerfiles: List[Dict[str, str]],
    compose_files: List[Dict[str, str]],
    file_tree: Optional[str],
    user_message: str,
    logs: Optional[List[str]] = None,
    extra_instructions: Optional[str] = None,
    services: Optional[List[Dict[str, str]]] = None,
    mode: str = "VALIDATE_EXISTING",
    source_files: Optional[List[Dict[str, str]]] = None,
) -> str:
    metadata, normalized_services = _normalize_ports_v2_contract(metadata, services)
    services = normalized_services

    if dockerfiles:
        dockerfile_summary = f"Dockerfiles detected: {len(dockerfiles)} ({', '.join(df.get('path', '') for df in dockerfiles[:5])})"
    else:
        dockerfile_summary = "Dockerfiles detected: 0"

    if compose_files:
        compose_summary = f"Compose files detected: {len(compose_files)} ({', '.join(cf.get('path', '') for cf in compose_files[:5])})"
    else:
        compose_summary = "Compose files detected: 0"

    sections = [
        f"SCHEMA_VERSION: {metadata.get('schema_version', PROMPT_SCHEMA_VERSION)}",
        f"MODE: {mode}",
        f"PROJECT_NAME: {project_name} ← USE IN: image: {project_name}-backend:latest, image: {project_name}-frontend:latest",
        f"Project: {project_name}",
        dockerfile_summary,
        compose_summary,
        _format_metadata(metadata),
        _format_dockerfiles(dockerfiles),
        _format_compose_files(compose_files),
        _format_file_tree(file_tree),
        _format_logs(logs),
    ]

    if extra_instructions:
        sections.append(f"User deployment instructions: {extra_instructions}")

    # Add service definitions if present
    if services:
        lines = ["⚠️⚠️⚠️ SERVICE DEFINITIONS (OVERRIDES metadata for multi-service!) ⚠️⚠️⚠️", 
                 "For each service, use ITS entry_point (relative to service dir), NOT metadata.entry_point!"]
        for svc in services:
            svc_line = f"- name: {svc.get('name', 'unknown')}, path: {svc.get('path', '.')}, type: {svc.get('type', 'unknown')}"
            svc_runtime_image = svc.get("runtime")
            if svc_runtime_image:
                svc_line += f", runtime: {svc_runtime_image} (USE IN Dockerfile FROM: {svc_runtime_image})"
            
            # Include port for all services (CRITICAL for correct EXPOSE and ports)
            if svc.get('type') == 'frontend':
                host_port = (
                    svc.get('runtime_port')
                    or svc.get('dev_port')
                    or svc.get('port')
                    or 3000
                )
                container_port = svc.get('container_port') or 80
                container_src = svc.get('container_port_source', 'unknown')
                frontend_mode = str(svc.get("frontend_mode", "")).strip().lower() or "unknown"
                svc_line += (
                    f", runtime_port: {host_port}, "
                    f"container_port: {container_port} "
                    f"(container_source: {container_src} - USE: \"{host_port}:{container_port}\")"
                )
                svc_line += f", frontend_mode: {frontend_mode}"
            else:
                svc_runtime_port = (
                    svc.get('runtime_port')
                    if svc.get('runtime_port') is not None
                    else svc.get('port')
                )
                svc_container_port = (
                    svc.get('container_port')
                    if svc.get('container_port') is not None
                    else svc_runtime_port
                )
                if svc_runtime_port is not None or svc_container_port is not None:
                    if svc_runtime_port is None:
                        svc_runtime_port = svc_container_port
                    if svc_container_port is None:
                        svc_container_port = svc_runtime_port
                    runtime_src = svc.get('port_source', 'default')
                    container_src = svc.get('container_port_source', 'service')
                    svc_line += (
                        f", runtime_port: {svc_runtime_port}, "
                        f"container_port: {svc_container_port} "
                        f"(runtime_from {runtime_src}, container_from {container_src} "
                        f"- USE: \"{svc_runtime_port}:{svc_container_port}\")"
                    )
            
            # Include entry_point for backend services (CRITICAL for correct CMD path)
            if svc.get('entry_point'):
                svc_lang = str(svc.get("language", "")).lower()
                if svc_lang == "python":
                    cmd_hint = f"python {svc.get('entry_point')}"
                else:
                    cmd_hint = f"node {svc.get('entry_point')}"
                svc_line += f", entry_point: {svc.get('entry_point')} (USE THIS IN CMD: {cmd_hint})"
            
            # Include build_output for frontend services (CRITICAL for correct COPY path)
            if svc.get('build_output'):
                svc_line += f", build_output: {svc.get('build_output')} (USE /app/{svc.get('build_output')})"
            
            # Include env_file for services with .env (CRITICAL for docker-compose env injection)
            if svc.get('env_file'):
                svc_line += f", env_file: {svc.get('env_file')} (ADD TO COMPOSE: env_file: ['{svc.get('env_file')}'])"
            
            # Include package_manager for correct install/build commands
            if svc.get('package_manager'):
                pm_info = svc.get('package_manager')
                # Handle both old string format and new dict format
                if isinstance(pm_info, dict):
                    pm = pm_info.get('manager', 'npm')
                    has_lock = pm_info.get('has_lockfile', True)
                else:
                    pm = pm_info
                    has_lock = True
                
                # Backend: install only, Frontend: install + build
                is_backend = svc.get('type') == 'backend'
                
                if pm == 'yarn':
                    if is_backend:
                        svc_line += f", package_manager: yarn (USE: yarn install --frozen-lockfile)"
                    else:
                        svc_line += f", package_manager: yarn (USE: yarn install --frozen-lockfile, yarn build)"
                elif pm == 'pnpm':
                    if is_backend:
                        svc_line += f", package_manager: pnpm (USE: pnpm install --frozen-lockfile)"
                    else:
                        svc_line += f", package_manager: pnpm (USE: pnpm install --frozen-lockfile, pnpm build)"
                elif has_lock:
                    if is_backend:
                        svc_line += f", package_manager: npm (USE: npm ci)"
                    else:
                        svc_line += f", package_manager: npm (USE: npm ci, npm run build)"
                else:
                    if is_backend:
                        svc_line += f", package_manager: npm, NO LOCKFILE (USE: npm install)"
                    else:
                        svc_line += f", package_manager: npm, NO LOCKFILE (USE: npm install, npm run build)"
            
            # Include database cloud/local info (CRITICAL for compose generation)
            if svc.get('type') == 'database':
                is_cloud = svc.get('is_cloud', False)
                if is_cloud:
                    svc_line += f", is_cloud: True (DO NOT ADD THIS TO COMPOSE - use backend env var instead!)"
                else:
                    docker_image = svc.get('docker_image', 'mongo:latest')
                    svc_line += f", is_cloud: False, docker_image: {docker_image} (ADD TO COMPOSE!)"
            
            lines.append(svc_line)
        sections.append("\n".join(lines))

        # ── Fix 2: Monolith architecture override ─────────────────────
        if metadata.get("architecture") == "monolith":
            monolith_svc = next(
                (s for s in services if s.get("type") == "monolith"), None
            )
            if monolith_svc:
                runtime = monolith_svc.get("runtime") or metadata.get("runtime", "node:20-alpine")
                runtime_port = (
                    monolith_svc.get("runtime_port")
                    if monolith_svc.get("runtime_port") is not None
                    else monolith_svc.get("port", 3000)
                )
                container_port = (
                    monolith_svc.get("container_port")
                    if monolith_svc.get("container_port") is not None
                    else runtime_port
                )
                entry = monolith_svc.get("entry_point", "server.js")
                pm_info = monolith_svc.get("package_manager", {})
                if isinstance(pm_info, dict):
                    has_lock = pm_info.get("has_lockfile", True)
                    pm = pm_info.get("manager", "npm")
                else:
                    has_lock = True
                    pm = pm_info or "npm"
                if pm == "yarn":
                    install_cmd = "yarn install --frozen-lockfile"
                elif pm == "pnpm":
                    install_cmd = "pnpm install --frozen-lockfile"
                elif has_lock:
                    install_cmd = "npm ci"
                else:
                    install_cmd = "npm install"

                sections.append(
                    "⚠️ MONOLITH ARCHITECTURE DETECTED\n"
                    "This project has Express AND React in the same package.json.\n"
                    "Generate ONE Dockerfile only (no multi-stage nginx split):\n\n"
                    f"FROM {runtime}\n"
                    "WORKDIR /app\n"
                    "COPY package*.json ./\n"
                    f"RUN {install_cmd}\n"
                    "COPY . .\n"
                    "RUN npm run build          ← builds React into /build or /dist\n"
                    f"ENV PORT={container_port}\n"
                    f"EXPOSE {container_port}\n"
                    f'CMD ["node", "{entry}"]   ← Express serves static files\n\n'
                    "docker-compose.yml should have ONE app service + database (if needed).\n"
                    "Do NOT generate a separate frontend Dockerfile or nginx service."
                )

        # ── Fix 7: Python backend Dockerfile override ─────────────────
        python_svcs = [s for s in services if s.get("dockerfile_strategy") == "python_backend"]
        for py_svc in python_svcs:
            fw = py_svc.get("framework", "Unknown")
            runtime_port = (
                py_svc.get("runtime_port")
                if py_svc.get("runtime_port") is not None
                else py_svc.get("port", 8000)
            )
            runtime_image = py_svc.get("runtime") or "python:3.11-slim"
            container_port = (
                py_svc.get("container_port")
                if py_svc.get("container_port") is not None
                else runtime_port
            )
            entry = py_svc.get("entry_point", "app.py")
            pm = py_svc.get("package_manager", "pip")
            svc_name = py_svc.get("name", "app")

            if pm == "poetry":
                install_block = (
                    "COPY pyproject.toml poetry.lock* ./\n"
                    "RUN pip install poetry && poetry install --no-root --no-dev"
                )
            elif pm == "pipenv":
                install_block = (
                    "COPY Pipfile Pipfile.lock* ./\n"
                    "RUN pip install pipenv && pipenv install --system --deploy"
                )
            else:
                install_block = (
                    "COPY requirements.txt ./\n"
                    "RUN pip install --no-cache-dir -r requirements.txt"
                )

            if fw == "Django":
                cmd_line = f'CMD ["python", "manage.py", "runserver", "0.0.0.0:{container_port}"]'
            elif fw == "FastAPI":
                cmd_line = f'CMD ["uvicorn", "{entry.replace(".py", "")}:app", "--host", "0.0.0.0", "--port", "{container_port}"]'
            elif fw == "Flask":
                cmd_line = f'CMD ["python", "{entry}"]'
            else:
                cmd_line = f'CMD ["python", "{entry}"]'

            sections.append(
                f"🐍 PYTHON BACKEND DETECTED: {svc_name} ({fw})\n"
                f"Generate a Python Dockerfile for service '{svc_name}':\n\n"
                f"FROM {runtime_image}\n"
                "WORKDIR /app\n"
                f"{install_block}\n"
                "COPY . .\n"
                f"ENV PORT={container_port}\n"
                f"EXPOSE {container_port}\n"
                f"{cmd_line}\n\n"
                f"Use this Dockerfile for the '{svc_name}' service in docker-compose.yml."
            )

    # Enhance user_message for GENERATE_MISSING mode if it's generic
    if mode == "GENERATE_MISSING" and user_message.strip().lower() in ["generate", "create", ""]:
        user_message = (
            "Generate ALL required Docker files:\n"
            "1. Create a Dockerfile for EACH service directory (use service runtime when provided, otherwise RUNTIME, plus runtime_port/container_port values)\n"
            "2. Create docker-compose.yml at project root (with image: and build: fields for ALL services)\n"
            "Use EXACT values from the input. Provide complete file contents."
        )

    sections.append(f"User message: {user_message}")
    sections.append("Respond with STATUS/REASON/FIXES or GENERATED DOCKERFILES/LOG ANALYSIS.")

    return "\n\n".join(sections)


GEMINI_DOCKER_SYSTEM_PROMPT = """Generate minimal working Docker files from the JSON input only.
Return only this structure:
STATUS: Generated
GENERATED FILES:
**path/to/file**
```dockerfile
...
```
**path/to/file**
```yaml
...
```
Rules:
- Write one Dockerfile at each app service dockerfile_path.
- Write docker-compose.yml at the project root.
- Compose must have services only, no top-level version.
- Every app service must have image, build, and ports using runtime_port:container_port.
- Use env_file only when the service has env_file.
- Add a database container only when database_is_cloud is false and database is MongoDB, PostgreSQL, MySQL, or Redis.
- Backend/monolith/worker/other Dockerfiles are single-stage and use ENV PORT plus EXPOSE container_port.
- static_nginx frontend Dockerfiles use builder_runtime as builder, nginx:alpine final stage, and EXPOSE 80.
- ssr/dev_server frontend Dockerfiles do not use nginx and EXPOSE container_port.
- COPY paths are relative to each service build context; never prefix COPY with the service path.
- Auto-Healing: If you see existing_dockerfiles or existing_compose_files, analyze them for errors and provide corrected versions.
- No placeholders, no comments, no extra prose.
"""


GEMINI_DOCKER_VALIDATION_PROMPT = """Validate existing Docker files from the JSON input and logs.
Do a comprehensive validation of the project configuration, source code, dependencies, and Docker files.
If you find ANY errors or if the user asked you to rewrite it, you MUST actively HEAL the project.
Return only this exact string structure:
STATUS: Invalid
REASON: one concise root cause
FIXES:
- minimal actionable fix summary
GENERATED FILES:
**path/to/script.js**
```javascript
...
```
**path/Dockerfile**
```dockerfile
...
```

Rules:
- Always output the completely rewritten file blocks to heal the application.
- If the project has NO issues at all, output 'STATUS: Valid' and omit GENERATED FILES.
- For build/run/push errors, rewrite the exact failing service/file to fix it.
- No placeholders, no comments, no extra prose. Include ALL code in the rewritten files.
"""


def _clean_path(path_value: object) -> str:
    path = str(path_value or ".").replace("\\", "/").strip()
    path = re.sub(r"^\./+", "", path)
    while "//" in path:
        path = path.replace("//", "/")
    return path.strip("/") or "."


def _expected_dockerfile_path(service: Dict) -> str:
    svc_path = _clean_path(service.get("path", "."))
    return "Dockerfile" if svc_path == "." else f"{svc_path}/Dockerfile"


def _is_app_service(service: Dict) -> bool:
    return str(service.get("type", "")).lower() != "database"


def _frontend_builder_runtime(metadata: Dict, service: Dict) -> str:
    candidates = [
        metadata.get("runtime"),
        service.get("builder_runtime"),
        service.get("runtime"),
    ]
    for candidate in candidates:
        runtime = str(candidate or "").strip()
        if runtime and "nginx" not in runtime.lower() and runtime != "alpine:latest":
            return runtime
    return "node:20-alpine"


def _minimal_service_for_prompt(metadata: Dict, service: Dict) -> Dict:
    svc = {
        "name": service.get("name") or os.path.basename(_clean_path(service.get("path", "."))) or "app",
        "path": _clean_path(service.get("path", ".")),
        "type": service.get("type", "other"),
        "runtime": service.get("runtime"),
        "runtime_port": service.get("runtime_port"),
        "container_port": service.get("container_port"),
        "dockerfile_path": _expected_dockerfile_path(service),
        "entry_point": service.get("entry_point"),
        "start_command": service.get("start_command"),
        "build_output": service.get("build_output"),
        "frontend_mode": service.get("frontend_mode"),
        "env_file": service.get("env_file"),
        "package_manager": service.get("package_manager"),
        "language": service.get("language"),
        "framework": service.get("framework"),
    }
    if str(service.get("type", "")).lower() == "frontend" and service.get("frontend_mode") == "static_nginx":
        svc["builder_runtime"] = _frontend_builder_runtime(metadata, service)
        svc["final_runtime"] = "nginx:alpine"
        svc.pop("runtime", None)
    return {key: value for key, value in svc.items() if value not in (None, "", [])}


def build_gemini_deploy_message(
    project_name: str,
    metadata: Dict,
    dockerfiles: List[Dict[str, str]],
    compose_files: List[Dict[str, str]],
    file_tree: Optional[str],
    user_message: str,
    logs: Optional[List[str]] = None,
    extra_instructions: Optional[str] = None,
    services: Optional[List[Dict[str, str]]] = None,
    mode: str = "VALIDATE_EXISTING",
    source_files: Optional[List[Dict[str, str]]] = None,
) -> str:
    metadata, normalized_services = _normalize_ports_v2_contract(metadata, services)
    app_services = [svc for svc in normalized_services if _is_app_service(svc)]
    source_files = source_files or []

    payload = {
        "schema_version": metadata.get("schema_version", PROMPT_SCHEMA_VERSION),
        "mode": mode,
        "project_name": project_name,
        "database": metadata.get("database"),
        "database_port": metadata.get("database_port"),
        "database_is_cloud": metadata.get("database_is_cloud"),
        "database_env_var": metadata.get("database_env_var"),
        "services": [_minimal_service_for_prompt(metadata, svc) for svc in app_services],
        "user_message": user_message,
        "source_files": source_files,
    }
    if file_tree:
        payload["file_tree"] = file_tree
    if dockerfiles:
        payload["existing_dockerfiles"] = dockerfiles
    if compose_files:
        payload["existing_compose_files"] = compose_files
    if logs:
        payload["logs"] = logs[-10:]
    if extra_instructions:
        payload["extra_instructions"] = extra_instructions
    return json.dumps(payload, indent=2, ensure_ascii=True)


def _response_message(
    project_name: str,
    metadata: Dict,
    dockerfiles: List[Dict[str, str]],
    compose_files: List[Dict[str, str]],
    file_tree: Optional[str],
    user_message: str,
    logs: Optional[List[str]],
    extra_instructions: Optional[str],
    services: Optional[List[Dict[str, str]]],
    mode: str,
    source_files: Optional[List[Dict[str, str]]] = None,
) -> tuple[str, str]:
    source_files = source_files or []
    # Groq serves capable instruction-tuned chat models over a clean
    # OpenAI-style messages API - much closer in nature to Gemini's setup
    # than to a small local Ollama model needing heavy few-shot guidance -
    # so it shares Gemini's terse JSON-payload prompt path.
    if get_docker_llm_provider() in ("gemini", "groq"):
        system_prompt = (
            GEMINI_DOCKER_SYSTEM_PROMPT
            if mode == "GENERATE_MISSING"
            else GEMINI_DOCKER_VALIDATION_PROMPT
        )
        return (
            system_prompt,
            build_gemini_deploy_message(
                project_name=project_name,
                metadata=metadata,
                dockerfiles=dockerfiles,
                compose_files=compose_files,
                source_files=source_files,
                file_tree=file_tree,
                user_message=user_message,
                logs=logs,
                extra_instructions=extra_instructions,
                services=services,
                mode=mode,
            ),
        )
    return (
        DOCKER_DEPLOY_SYSTEM_PROMPT,
        build_deploy_message(
            project_name=project_name,
            metadata=metadata,
            dockerfiles=dockerfiles,
            compose_files=compose_files,
            source_files=source_files,
            file_tree=file_tree,
            user_message=user_message,
            logs=logs,
            extra_instructions=extra_instructions,
            services=services,
            mode=mode,
        ),
    )


def _normalize_generated_path(path: str) -> str:
    path = str(path or "").strip().strip("`#* ").replace("\\", "/")
    path = re.sub(r"^\./+", "", path)
    path = path.strip("/ ")
    if not path:
        return ""
    normalized = os.path.normpath(path).replace("\\", "/")
    return "" if normalized.startswith("../") or normalized == ".." or os.path.isabs(normalized) else normalized


def parse_generated_docker_files(response_text: str) -> Dict[str, str]:
    files: Dict[str, str] = {}
    patterns = [
        r"\*\*(?P<path>[^*\n]+)\*\*\s*```(?P<lang>[a-zA-Z0-9_-]*)\s*(?P<content>[\s\S]*?)```",
        r"(?:^|\n)#+\s*(?P<path>[^\n]+)\s*```(?P<lang>[a-zA-Z0-9_-]*)\s*(?P<content>[\s\S]*?)```",
        r"(?:^|\n)(?P<path>[^\n]+)\s*```(?P<lang>[a-zA-Z0-9_-]*)\s*(?P<content>[\s\S]*?)```",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, response_text or "", re.IGNORECASE):
            path = _normalize_generated_path(match.group("path"))
            content = (match.group("content") or "").strip()
            if path and content:
                files[path] = content
    return files


def _compose_path(files: Dict[str, str]) -> Optional[str]:
    for path in files:
        if os.path.basename(path).lower() in {"docker-compose.yml", "docker-compose.yaml"}:
            return path
    return None


def _compose_build_context(value: object) -> str:
    if isinstance(value, str):
        return _clean_path(value)
    if isinstance(value, dict):
        return _clean_path(value.get("context", "."))
    return "."


def remap_generated_docker_paths(
    files: Dict[str, str],
    metadata: Dict,
    services: Optional[List[Dict]],
) -> Dict[str, str]:
    """
    Fix common LLM path errors before validation:
    1. Remap Dockerfiles placed at the wrong path to match service definitions
    2. Fix docker-compose build contexts to match service paths
    """
    if not files:
        return files

    _, normalized_services = _normalize_ports_v2_contract(
        dict(metadata or {}), list(services or [])
    )
    app_services = [svc for svc in normalized_services if _is_app_service(svc)]
    if not app_services:
        return files

    new_files = dict(files)

    # ── 1. Remap misplaced Dockerfiles ─────────────────────────────────
    expected = {_expected_dockerfile_path(svc): svc for svc in app_services}
    gen_dockerfiles = [
        p for p in new_files if os.path.basename(p).lower() == "dockerfile"
    ]
    misplaced = [p for p in gen_dockerfiles if p not in expected]
    missing = [p for p in expected if p not in gen_dockerfiles]

    if misplaced and missing and len(misplaced) == len(missing):
        for old, new in zip(misplaced, missing):
            new_files[new] = new_files.pop(old)
            print(f"🔧 Path remap: {old} → {new}")

    # ── 2. Fix compose build contexts ──────────────────────────────────
    compose_key = _compose_path(new_files)
    if compose_key:
        try:
            import yaml
            data = yaml.safe_load(new_files[compose_key])
            if isinstance(data, dict) and isinstance(data.get("services"), dict):
                svc_map = {
                    str(s.get("name", "")).strip(): _clean_path(s.get("path", "."))
                    for s in app_services
                }
                changed = False
                for name, expected_ctx in svc_map.items():
                    cs = data["services"].get(name)
                    if not isinstance(cs, dict):
                        continue
                    want = f"./{expected_ctx}" if expected_ctx != "." else "."
                    bv = cs.get("build")
                    if isinstance(bv, str):
                        if _clean_path(bv) != expected_ctx:
                            cs["build"] = want
                            changed = True
                    elif isinstance(bv, dict):
                        if _clean_path(bv.get("context", ".")) != expected_ctx:
                            bv["context"] = want
                            changed = True
                if changed:
                    new_files[compose_key] = yaml.dump(
                        data, default_flow_style=False, sort_keys=False
                    )
                    print("🔧 Fixed compose build contexts")
        except Exception:
            pass

    return new_files


def _compose_port_matches(value: object, runtime_port: object, container_port: object) -> bool:
    expected = f"{runtime_port}:{container_port}"
    if isinstance(value, str):
        return value.split("/", 1)[0].strip('"').strip("'") == expected
    if isinstance(value, dict):
        published = value.get("published") or value.get("host_port")
        target = value.get("target") or value.get("container_port")
        return str(published) == str(runtime_port) and str(target) == str(container_port)
    return False


def _env_file_values(value: object) -> List[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [_clean_path(value)]
    if isinstance(value, list):
        return [_clean_path(item) for item in value]
    return []


def _is_database_compose_service(name: str, service: Dict) -> bool:
    joined = f"{name} {service.get('image', '')}".lower()
    return any(token in joined for token in ("mongo", "postgres", "mysql", "redis", "database", "db"))


def _requires_database_container(metadata: Dict) -> bool:
    database = str(metadata.get("database") or "").strip().lower()
    if database in {"", "unknown", "sqlite"}:
        return False
    supported_local = {"mongodb", "postgresql", "postgres", "mysql", "redis"}
    return database in supported_local and not bool(metadata.get("database_is_cloud"))


def _validate_compose(
    compose_content: str,
    metadata: Dict,
    app_services: List[Dict],
) -> List[str]:
    try:
        import yaml
    except Exception as exc:
        return [f"PyYAML is unavailable for compose validation: {exc}"]

    errors: List[str] = []
    try:
        data = yaml.safe_load(compose_content) or {}
    except Exception as exc:
        return [f"docker-compose.yml is not valid YAML: {exc}"]

    if not isinstance(data, dict):
        return ["docker-compose.yml must be a YAML object."]
    if "version" in data:
        errors.append("docker-compose.yml must not contain a top-level version field.")

    compose_services = data.get("services")
    if not isinstance(compose_services, dict) or not compose_services:
        return errors + ["docker-compose.yml must contain a services mapping."]

    expected_names = {str(svc.get("name") or "").strip(): svc for svc in app_services}
    expected_names = {name: svc for name, svc in expected_names.items() if name}

    for name, svc in expected_names.items():
        compose_svc = compose_services.get(name)
        if not isinstance(compose_svc, dict):
            errors.append(f"docker-compose.yml is missing service '{name}'.")
            continue

        if "image" not in compose_svc:
            errors.append(f"compose service '{name}' is missing image.")
        if "build" not in compose_svc:
            errors.append(f"compose service '{name}' is missing build.")

        expected_context = _clean_path(svc.get("path", "."))
        actual_context = _compose_build_context(compose_svc.get("build"))
        if actual_context != expected_context:
            errors.append(
                f"compose service '{name}' build context is '{actual_context}', expected '{expected_context}'."
            )

        runtime_port = svc.get("runtime_port")
        container_port = svc.get("container_port")
        ports = compose_svc.get("ports") or []
        if runtime_port is not None and container_port is not None:
            if not any(_compose_port_matches(port, runtime_port, container_port) for port in ports):
                errors.append(
                    f"compose service '{name}' must map port {runtime_port}:{container_port}."
                )

        expected_env_file = svc.get("env_file")
        if expected_env_file:
            env_files = _env_file_values(compose_svc.get("env_file"))
            if _clean_path(expected_env_file) not in env_files:
                errors.append(f"compose service '{name}' must include env_file {expected_env_file}.")

    database = str(metadata.get("database") or "").strip().lower()
    if database and database != "unknown":
        db_services = [
            name for name, svc in compose_services.items()
            if isinstance(svc, dict) and name not in expected_names and _is_database_compose_service(name, svc)
        ]
        if bool(metadata.get("database_is_cloud")) and db_services:
            errors.append("docker-compose.yml must not add a database container when database_is_cloud is true.")
        if _requires_database_container(metadata) and not db_services:
            errors.append("docker-compose.yml must add a database container when database_is_cloud is false.")

    return errors


def _validate_dockerfile(path: str, content: str, service: Dict) -> List[str]:
    errors: List[str] = []
    lower = content.lower()
    from_lines = re.findall(r"^\s*from\s+", content, flags=re.IGNORECASE | re.MULTILINE)
    svc_type = str(service.get("type", "")).lower()
    svc_path = _clean_path(service.get("path", "."))
    container_port = service.get("container_port")

    if not from_lines:
        errors.append(f"{path} is missing FROM.")
    if "..." in content or "${" in content:
        errors.append(f"{path} contains placeholders.")
    if svc_path != "." and re.search(rf"^\s*COPY\s+\.?/?{re.escape(svc_path)}/", content, flags=re.IGNORECASE | re.MULTILINE):
        errors.append(f"{path} uses service-path-prefixed COPY even though build context is {svc_path}.")
    if container_port is not None and not re.search(rf"^\s*EXPOSE\s+{re.escape(str(container_port))}\b", content, flags=re.IGNORECASE | re.MULTILINE):
        errors.append(f"{path} must EXPOSE {container_port}.")

    if svc_type == "frontend":
        mode = str(service.get("frontend_mode") or "").lower()
        if mode == "static_nginx":
            if len(from_lines) < 2:
                errors.append(f"{path} static frontend must use a builder stage plus nginx final stage.")
            if "nginx:alpine" not in lower:
                errors.append(f"{path} static frontend must use nginx:alpine final stage.")
            if "copy --from" not in lower:
                errors.append(f"{path} static frontend must copy build output from builder.")
            build_output = service.get("build_output")
            if build_output and f"/app/{build_output}".lower() not in lower:
                errors.append(f"{path} must copy /app/{build_output}.")
        else:
            if "nginx" in lower:
                errors.append(f"{path} {mode or 'runtime'} frontend must not use nginx.")
    else:
        if len(from_lines) > 1:
            errors.append(f"{path} backend-like service must be single-stage.")
        if "nginx" in lower:
            errors.append(f"{path} backend-like service must not use nginx.")
        entry_point = service.get("entry_point")
        if entry_point and str(entry_point) not in content:
            errors.append(f"{path} must reference entry_point {entry_point}.")

    return errors


def validate_generated_docker_files(
    files: Dict[str, str],
    metadata: Dict,
    services: Optional[List[Dict]],
    require_dockerfiles: bool = True,
    require_compose: bool = True,
) -> List[str]:
    metadata, normalized_services = _normalize_ports_v2_contract(metadata, services)
    app_services = [svc for svc in normalized_services if _is_app_service(svc)]
    errors: List[str] = []

    expected_dockerfiles = {_expected_dockerfile_path(svc): svc for svc in app_services}
    generated_dockerfiles = {
        path: content for path, content in files.items()
        if os.path.basename(path).lower() == "dockerfile"
    }

    if require_dockerfiles:
        for path in expected_dockerfiles:
            if path not in generated_dockerfiles:
                errors.append(f"Missing required Dockerfile: {path}.")
        for path in generated_dockerfiles:
            if path not in expected_dockerfiles:
                errors.append(f"Unexpected Dockerfile path: {path}.")

    for path, svc in expected_dockerfiles.items():
        if path in generated_dockerfiles:
            errors.extend(_validate_dockerfile(path, generated_dockerfiles[path], svc))

    compose_file_path = _compose_path(files)
    if require_compose and not compose_file_path:
        errors.append("Missing required docker-compose.yml.")
    if compose_file_path:
        if _clean_path(compose_file_path) != "docker-compose.yml":
            errors.append("docker-compose.yml must be generated at the project root.")
        errors.extend(_validate_compose(files[compose_file_path], metadata, app_services))

    return errors


def parse_and_validate_generated_docker_response(
    response_text: str,
    metadata: Dict,
    services: Optional[List[Dict]],
    require_dockerfiles: bool = True,
    require_compose: bool = True,
) -> tuple[Dict[str, str], List[str]]:
    files = parse_generated_docker_files(response_text)
    files = remap_generated_docker_paths(files, metadata, services)
    errors = validate_generated_docker_files(
        files=files,
        metadata=metadata,
        services=services,
        require_dockerfiles=require_dockerfiles,
        require_compose=require_compose,
    )
    return files, errors


def _call_llm_docker_with_repair(
    messages: List[Dict[str, str]],
    project_name: str,
    metadata: Dict,
    dockerfiles: List[Dict[str, str]],
    compose_files: List[Dict[str, str]],
    file_tree: Optional[str],
    user_message: str,
    logs: Optional[List[str]],
    extra_instructions: Optional[str],
    services: Optional[List[Dict[str, str]]],
    mode: str,
    source_files: Optional[List[Dict[str, str]]] = None,
    call_fn=None,
) -> str:
    """
    Self-repair loop shared by the Gemini and Groq providers: both consume the
    same terse JSON-payload prompt contract (see _response_message), so both
    get the same validate-then-regenerate-on-error behavior. `call_fn` is the
    single-call LLM function to use (call_gemini or call_groq); it defaults to
    call_gemini for backward compatibility with existing callers.
    """
    source_files = source_files or []
    llm_call = call_fn or call_gemini
    response = llm_call(messages, custom_options={"temperature": 0.0})
    if response.startswith("ERROR:") or mode != "GENERATE_MISSING" or not services:
        return response

    _, validation_errors = parse_and_validate_generated_docker_response(
        response,
        metadata,
        services,
        require_dockerfiles=True,
        require_compose=True,
    )
    if not validation_errors:
        return response

    repair_system_prompt, repair_message = _response_message(
        project_name=project_name,
        metadata=metadata,
        dockerfiles=dockerfiles,
        compose_files=compose_files,
        source_files=source_files,
        file_tree=file_tree,
        user_message=(
            "Regenerate complete Docker files. Fix these validation errors exactly:\n"
            + "\n".join(f"- {err}" for err in validation_errors)
        ),
        logs=(logs or []) + validation_errors,
        extra_instructions=f"Previous invalid response:\n{response[:4000]}",
        services=services,
        mode=mode,
    )
    return llm_call(
        [
            {"role": "system", "content": repair_system_prompt},
            {"role": "user", "content": repair_message},
        ],
        custom_options={"temperature": 0.0},
    )


def run_docker_deploy_chat(
    project_name: str,
    metadata: Dict,
    dockerfiles: List[Dict[str, str]],
    compose_files: List[Dict[str, str]],
    file_tree: Optional[str],
    user_message: str,
    logs: Optional[List[str]] = None,
    extra_instructions: Optional[str] = None,
    services: Optional[List[Dict[str, str]]] = None,
    source_files: Optional[List[Dict[str, str]]] = None,
) -> str:
    """
    Invoke LLM to analyze or generate Dockerfiles with the mandated response shape.
    """

    source_files = source_files or []

    # Decide mode based on presence of Dockerfiles / compose
    if dockerfiles or compose_files:
        mode = "VALIDATE_EXISTING"
    else:
        mode = "GENERATE_MISSING"

    system_prompt, message = _response_message(
        project_name=project_name,
        metadata=metadata,
        dockerfiles=dockerfiles,
        compose_files=compose_files,
        source_files=source_files,
        file_tree=file_tree,
        user_message=user_message,
        logs=logs,
        extra_instructions=extra_instructions,
        services=services,
        mode=mode,
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": message},
    ]
    provider = get_docker_llm_provider()
    if provider in ("gemini", "groq"):
        return _call_llm_docker_with_repair(
            messages=messages,
            project_name=project_name,
            metadata=metadata,
            dockerfiles=dockerfiles,
            compose_files=compose_files,
            source_files=source_files,
            file_tree=file_tree,
            user_message=user_message,
            logs=logs,
            extra_instructions=extra_instructions,
            services=services,
            mode=mode,
            call_fn=call_groq if provider == "groq" else call_gemini,
        )
    return call_llama(messages)


def run_docker_deploy_chat_stream(
    project_name: str,
    metadata: Dict,
    dockerfiles: List[Dict[str, str]],
    compose_files: List[Dict[str, str]],
    file_tree: Optional[str],
    user_message: str,
    logs: Optional[List[str]] = None,
    extra_instructions: Optional[str] = None,
    services: Optional[List[Dict[str, str]]] = None,
    source_files: Optional[List[Dict[str, str]]] = None,
):
    """
    Streaming version of run_docker_deploy_chat.
    Yields tokens as they're generated by the LLM.
    """
    source_files = source_files or []

    # Decide mode based on presence of Dockerfiles / compose
    if dockerfiles or compose_files:
        mode = "VALIDATE_EXISTING"
    else:
        mode = "GENERATE_MISSING"

    system_prompt, message = _response_message(
        project_name=project_name,
        metadata=metadata,
        dockerfiles=dockerfiles,
        compose_files=compose_files,
        source_files=source_files,
        file_tree=file_tree,
        user_message=user_message,
        logs=logs,
        extra_instructions=extra_instructions,
        services=services,
        mode=mode,
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": message},
    ]
    provider = get_docker_llm_provider()
    if provider in ("gemini", "groq"):
        response = _call_llm_docker_with_repair(
            messages=messages,
            project_name=project_name,
            metadata=metadata,
            dockerfiles=dockerfiles,
            compose_files=compose_files,
            source_files=source_files,
            file_tree=file_tree,
            user_message=user_message,
            logs=logs,
            extra_instructions=extra_instructions,
            services=services,
            mode=mode,
            call_fn=call_groq if provider == "groq" else call_gemini,
        )
        if response.startswith("ERROR:"):
            yield {"token": response, "done": True, "error": True}
            return
        yield {"token": response, "done": False}
        yield {"token": "", "done": True}
        return

    stream = call_llama_stream(messages)
    for chunk in stream:
        yield chunk


# ─────────────────────────────────────────────────────────────────────────────
# Kubernetes Manifest Generation via Gemini
# ─────────────────────────────────────────────────────────────────────────────

K8S_MANIFEST_SYSTEM_PROMPT = """You are a Kubernetes YAML expert.
Generate production-ready Kubernetes manifests for a local Kubernetes deployment (Docker Desktop).
Return ONLY this exact structure with no extra commentary:

STATUS: Generated

GENERATED FILES:

**k8s/deployment.yaml**
```yaml
{complete content}
```

**k8s/service.yaml**
```yaml
{complete content}
```

Rules:
- Deployment: apiVersion apps/v1, kind Deployment, 1 replica, correct image, correct containerPort
- Service: apiVersion v1, kind Service, type NodePort, correct targetPort, nodePort in 30000-32767 range
- Use imagePullPolicy: Always so latest pushes are picked up
- Add resource limits: memory 256Mi, cpu 250m
- No placeholders, no template variables like ${X}
- If multiple services exist, generate one Deployment+Service pair per service
"""


def _build_k8s_manifest_message(
    project_name: str,
    deployment_name: str,
    image_repo: str,
    node_port: int,
    services: List[Dict],
    metadata: Dict,
) -> str:
    """Build the JSON message sent to Gemini for k8s manifest generation."""
    app_services = [s for s in (services or []) if str(s.get("type", "")).lower() != "database"]

    service_specs = []
    for svc in app_services:
        container_port = (
            svc.get("container_port")
            or svc.get("runtime_port")
            or svc.get("port")
            or metadata.get("port")
            or 8000
        )
        svc_name = str(svc.get("name") or "app").lower().replace(" ", "-")
        service_specs.append({
            "name": svc_name,
            "image": f"{image_repo}-{svc_name}:latest" if len(app_services) > 1 else f"{image_repo}:latest",
            "container_port": container_port,
            "type": svc.get("type", "backend"),
        })

    # Fallback: single service from metadata
    if not service_specs:
        container_port = metadata.get("port") or metadata.get("backend_port") or 8000
        service_specs.append({
            "name": project_name,
            "image": f"{image_repo}:latest",
            "container_port": container_port,
            "type": "backend",
        })

    payload = {
        "project_name": project_name,
        "deployment_name": deployment_name,
        "image_repo": image_repo,
        "node_port": node_port,
        "namespace": "default",
        "services": service_specs,
    }
    return json.dumps(payload, indent=2)


def parse_generated_k8s_files(response_text: str) -> Dict[str, str]:
    """
    Parse Gemini's response for Kubernetes manifest files.
    Returns dict: {relative_path -> yaml_content}
    """
    files: Dict[str, str] = {}
    patterns = [
        r"\*\*(?P<path>[^*\n]+)\*\*\s*```(?:yaml|yml)?\s*(?P<content>[\s\S]*?)```",
        r"(?:^|\n)#+\s*(?P<path>[^\n]+)\s*```(?:yaml|yml)?\s*(?P<content>[\s\S]*?)```",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, response_text or "", re.IGNORECASE):
            path = _normalize_generated_path(match.group("path"))
            content = (match.group("content") or "").strip()
            if path and content and path not in files:
                # Only accept paths that look like k8s manifests
                basename = os.path.basename(path).lower()
                if basename.endswith(".yaml") or basename.endswith(".yml"):
                    files[path] = content
    return files


def run_k8s_manifest_generation(
    project_name: str,
    deployment_name: str,
    image_repo: str,
    node_port: int,
    services: Optional[List[Dict]] = None,
    metadata: Optional[Dict] = None,
) -> Dict[str, str]:
    """
    Use Gemini to generate Kubernetes deployment + service manifests.
    Returns dict of {relative_path -> yaml_content} or empty dict on failure.
    """
    meta = metadata or {}
    svcs = services or []

    message = _build_k8s_manifest_message(
        project_name=project_name,
        deployment_name=deployment_name,
        image_repo=image_repo,
        node_port=node_port,
        services=svcs,
        metadata=meta,
    )

    messages = [
        {"role": "system", "content": K8S_MANIFEST_SYSTEM_PROMPT},
        {"role": "user", "content": message},
    ]

    # K8s manifest generation previously always called Gemini regardless of
    # DOCKER_LLM_PROVIDER (the same kind of bypass the Terraform agent had).
    # Route it through the same provider switch as Docker generation so Groq
    # (and Ollama) are usable here too.
    provider = get_docker_llm_provider()
    if provider == "groq":
        response = call_groq(messages, custom_options={"temperature": 0.0})
    elif provider == "ollama":
        response = call_llama(messages, custom_options={"temperature": 0.0})
    else:
        response = call_gemini(messages, custom_options={"temperature": 0.0})
    if response.startswith("ERROR:"):
        print(f"[k8s manifest gen] {provider} error: {response}")
        return {}

    files = parse_generated_k8s_files(response)
    return files

