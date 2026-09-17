"""
Port detection helpers for backend, frontend, Docker Compose, and Dockerfile EXPOSE.
Extracted from detector.py to reduce its size.
"""

import os
import json
import re
try:
    import yaml
except ImportError:
    yaml = None
from collections import Counter
from typing import Dict, List, Tuple, Optional


def _iter_compose_files(project_path: str) -> List[str]:
    """Discover docker-compose files recursively under project_path."""
    compose_names = {"docker-compose.yml", "docker-compose.yaml"}
    skip_dirs = {
        "node_modules", ".git", "__pycache__", "venv", ".venv",
        "dist", "build", ".next", ".cache", "coverage",
    }
    discovered: List[str] = []
    seen = set()

    for root, dirs, files in os.walk(project_path):
        dirs[:] = [d for d in dirs if d not in skip_dirs]
        for fname in compose_names:
            if fname not in files:
                continue
            path = os.path.join(root, fname)
            norm = os.path.normcase(os.path.normpath(path))
            if norm in seen:
                continue
            seen.add(norm)
            discovered.append(path)

    def _sort_key(path: str) -> tuple[int, str]:
        try:
            rel = os.path.relpath(path, project_path)
            depth = len(rel.split(os.sep))
        except ValueError:
            depth = 9999
        return depth, path.lower()

    discovered.sort(key=_sort_key)
    return discovered


def _detect_fullstack_structure(project_path: str) -> Dict[str, Optional[str]]:
    """
    Detect typical fullstack structure with separate frontend/backend folders.
    Looks for 'backend/server/api' and 'frontend/client/web' with package.json.
    """
    structure = {
        "is_fullstack": False,
        "has_backend": False,
        "has_frontend": False,
        "backend_path": None,
        "frontend_path": None,
    }
    _BE_TOKENS = ("backend", "server", "api", "rest-api", "api-server", "backend-api", "app")
    _FE_TOKENS = ("frontend", "client", "web", "ui", "webapp", "frontend-app", "client-app")

    def _matches(folder_name: str, token: str) -> bool:
        # Keep short tokens strict to avoid cross-classification noise.
        if len(token) < 5:
            return folder_name == token
        return folder_name == token or token in folder_name
    
    for root, dirs, files in os.walk(project_path):
        try:
            rel = os.path.relpath(root, project_path)
            depth = 0 if rel == "." else len(rel.split(os.sep))
        except ValueError:
            continue  # different drive on Windows — skip
        if depth > 5:
            continue
        
        for folder in dirs:
            folder_lower = folder.lower()
            folder_path = os.path.join(root, folder)
            pkg_path = os.path.join(folder_path, "package.json")
            
            if os.path.exists(pkg_path):
                if any(_matches(folder_lower, t) for t in _BE_TOKENS):
                    structure["has_backend"] = True
                    if structure["backend_path"] is None:
                        structure["backend_path"] = folder_path
                    structure["is_fullstack"] = True
                    print(f"🔍 Fullstack: found backend folder '{folder}'")
                if any(_matches(folder_lower, t) for t in _FE_TOKENS):
                    structure["has_frontend"] = True
                    if structure["frontend_path"] is None:
                        structure["frontend_path"] = folder_path
                    structure["is_fullstack"] = True
                    print(f"🔍 Fullstack: found frontend folder '{folder}'")
    
    return structure


def _scan_js_for_port_hint(service_path: str, max_files: int = 50) -> Optional[int]:
    """
    Scan JS/TS files under service_path for typical port patterns like:
      - process.env.PORT || 5050
      - app.listen(5050)
    Returns the first reasonable port it finds, or None.
    """
    exts = {".js", ".jsx", ".ts", ".tsx"}
    priority_patterns = [
        re.compile(r"\.listen\s*\(\s*(\d{2,5})\s*[,)]", re.IGNORECASE),
        re.compile(r"process\.env\.PORT\s*\|\|\s*(\d{2,5})", re.IGNORECASE),
        re.compile(r"process\.env\.PORT\s*\?\?\s*(\d{2,5})", re.IGNORECASE),
        re.compile(r"parseInt\s*\(\s*process\.env\.PORT\s*(?:\|\||\?\?)\s*['\"]?(\d{2,5})", re.IGNORECASE),
        re.compile(r"Number\s*\(\s*process\.env\.PORT\s*\)\s*(?:\|\||\?\?)\s*(\d{2,5})", re.IGNORECASE),
        re.compile(r"process\.env\.\w+\s*\|\|\s*(\d{2,5})", re.IGNORECASE),
        re.compile(r"(?:const|let|var)\s+PORT\s*=\s*(\d{2,5})", re.IGNORECASE),
    ]
    fallback_patterns = [
        re.compile(r"port\s*:\s*(\d{2,5})", re.IGNORECASE),
    ]

    ignore_name_parts = (
        ".test.", ".spec.",
        "vite.config", "webpack.config", "next.config", "jest.config",
    )

    def _scan_with(patterns: List[re.Pattern]) -> Optional[int]:
        files_scanned = 0
        for root, dirs, files in os.walk(service_path):
            dirs[:] = [
                d for d in dirs
                if d not in ["node_modules", "dist", "build", ".next", ".vite", ".git"]
            ]

            for file in files:
                if files_scanned >= max_files:
                    break

                _, ext = os.path.splitext(file)
                if ext.lower() not in exts:
                    continue

                file_lower = file.lower()
                if any(part in file_lower for part in ignore_name_parts):
                    continue

                file_path = os.path.join(root, file)
                try:
                    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                        content = f.read(10000)
                except Exception:
                    files_scanned += 1
                    continue

                for pat in patterns:
                    for m in pat.finditer(content):
                        for g in reversed(m.groups()):
                            if not (g and g.isdigit()):
                                continue
                            p = int(g)
                            if 1024 <= p <= 65535:
                                return p

                files_scanned += 1

            if files_scanned >= max_files:
                break
        return None

    port = _scan_with(priority_patterns)
    if port is not None:
        return port

    return _scan_with(fallback_patterns)


def _detect_port_from_package_json(project_path: str, prefer_frontend: bool = False) -> Optional[int]:
    """
    Guess port from package.json scripts.
    prefer_frontend:
      - True  => prioritise typical frontend ports
      - False => prioritise typical backend ports
    Also special-cases Vite (frontend) to default to 5173 when no explicit port is set.
    """
    pkg_json = os.path.join(project_path, "package.json")
    if not os.path.exists(pkg_json):
        return None
    
    try:
        with open(pkg_json, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"Error reading package.json for port detection: {e}")
        return None
    
    scripts = data.get("scripts", {})
    # collect dependency names to detect Vite etc.
    deps = {}
    deps.update(data.get("dependencies", {}))
    deps.update(data.get("devDependencies", {}))
    dep_names = set(k.lower() for k in deps.keys())
    
    script_strings = " ".join(str(v) for v in scripts.values())
    
    # Look for explicit PORT=XXXX patterns first
    match = re.search(r"\bPORT\s*=?\s*(\d{2,5})\b", script_strings)
    if match:
        try:
            return int(match.group(1))
        except ValueError:
            pass
    
    # Heuristic port priorities
    if prefer_frontend:
        candidates = [3000, 5173, 4173, 4200, 8080, 8000, 5000, 4000]
    else:
        candidates = [8000, 8080, 5000, 4000, 3000, 5173, 4200]
    
    # More precise matching than simple substring
    for port in candidates:
        port_pattern = re.compile(
            rf"(?:[:=]\s*{port}\b|\bPORT\s*=?\s*{port}\b)"
        )
        if port_pattern.search(script_strings):
            return port
    
    # If this looks like a Vite frontend app, default to Vite's dev port 5173
    if prefer_frontend and "vite" in dep_names:
        return 5173
    
    return None


def _scan_code_for_ports(project_path: str, max_files: int = 150) -> Optional[int]:
    """
    Generic port scan: look for host:port patterns in code.
    Used mainly for non-JS/TS projects.
    """
    exts = {".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".go", ".rb", ".php"}
    pattern = re.compile(
        r"(?:0\.0\.0\.0|127\.0\.0\.1|localhost)\s*[: ]\s*(\d{2,5})"
    )
    
    counts: Counter = Counter()
    files_scanned = 0
    
    try:
        for root, dirs, files in os.walk(project_path):
            dirs[:] = [d for d in dirs if d not in [
                "node_modules", "__pycache__", ".git", "venv", ".venv",
                "dist", "build", ".next", "target", "bin", "obj", "out"
            ]]
            
            for file in files:
                if files_scanned >= max_files:
                    break
                _, ext = os.path.splitext(file)
                if ext.lower() not in exts:
                    continue
                
                file_path = os.path.join(root, file)
                try:
                    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                        content = f.read(8000)
                    for m in pattern.finditer(content):
                        try:
                            port = int(m.group(1))
                            if 1024 <= port <= 65535:
                                counts[port] += 1
                        except ValueError:
                            continue
                except Exception:
                    continue
                files_scanned += 1
            
            if files_scanned >= max_files:
                break
    except Exception as e:
        print(f"Port scan error: {e}")
    
    if not counts:
        return None
    
    # Most frequent port
    port, _ = counts.most_common(1)[0]
    return port


def _parse_docker_compose_ports(project_path: str) -> Dict[str, List[Tuple[int, int]]]:
    """
    Parse docker-compose.yml/.yaml using a proper YAML parser (PyYAML).

    Supports:
      services:
        frontend:
          ports:
            - "5173:80"
            - 3000:3000
        backend:
          ports: "5000:5000"
        db:
          ports:
            - "27017:27017"

    Returns:
        {
            "<service_name>": [(host_port, container_port), ...],
            ...
        }
    """
    compose_paths = _iter_compose_files(project_path)

    # No compose files found
    if not compose_paths:
        return {}

    # If PyYAML isn't installed, fall back to "no ports" rather than breaking
    if yaml is None:
        print("Warning: PyYAML not installed; docker-compose ports will not be parsed.")
        return {}

    service_ports: Dict[str, List[Tuple[int, int]]] = {}

    for compose_path in compose_paths:
        try:
            with open(compose_path, "r", encoding="utf-8", errors="ignore") as f:
                data = yaml.safe_load(f) or {}
        except Exception as e:
            print(f"Error parsing docker-compose with YAML ({compose_path}): {e}")
            continue

        services = data.get("services", {})

        for svc_name, svc_def in services.items():
            if not isinstance(svc_def, dict):
                continue

            ports_field = svc_def.get("ports")
            if not ports_field:
                continue

            # Normalize ports_field to a list
            if isinstance(ports_field, (str, int)):
                ports_list = [ports_field]
            elif isinstance(ports_field, list):
                ports_list = ports_field
            else:
                # Unknown type, skip
                continue

            mappings: List[Tuple[int, int]] = []

            for entry in ports_list:
                # Possible formats:
                # - "3000:3000"
                # - "0.0.0.0:3000:3000"
                # - "3000"
                # - 3000
                host_port: Optional[int] = None
                container_port: Optional[int] = None

                if isinstance(entry, int):
                    # `ports: - 3000` means container port 3000, host randomized.
                    # We can treat this as (3000, 3000) for our detection purposes,
                    # or skip host mapping. Here we treat host == container.
                    host_port = entry
                    container_port = entry
                else:
                    # It's a string
                    text = str(entry).strip().strip('"').strip("'")
                    # Drop protocol suffix if present, e.g. "3000:3000/tcp"
                    if "/" in text:
                        text = text.split("/", 1)[0]

                    parts = text.split(":")
                    # "3000" => only container port (host random)
                    if len(parts) == 1 and parts[0].isdigit():
                        container_port = int(parts[0])
                        # we *could* leave host_port as None; but for your
                        # detector it's more useful to treat host == container
                        host_port = container_port
                    elif len(parts) >= 2:
                        # Could be "host:container" or "ip:host:container"
                        # We take last two segments as host/container
                        maybe_host = parts[-2]
                        maybe_container = parts[-1]
                        if maybe_host.isdigit() and maybe_container.isdigit():
                            host_port = int(maybe_host)
                            container_port = int(maybe_container)

                if host_port is not None and container_port is not None:
                    mappings.append((host_port, container_port))

            if mappings:
                service_ports.setdefault(svc_name, []).extend(mappings)

    for svc_name, mappings in list(service_ports.items()):
        service_ports[svc_name] = sorted(set(mappings))

    return service_ports


def _extract_port_from_value(value: object) -> Optional[int]:
    if value is None:
        return None
    text = str(value).strip().strip('"').strip("'")
    if not text:
        return None
    if text.isdigit():
        p = int(text)
        if 1 <= p <= 65535:
            return p
    m = re.search(r"(\d{2,5})", text)
    if not m:
        return None
    try:
        p = int(m.group(1))
    except ValueError:
        return None
    if 1 <= p <= 65535:
        return p
    return None


def _extract_compose_env_hints(svc_def: Dict[str, object]) -> tuple[Optional[int], Optional[int], Optional[int]]:
    """
    Parse compose service environment for port hints and return:
      (backend_hint, frontend_hint, generic_port_hint)
    """
    backend_hint: Optional[int] = None
    frontend_hint: Optional[int] = None
    generic_hint: Optional[int] = None

    env_field = svc_def.get("environment")
    env_items: List[tuple[str, object]] = []

    if isinstance(env_field, dict):
        env_items = [(str(k), v) for k, v in env_field.items()]
    elif isinstance(env_field, list):
        for item in env_field:
            if isinstance(item, str) and "=" in item:
                key, value = item.split("=", 1)
                env_items.append((key, value))
            elif isinstance(item, dict):
                for k, v in item.items():
                    env_items.append((str(k), v))

    for raw_key, raw_value in env_items:
        key = str(raw_key).strip().upper()
        port = _extract_port_from_value(raw_value)
        if port is None:
            continue

        if key in ("BACKEND_PORT", "SERVER_PORT", "API_PORT"):
            if backend_hint is None:
                backend_hint = port
            continue

        if key in ("FRONTEND_PORT", "CLIENT_PORT", "VITE_PORT", "REACT_APP_PORT", "NEXT_PUBLIC_PORT", "VITE_DEV_PORT"):
            if frontend_hint is None:
                frontend_hint = port
            continue

        if key == "PORT" and generic_hint is None:
            generic_hint = port

    return backend_hint, frontend_hint, generic_hint


def _parse_docker_compose_env_ports(project_path: str) -> Dict[str, Dict[str, Optional[int]]]:
    """
    Parse docker-compose service `environment` sections for backend/frontend/generic
    port hints.
    """
    compose_paths = _iter_compose_files(project_path)
    if not compose_paths:
        return {}
    if yaml is None:
        return {}

    hints: Dict[str, Dict[str, Optional[int]]] = {}
    for compose_path in compose_paths:
        try:
            with open(compose_path, "r", encoding="utf-8", errors="ignore") as f:
                data = yaml.safe_load(f) or {}
        except Exception:
            continue

        services = data.get("services", {})
        if not isinstance(services, dict):
            continue

        for svc_name, svc_def in services.items():
            if not isinstance(svc_def, dict):
                continue
            backend_hint, frontend_hint, generic_hint = _extract_compose_env_hints(svc_def)
            if backend_hint is None and frontend_hint is None and generic_hint is None:
                continue
            hints[str(svc_name)] = {
                "backend_port": backend_hint,
                "frontend_port": frontend_hint,
                "generic_port": generic_hint,
            }

    return hints


def _parse_dockerfile_expose_ports(project_path: str) -> List[int]:
    """
    Collect exposed ports from any Dockerfile found in the project.
    Supports:
      EXPOSE 3000
      EXPOSE 80 443
      EXPOSE 8080/tcp
      EXPOSE 8081/udp
    """
    ports = []
    dockerfiles = ["Dockerfile", "dockerfile"]

    for root, dirs, files in os.walk(project_path):
        dirs[:] = [d for d in dirs if d not in ["node_modules", ".git", "__pycache__", "dist", "build"]]

        for file in files:
            if file in dockerfiles:
                file_path = os.path.join(root, file)
                try:
                    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                        for line in f:
                            m = re.search(r"EXPOSE\s+(.*)", line, re.IGNORECASE)
                            if not m:
                                continue

                            tokens = m.group(1).split()
                            for token in tokens:
                                token = token.strip()
                                token = token.split("/")[0]  # drop /tcp
                                if token.isdigit():
                                    ports.append(int(token))
                except:
                    pass

    return sorted(set(ports))


def _classify_docker_service(name: str) -> str:
    """
    Classify a docker-compose service name into one of:
    'frontend', 'backend', 'database', or 'other'.
    """
    n = name.lower()

    FRONTEND_EXACT = {"frontend", "client", "web", "ui", "webapp", "react"}
    FRONTEND_SUBSTR = ["front", "client", "ui"]
    BACKEND_EXACT = {"backend", "api", "server", "express", "node", "app"}
    BACKEND_SUBSTR = ["back", "api", "server"]
    DB_KEYS = ["mongo", "mysql", "postgres", "pgsql", "redis", "db"]

    if any(k in n for k in DB_KEYS):
        return "database"
    if n in FRONTEND_EXACT or any(k in n for k in FRONTEND_SUBSTR):
        return "frontend"
    if n in BACKEND_EXACT or any(k in n for k in BACKEND_SUBSTR):
        return "backend"
    return "other"


def detect_ports_for_project(
    project_path: str,
    language: str,
    framework: str,
    base_port: Optional[int]
) -> Dict[str, Optional[int]]:
    """
    Detect backend and frontend ports.

    - For JS/TS: look for fullstack structure and read package.json,
      then refine backend with a JS code scan.
      Also consult .env for port hints (root and, for fullstack, backend/frontend subfolders).
    - For others: scan code for host:port; fall back to base_port / defaults,
      with .env overrides.

    Additionally:
    - Parse docker-compose.yml/.yaml and expose:
        docker_backend_ports             (HOST ports on the machine)
        docker_frontend_ports            (HOST ports on the machine)
        docker_database_ports            (HOST ports on the machine)
        docker_other_ports               (service_name -> [HOST ports])
        docker_backend_container_ports   (CONTAINER ports inside backend containers)
        docker_frontend_container_ports  (CONTAINER ports inside frontend containers)
        docker_database_container_ports  (CONTAINER ports inside DB containers)
        docker_other_container_ports     (service_name -> [CONTAINER ports])
    - Parse Dockerfile EXPOSE lines to collect container ports in docker_expose_ports.
    """
    # Import here to avoid circular imports (detector.py owns _read_env_key_values)
    from .detector import _read_env_key_values

    backend_port: Optional[int] = None
    frontend_port: Optional[int] = None
    backend_port_source: Optional[str] = None
    frontend_port_source: Optional[str] = None

    # ---- read env key/values at the project root ----
    root_env_kv = _read_env_key_values(project_path)

    def _extract_port(val: str) -> Optional[int]:
        return _extract_port_from_value(val)

    def _extract_env_ports(env_kv: Dict[str, str]):
        """
        Given a mapping of env key -> value, extract:
          - backend_env_port: from BACKEND_PORT/SERVER_PORT/API_PORT
          - frontend_env_port: from FRONTEND_PORT/CLIENT_PORT/VITE_PORT/REACT_APP_PORT/NEXT_PUBLIC_PORT/VITE_DEV_PORT
          - generic_env_port: from PORT
        """
        backend_env_port: Optional[int] = None
        frontend_env_port: Optional[int] = None
        generic_env_port: Optional[int] = None

        for key, value in env_kv.items():
            p = _extract_port(value)
            if p is None:
                continue
            ku = key.upper()

            # explicit backend hints
            if ku in ("BACKEND_PORT", "SERVER_PORT", "API_PORT"):
                if backend_env_port is None:
                    backend_env_port = p
                continue

            # explicit frontend hints
            if ku in (
                "FRONTEND_PORT", "CLIENT_PORT", "VITE_PORT",
                "REACT_APP_PORT", "NEXT_PUBLIC_PORT", "VITE_DEV_PORT",
            ):
                if frontend_env_port is None:
                    frontend_env_port = p
                continue

            # generic PORT
            if ku == "PORT" and generic_env_port is None:
                generic_env_port = p

        return backend_env_port, frontend_env_port, generic_env_port

    # root env-derived ports (may be overridden for fullstack)
    backend_env_port, frontend_env_port, generic_env_port = _extract_env_ports(root_env_kv)

    is_js_ts = language in ["JavaScript", "TypeScript"]

    if is_js_ts:
        fullstack = _detect_fullstack_structure(project_path)
        is_fullstack = fullstack.get("is_fullstack", False)

        # ---- FULLSTACK JS/TS (client + server folders) ----
        if is_fullstack:
            backend_path = fullstack.get("backend_path")
            frontend_path = fullstack.get("frontend_path")

            # Backend-specific env: backend/.env overrides root .env
            if backend_path:
                backend_env_kv = _read_env_key_values(backend_path)
                b_backend_env_port, b_frontend_env_port, b_generic_env_port = _extract_env_ports(backend_env_kv)


                if b_backend_env_port is not None:
                    backend_env_port = b_backend_env_port
                elif b_generic_env_port is not None:
                    # FIX: In fullstack project, treat backend/.env PORT as backend port
                    # (not just generic fallback). This ensures PORT=4000 from backend/.env
                    # takes priority over package.json default of 3000.
                    backend_env_port = b_generic_env_port

                # Preserve frontend explicit hints found in backend env files
                # (common in MERN templates that colocate both ports).
                if frontend_env_port is None and b_frontend_env_port is not None:
                    frontend_env_port = b_frontend_env_port

            # Frontend-specific env: frontend/.env overrides root .env
            if frontend_path:
                frontend_env_kv = _read_env_key_values(frontend_path)
                _, f_frontend_env_port, f_generic_env_port = _extract_env_ports(frontend_env_kv)

                if f_frontend_env_port is not None:
                    frontend_env_port = f_frontend_env_port
                elif f_generic_env_port is not None:
                    frontend_env_port = f_generic_env_port

            # Backend: env override > package.json + code scan > generic PORT
            if backend_env_port is not None:
                backend_port = backend_env_port
                backend_port_source = "env"
            else:
                if fullstack.get("has_backend") and backend_path:
                    pkg_port = _detect_port_from_package_json(
                        backend_path, prefer_frontend=False
                    )
                    if pkg_port is not None:
                        backend_port = pkg_port
                        backend_port_source = "package"
                    code_port = _scan_js_for_port_hint(backend_path)
                    if code_port is not None:
                        backend_port = code_port
                        backend_port_source = "source"
                else:
                    pkg_port = _detect_port_from_package_json(
                        project_path, prefer_frontend=False
                    )
                    if pkg_port is not None:
                        backend_port = pkg_port
                        backend_port_source = "package"
                    code_port = _scan_js_for_port_hint(project_path)
                    if code_port is not None:
                        backend_port = code_port
                        backend_port_source = "source"

                if backend_port is None and generic_env_port is not None:
                    backend_port = generic_env_port
                    backend_port_source = "env"

            # Frontend: env override > package.json (frontend preference)
            if fullstack.get("has_frontend") and frontend_path:
                if frontend_env_port is not None:
                    frontend_port = frontend_env_port
                    frontend_port_source = "env"
                else:
                    frontend_port = _detect_port_from_package_json(
                        frontend_path, prefer_frontend=True
                    )
                    if frontend_port is not None:
                        frontend_port_source = "package"
            # we do NOT use generic PORT for frontend in fullstack case

        # ---- SINGLE JS/TS PROJECT (no separate client/server folders) ----
        else:
            # treat React / Next.js as frontend-only by default
            is_frontend_only = framework in ["React", "Next.js"]

            if is_frontend_only:
                # FRONTEND: env > generic PORT > package.json + JS scan
                if frontend_env_port is not None:
                    frontend_port = frontend_env_port
                    frontend_port_source = "env"
                elif generic_env_port is not None:
                    frontend_port = generic_env_port
                    frontend_port_source = "env"
                else:
                    pkg_port = _detect_port_from_package_json(
                        project_path, prefer_frontend=True
                    )
                    if pkg_port is not None:
                        frontend_port = pkg_port
                        frontend_port_source = "package"
                    code_port = _scan_js_for_port_hint(project_path)
                    if code_port is not None:
                        frontend_port = code_port
                        frontend_port_source = "source"

                # BACKEND: only if explicitly defined in env
                if backend_env_port is not None:
                    backend_port = backend_env_port
                    backend_port_source = "env"
                else:
                    backend_port = None
                    backend_port_source = None

            else:
                # Non-React/Next single JS/TS: treat as backend app
                # Backend: env > package.json + JS scan > generic PORT
                if backend_env_port is not None:
                    backend_port = backend_env_port
                    backend_port_source = "env"
                else:
                    pkg_port = _detect_port_from_package_json(
                        project_path, prefer_frontend=False
                    )
                    if pkg_port is not None:
                        backend_port = pkg_port
                        backend_port_source = "package"
                    code_port = _scan_js_for_port_hint(project_path)
                    if code_port is not None:
                        backend_port = code_port
                        backend_port_source = "source"

                    if backend_port is None and generic_env_port is not None:
                        backend_port = generic_env_port
                        backend_port_source = "env"

                # Frontend only if explicitly given in env
                if frontend_env_port is not None:
                    frontend_port = frontend_env_port
                    frontend_port_source = "env"

    else:
        # ---- NON JS/TS PROJECTS ----
        detected = _scan_code_for_ports(project_path)
        if detected:
            backend_port = detected
            backend_port_source = "source"
        else:
            backend_port = base_port
            if backend_port is not None:
                backend_port_source = "base"
            if backend_port is None:
                framework_default_ports = {
                    "Express.js": 3000,
                    "Fastify": 3000,
                    "NestJS": 3000,
                    "Next.js": 3000,
                    "Vite": 5173,
                }
                default_ports = {
                    "JavaScript": 3000,
                    "TypeScript": 3000,
                    "Python": 8000,
                    "Java": 8080,
                    "Go": 8080,
                    "Ruby": 3000,
                    "PHP": 8000
                }
                backend_port = framework_default_ports.get(framework)
                if backend_port is None:
                    backend_port = default_ports.get(language, 8000)
                backend_port_source = "default"

        # Env overrides for backend
        if backend_env_port is not None:
            backend_port = backend_env_port
            backend_port_source = "env"
        elif generic_env_port is not None:
            backend_port = generic_env_port
            backend_port_source = "env"

        # Allow explicit frontend env even in non-JS projects (edge multi-service)
        if frontend_env_port is not None:
            frontend_port = frontend_env_port
            frontend_port_source = "env"

    # ---- Docker-compose ports ----
    docker_service_ports = _parse_docker_compose_ports(project_path)
    docker_compose_env_hints = _parse_docker_compose_env_ports(project_path)

    # ---- Dockerfile EXPOSE ports ----
    dockerfile_exposed_ports = _parse_dockerfile_expose_ports(project_path)

    docker_backend_ports: List[int] = []
    docker_frontend_ports: List[int] = []
    docker_database_ports: List[int] = []
    docker_other_ports: Dict[str, List[int]] = {}
    compose_backend_env_ports: List[int] = []
    compose_frontend_env_ports: List[int] = []

    # Container (internal) ports for the same services
    docker_backend_container_ports: List[int] = []
    docker_frontend_container_ports: List[int] = []
    docker_database_container_ports: List[int] = []
    docker_other_container_ports: Dict[str, List[int]] = {}

    for svc, mappings in docker_service_ports.items():
        role = _classify_docker_service(svc)
        host_ports = [hp for (hp, cp) in mappings]
        container_ports = [cp for (hp, cp) in mappings]

        if not host_ports and not container_ports:
            continue

        if role == "backend":
            docker_backend_ports.extend(host_ports)
            docker_backend_container_ports.extend(container_ports)
            # OLD behavior was: if backend_port is None and host_ports: backend_port = host_ports[0]
            # We now defer the canonical override until after all services are processed.
        elif role == "frontend":
            docker_frontend_ports.extend(host_ports)
            docker_frontend_container_ports.extend(container_ports)
        elif role == "database":
            docker_database_ports.extend(host_ports)
            docker_database_container_ports.extend(container_ports)
        else:
            if host_ports:
                docker_other_ports[svc] = host_ports
            if container_ports:
                docker_other_container_ports[svc] = container_ports

    for svc_name, svc_hints in docker_compose_env_hints.items():
        role = _classify_docker_service(svc_name)
        backend_hint = svc_hints.get("backend_port")
        frontend_hint = svc_hints.get("frontend_port")
        generic_hint = svc_hints.get("generic_port")

        if role == "backend":
            candidate = backend_hint if backend_hint is not None else generic_hint
            if candidate is not None:
                compose_backend_env_ports.append(candidate)
        elif role == "frontend":
            candidate = frontend_hint if frontend_hint is not None else generic_hint
            if candidate is not None:
                compose_frontend_env_ports.append(candidate)
        else:
            # Fallback for ambiguous service names.
            if backend_hint is not None:
                compose_backend_env_ports.append(backend_hint)
            elif generic_hint is not None and frontend_hint is None:
                compose_backend_env_ports.append(generic_hint)

            if frontend_hint is not None:
                compose_frontend_env_ports.append(frontend_hint)

    # de-duplicate docker port lists
    docker_backend_ports = sorted(set(docker_backend_ports))
    docker_frontend_ports = sorted(set(docker_frontend_ports))
    docker_database_ports = sorted(set(docker_database_ports))

    docker_backend_container_ports = sorted(set(docker_backend_container_ports))
    docker_frontend_container_ports = sorted(set(docker_frontend_container_ports))
    docker_database_container_ports = sorted(set(docker_database_container_ports))
    compose_backend_env_ports = sorted(set(compose_backend_env_ports))
    compose_frontend_env_ports = sorted(set(compose_frontend_env_ports))

    # --- Compose-driven hints (conservative) ---
    # Only use compose hints if we did not find a better hint earlier.
    if backend_port is None:
        if compose_backend_env_ports:
            backend_port = compose_backend_env_ports[0]
            backend_port_source = "compose_env"
        elif docker_backend_container_ports:
            backend_port = docker_backend_container_ports[0]
            backend_port_source = "compose"
        elif docker_backend_ports:
            backend_port = docker_backend_ports[0]
            backend_port_source = "compose"

    if frontend_port is None:
        if compose_frontend_env_ports:
            frontend_port = compose_frontend_env_ports[0]
            frontend_port_source = "compose_env"
        elif docker_frontend_container_ports:
            if any(p in (80, 443) for p in docker_frontend_container_ports) and docker_frontend_ports:
                # For static frontend containers, runtime/dev port maps to compose host port.
                frontend_port = docker_frontend_ports[0]
            else:
                # Otherwise runtime port is the app/container-side port.
                frontend_port = docker_frontend_container_ports[0]
            frontend_port_source = "compose"
        elif docker_frontend_ports:
            frontend_port = docker_frontend_ports[0]
            frontend_port_source = "compose"

    return {
        "backend_port": backend_port,
        "frontend_port": frontend_port,
        "backend_port_source": backend_port_source,
        "frontend_port_source": frontend_port_source,
        "backend_compose_env_port": compose_backend_env_ports[0] if compose_backend_env_ports else None,
        "frontend_compose_env_port": compose_frontend_env_ports[0] if compose_frontend_env_ports else None,

        # HOST ports from docker-compose (what you bind on the machine)
        "docker_backend_ports": docker_backend_ports or None,
        "docker_frontend_ports": docker_frontend_ports or None,
        "docker_database_ports": docker_database_ports or None,
        "docker_other_ports": docker_other_ports or None,

        # CONTAINER ports from docker-compose (internal container-side ports)
        "docker_backend_container_ports": docker_backend_container_ports or None,
        "docker_frontend_container_ports": docker_frontend_container_ports or None,
        "docker_database_container_ports": docker_database_container_ports or None,
        "docker_other_container_ports": docker_other_container_ports or None,

        # Container ports from Dockerfile EXPOSE lines
        "docker_expose_ports": dockerfile_exposed_ports or None,
    }
