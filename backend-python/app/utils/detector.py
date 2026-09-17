"""
detector.py — Orchestrator + backwards-compatible re-export hub.
"""

import os
import json
import sys
from typing import Dict, List, Optional

# ── Re-exports from detection_constants ────────────────────────────────
from .detection_constants import (                          # noqa: F401
    PORT_SCHEMA_VERSION,
    LANGUAGE_INDICATORS,
    FRAMEWORK_INDICATORS,
    FRAMEWORK_LANGUAGES,
    DB_INDICATORS,
    DB_ENV_KEYWORDS,
    BACKEND_DEPS,
    FRONTEND_DEPS,
    SKIP_DIRS,
    PYTHON_BACKEND_DEPS,
    DB_KEYWORDS,
    PYTHON_SKIP_DIRS,
    _languages_compatible,
    norm_path,
    _normalize_dep_name,
)

# ── Re-exports from detection_language ─────────────────────────────────
from .detection_language import (                           # noqa: F401
    parse_dependencies_file,
    get_runtime_info,
    heuristic_language_detection,
    heuristic_framework_detection,
)

# ── Re-exports from detection_ports ────────────────────────────────────
from .detection_ports import (                              # noqa: F401
    _detect_fullstack_structure,
    _scan_js_for_port_hint,
    _detect_port_from_package_json,
    _scan_code_for_ports,
    _parse_docker_compose_ports,
    _parse_dockerfile_expose_ports,
    _classify_docker_service,
    detect_ports_for_project,
)

# ── Re-exports from detection_database ─────────────────────────────────
from .detection_database import (                           # noqa: F401
    _infer_database_port,
    detect_databases,
    detect_db_and_ports,
)

# ── Re-exports from detection_services ─────────────────────────────────
from .detection_services import (                           # noqa: F401
    _INFRA_DIRS,
    _SERVICE_INDICATOR_FILES,
    _is_service_candidate,
    _infer_service_type,
    _find_all_services_by_deps,
    _suppress_root_if_children_found,
    _drop_empty_shells,
    _normalize_service_path,
    _detect_package_manager,
    _find_python_services,
    _merge_node_python_stubs,
    infer_services,
)

# ── Direct imports used by functions that remain in this file ──────────
from .ml_analyzer import get_ml_analyzer
from .command_extractor import (
    extract_nodejs_commands,
    extract_python_commands,
    extract_port_from_project,
    extract_frontend_port,
    extract_database_info,
)


# =====================================================================
# Functions that remain in detector.py
# =====================================================================

def _ensure_utf8_stdout() -> None:
    """
    Best-effort safeguard for Windows cp1252 consoles.
    Some debug logs in detection helpers contain non-ASCII characters;
    if stdout is not UTF-8 those prints can raise UnicodeEncodeError and
    incorrectly trip the broad detect_framework exception path.
    """
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    except Exception:
        # Logging should never block detection flow.
        pass

def detect_docker_files(project_path: str) -> Dict:
    """Detect Docker files"""
    result = {
        "dockerfile": False,
        "docker_compose": False,
        "detected_files": []
    }
    
    docker_files = ['Dockerfile', 'dockerfile']
    compose_files = ['docker-compose.yml', 'docker-compose.yaml']
    
    try:
        for root, dirs, files in os.walk(project_path):
            dirs[:] = [d for d in dirs if d not in ['node_modules', '__pycache__', '.git', 'venv']]
            
            for file in files:
                if file in docker_files:
                    result["dockerfile"] = True
                    rel = os.path.relpath(os.path.join(root, file), project_path).replace("\\", "/")
                    if rel not in result["detected_files"]:
                        result["detected_files"].append(rel)
                
                if file in compose_files:
                    result["docker_compose"] = True
                    rel = os.path.relpath(os.path.join(root, file), project_path).replace("\\", "/")
                    if rel not in result["detected_files"]:
                        result["detected_files"].append(rel)
    
    except Exception as e:
        print(f"Docker detection error: {e}")
    
    return result


def detect_env_variables(project_path: str) -> List[str]:
    """Detect environment variables from .env files (keys only)"""
    env_vars = []
    env_files = ['.env', '.env.local', '.env.development', '.env.production', '.env.test', '.env.example', '.env.sample']
    
    for env_file in env_files:
        env_path = os.path.join(project_path, env_file)
        if os.path.exists(env_path):
            try:
                with open(env_path, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith('#') and '=' in line:
                            key = line.split('=')[0].strip()
                            if key and key not in env_vars:
                                env_vars.append(key)
            except Exception as e:
                print(f"Error reading {env_file}: {e}")
    
    return env_vars


def _read_env_key_values(project_path: str) -> Dict[str, str]:
    """Internal helper: read key=value pairs from typical .env files (for DB/port hints)."""
    env_values: Dict[str, str] = {}
    port_override_keys = {
        "PORT",
        "BACKEND_PORT", "SERVER_PORT", "API_PORT",
        "FRONTEND_PORT", "CLIENT_PORT",
        "VITE_PORT", "REACT_APP_PORT", "NEXT_PUBLIC_PORT", "VITE_DEV_PORT",
    }

    def _parse_env_line(line: str) -> Optional[tuple[str, str]]:
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            return None
        key, value = line.split('=', 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if not key:
            return None
        return key, value

    env_files = [
        '.env', '.env.local', '.env.development', '.env.production',
        '.env.test', '.env.example'
    ]
    for env_file in env_files:
        env_path = os.path.join(project_path, env_file)
        if os.path.exists(env_path):
            try:
                with open(env_path, 'r', encoding='utf-8') as f:
                    for line in f:
                        parsed = _parse_env_line(line)
                        if not parsed:
                            continue
                        key, value = parsed
                        env_values.setdefault(key, value)
            except Exception as e:
                print(f"Error reading {env_file} for values: {e}")

    # Explicitly allow .env.local to override only port-related keys.
    env_local_path = os.path.join(project_path, '.env.local')
    if os.path.exists(env_local_path):
        try:
            with open(env_local_path, 'r', encoding='utf-8') as f:
                for line in f:
                    parsed = _parse_env_line(line)
                    if not parsed:
                        continue
                    key, value = parsed
                    if key in port_override_keys:
                        env_values[key] = value
        except Exception as e:
            print(f"Error reading .env.local for port overrides: {e}")

    return env_values


def find_project_root(extracted_path: str, max_depth: int = 5) -> str:
    """Find actual project root recursively"""
    try:
        framework_files = [
            'package.json', 'requirements.txt', 'pyproject.toml', 'Pipfile', 'poetry.lock',
            'pom.xml', 'build.gradle', 'composer.json', 'go.mod', 'Gemfile', 'Cargo.toml',
            'setup.py'
        ]
        source_extensions = {'.js', '.jsx', '.ts', '.tsx', '.py', '.java', '.go', '.rb', '.php'}

        excluded_dirs = {
            'infra', 'node_modules', '.git', '__pycache__', '.terraform',
            'venv', '.venv', 'dist', 'build', '.next'
        }

        def _workspace_patterns(pkg: Dict) -> List[str]:
            workspaces = pkg.get("workspaces")
            if isinstance(workspaces, list):
                return [str(w).strip() for w in workspaces if isinstance(w, str) and str(w).strip()]
            if isinstance(workspaces, dict):
                packages = workspaces.get("packages")
                if isinstance(packages, list):
                    return [str(w).strip() for w in packages if isinstance(w, str) and str(w).strip()]
            return []

        def _resolve_workspace_paths(base_dir: str, patterns: List[str]) -> List[str]:
            resolved: List[str] = []
            seen = set()
            for pattern in patterns:
                if "*" in pattern:
                    if pattern.endswith("/*"):
                        prefix = pattern[:-2].strip("/\\")
                        parent = os.path.join(base_dir, prefix) if prefix else base_dir
                        if not os.path.isdir(parent):
                            continue
                        try:
                            for name in os.listdir(parent):
                                candidate = os.path.join(parent, name)
                                if os.path.isdir(candidate):
                                    norm = os.path.normpath(candidate)
                                    if norm not in seen:
                                        seen.add(norm)
                                        resolved.append(candidate)
                        except Exception:
                            continue
                    continue

                candidate = os.path.join(base_dir, pattern.strip("/\\"))
                if os.path.isdir(candidate):
                    norm = os.path.normpath(candidate)
                    if norm not in seen:
                        seen.add(norm)
                        resolved.append(candidate)

            return resolved

        def _find_workspace_root(start_path: str) -> Optional[str]:
            queue: List[tuple[str, int]] = [(os.path.abspath(start_path), 0)]
            seen = set()

            while queue:
                current_path, depth = queue.pop(0)
                current_norm = os.path.normpath(current_path)
                if current_norm in seen:
                    continue
                seen.add(current_norm)

                pkg_path = os.path.join(current_path, "package.json")
                if os.path.exists(pkg_path):
                    try:
                        with open(pkg_path, "r", encoding="utf-8", errors="ignore") as f:
                            pkg = json.load(f)
                        patterns = _workspace_patterns(pkg)
                    except Exception:
                        patterns = []

                    if patterns:
                        workspace_dirs = _resolve_workspace_paths(current_path, patterns)
                        if workspace_dirs:
                            return current_path

                if depth >= max_depth:
                    continue

                try:
                    entries = os.listdir(current_path)
                except Exception:
                    continue

                for name in entries:
                    abs_path = os.path.join(current_path, name)
                    if not os.path.isdir(abs_path):
                        continue
                    if name in excluded_dirs:
                        continue
                    queue.append((abs_path, depth + 1))

            return None

        workspace_root = _find_workspace_root(extracted_path)
        if workspace_root:
            if workspace_root != extracted_path:
                print(f"Found workspace root: {workspace_root}")
            return workspace_root

        def has_manifest(path: str) -> bool:
            try:
                return any(os.path.exists(os.path.join(path, f)) for f in framework_files)
            except Exception:
                return False

        def has_source_files(path: str) -> bool:
            source_hint_dirs = {
                "src", "app", "lib", "client", "server", "backend",
                "frontend", "api", "web", "ui", "packages",
            }
            try:
                for name in os.listdir(path):
                    abs_path = os.path.join(path, name)
                    if os.path.isfile(abs_path):
                        _, ext = os.path.splitext(name)
                        if ext.lower() in source_extensions:
                            return True
                    # Accept conventional code folders for source signal, but
                    # not when that folder is itself a self-contained nested
                    # project (has its own manifest) — that's a real child
                    # service, not "source belonging to this level", and
                    # should be reached by descending further instead of
                    # anchoring the wrapper directory as the root.
                    if (
                        os.path.isdir(abs_path)
                        and name.lower() in source_hint_dirs
                        and not has_manifest(abs_path)
                    ):
                        try:
                            base_depth = abs_path.count(os.sep)
                            for sub_root, sub_dirs, sub_files in os.walk(abs_path):
                                depth = sub_root.count(os.sep) - base_depth
                                if depth > 2:
                                    sub_dirs[:] = []
                                    continue
                                for child in sub_files:
                                    _, child_ext = os.path.splitext(child)
                                    if child_ext.lower() in source_extensions:
                                        return True
                        except Exception:
                            continue
            except Exception:
                return False
            return False

        def direct_manifest_majority(path: str) -> bool:
            try:
                entries = os.listdir(path)
            except Exception:
                return False
            direct_dirs = []
            for name in entries:
                abs_path = os.path.join(path, name)
                if not os.path.isdir(abs_path):
                    continue
                if name in excluded_dirs:
                    continue
                direct_dirs.append(abs_path)
            if not direct_dirs:
                return False
            manifest_children = sum(1 for d in direct_dirs if has_manifest(d))
            return manifest_children > (len(direct_dirs) / 2.0)

        def search_unique(current_path: str, depth: int = 0) -> Optional[str]:
            """
            Search down to max_depth for manifest files. If exactly one unique
            manifest-containing directory exists in this subtree, return it.
            If multiple candidates exist, return None (ambiguous).
            """
            if depth > max_depth:
                return None

            if has_manifest(current_path):
                # Only return this path if it contains real source files;
                # otherwise keep descending to avoid stale wrapper manifests.
                if has_source_files(current_path):
                    return current_path

            try:
                entries = os.listdir(current_path)
            except Exception as e:
                print(f"Error scanning {current_path}: {e}")
                return None

            found: List[str] = []
            for name in entries:
                abs_path = os.path.join(current_path, name)
                if not os.path.isdir(abs_path):
                    continue
                if name in excluded_dirs:
                    continue
                child = search_unique(abs_path, depth + 1)
                if child:
                    found.append(child)

            # Deduplicate candidates
            uniq: List[str] = []
            seen = set()
            for p in found:
                if p in seen:
                    continue
                seen.add(p)
                uniq.append(p)

            if len(uniq) == 1:
                return uniq[0]

            # Ambiguous subtree: if most direct children have manifests,
            # treat current_path as the project root.
            if len(uniq) > 1 and direct_manifest_majority(current_path):
                return current_path

            # If this dir has a manifest and no deeper manifest candidates,
            # keep this directory as root (prevents over-promoting parents).
            if len(uniq) == 0 and has_manifest(current_path):
                return current_path
            return None

        final_path = search_unique(extracted_path) or extracted_path

        # Guard: if search_unique descended into a service-named folder,
        # check whether its parent contains sibling service folders.
        # If so, the parent is the real root, not the child.
        _SERVICE_FOLDER_NAMES = {
            "backend", "frontend", "client", "server", "api", "web", "ui",
            "app", "src", "admin", "dashboard", "worker", "services",
            "front", "back", "bend", "nodejs", "node", "express",
            "react", "vue", "angular", "spa",
        }
        _NOISE_DIRS = {
            ".git", ".github", ".circleci", ".vscode", ".idea", "docs",
            "scripts", "nginx", "node_modules", "__pycache__", "dist",
            "build", "coverage", ".cache", ".next", "db", "data",
            "database", "config", "configs", "routes", "models",
            "middleware", "middlewares", "controllers", "utils", "helpers",
            "lib", "public", "static", "assets", "images", "tf", "toolbox",
        }

        if final_path != extracted_path:
            leaf_name = os.path.basename(final_path).lower()
            if leaf_name in _SERVICE_FOLDER_NAMES:
                parent = os.path.dirname(final_path)
                try:
                    parent_contents = set(os.listdir(parent))
                    _DEP_FILES = {
                        "package.json", "requirements.txt", "pyproject.toml",
                        "pom.xml", "go.mod", "manage.py",
                    }
                    parent_has_dep = bool(parent_contents & _DEP_FILES)
                    siblings = [
                        d for d in parent_contents
                        if os.path.isdir(os.path.join(parent, d))
                        and d not in _NOISE_DIRS
                        and d.lower() in _SERVICE_FOLDER_NAMES
                    ]
                    # Use parent only when it looks like a real multi-service container:
                    # - parent has no dep file of its own, AND
                    # - parent has 2+ service-named children
                    if not parent_has_dep and len(siblings) >= 2:
                        final_path = parent
                except (PermissionError, OSError):
                    pass

        root = final_path

        if final_path != extracted_path:
            print(f"Found project root: {final_path}")

        return root
        
    except Exception as e:
        print(f"Error finding project root: {e}")
        return extracted_path


def detect_framework(project_path: str, use_ml: bool = True) -> Dict:
    """Hybrid detection: Heuristics first, ML as supplement"""
    _ensure_utf8_stdout()

    print(f"\nStarting Hybrid Framework Detection")
    print(f"Project Path: {project_path}")
    print(f"ML Mode: {'Enabled' if use_ml else 'Disabled'}\n")
    
    actual_path = find_project_root(project_path, max_depth=5)
    
    results: Dict = {
        "schema_version": PORT_SCHEMA_VERSION,
        "framework": "Unknown",
        "language": "Unknown",
        "runtime": "alpine:latest",
        "dependencies": [],
        "port": None,
        "build_command": None,
        "start_command": None,
        "env_variables": [],
        "dockerfile": False,
        "docker_compose": False,
        "detected_files": [],
        "detection_confidence": {
            "language": 0.0,
            "framework": 0.0,
            "method": "unknown"
        },
        "has_package_json": False,
        "has_requirements_txt": False,
        "has_manage_py": False,
        "static_only": False,
        # New fields for DB & multi-port
        "database": "Unknown",
        "databases": [],
        "database_detection": {},
        "database_port": None,
        # Runtime ports (service-internal app ports used by the app process)
        "backend_runtime_port": None,
        "frontend_runtime_port": None,
        "backend_runtime_port_source": None,
        "frontend_runtime_port_source": None,
        # Container ports (ports exposed inside containers)
        "backend_container_port": None,
        "frontend_container_port": None,
        "backend_container_port_source": None,
        "frontend_container_port_source": None,
        # Backward-compatible aliases (runtime semantics)
        "backend_port": None,
        "frontend_port": None,
        "backend_port_source": None,
        "frontend_port_source": None,
        # docker-aware ports (HOST ports)
        "docker_backend_ports": None,
        "docker_frontend_ports": None,
        "docker_database_ports": None,
        "docker_other_ports": None,
        # NEW: docker container ports
        "docker_backend_container_ports": None,
        "docker_frontend_container_ports": None,
        "docker_database_container_ports": None,
        "docker_other_container_ports": None,
        # Dockerfile EXPOSE ports (container ports)
        "docker_expose_ports": None,
    }
    
    try:
        print("Running heuristic detection (fast & reliable)...")
        heur_lang, heur_lang_conf = heuristic_language_detection(actual_path)
        heur_fw, heur_fw_conf = heuristic_framework_detection(actual_path, heur_lang)
        
        results["language"] = heur_lang
        results["framework"] = heur_fw
        results["detection_confidence"]["language"] = heur_lang_conf
        results["detection_confidence"]["framework"] = heur_fw_conf
        results["detection_confidence"]["method"] = "heuristic"
        
        # Optional ML supplement
        if use_ml and (heur_lang_conf < 0.5 or heur_fw_conf < 0.5):
            print("\nHeuristic confidence low, trying ML as supplement...")
            try:
                ml_analyzer = get_ml_analyzer()
                ml_results = ml_analyzer.analyze_project(actual_path)

                ml_lang_conf = ml_results.get("language_confidence", 0)
                ml_fw_conf = ml_results.get("framework_confidence", 0)

                if ml_lang_conf > (heur_lang_conf + 0.2):
                    results["language"] = ml_results["language"]
                    results["detection_confidence"]["language"] = ml_lang_conf
                    results["detection_confidence"]["method"] = "hybrid (ML language)"

                if ml_fw_conf > (heur_fw_conf + 0.2):
                    results["framework"] = ml_results["framework"]
                    results["detection_confidence"]["framework"] = ml_fw_conf
                    # if we changed framework via ML, mark method accordingly
                    if results["detection_confidence"]["method"] == "heuristic":
                        results["detection_confidence"]["method"] = "hybrid (ML framework)"
            except Exception as e:
                print(f"ML analysis failed, continuing with heuristic: {e}")
        
                # Sanity check: prevent impossible framework/language combos
        fw_lang = FRAMEWORK_LANGUAGES.get(results["framework"])
        if fw_lang and results["language"] == "Unknown":
            results["language"] = fw_lang
            results["detection_confidence"]["language"] = max(results["detection_confidence"]["language"], 0.5)

        if fw_lang and results["language"] != "Unknown" and not _languages_compatible(results["language"], fw_lang):
            print(
                f"Inconsistent detection: framework {results['framework']} "
                f"normally uses {fw_lang}, but language detected as {results['language']}. "
                f"Keeping framework and adjusting language."
            )
            results["language"] = fw_lang
            results["detection_confidence"]["language"] = max(results["detection_confidence"]["language"], 0.5)
            if results["detection_confidence"]["method"] == "heuristic":
                results["detection_confidence"]["method"] = "hybrid (framework->language)"


        # Runtime defaults (may include default port)
        runtime_info = get_runtime_info(results["language"], results["framework"], actual_path)
        results.update(runtime_info)

        # --- Smart command extraction from actual project files ---
        # Override generic defaults with actual commands from package.json or Python entry points
        if results["language"] in ("JavaScript", "TypeScript") or results["framework"] in ("Express.js", "Next.js", "React"):
            nodejs_cmds = extract_nodejs_commands(actual_path)
            if nodejs_cmds.get("start_command"):
                results["start_command"] = nodejs_cmds["start_command"]
                print(f"Overriding start_command with: {results['start_command']}")
            if nodejs_cmds.get("entry_point"):
                results["entry_point"] = nodejs_cmds["entry_point"]
            if nodejs_cmds.get("build_command"):
                results["build_command"] = nodejs_cmds["build_command"]
            if nodejs_cmds.get("build_output"):
                results["build_output"] = nodejs_cmds["build_output"]
                print(f"Detected build_output: {results['build_output']}")
        
        elif results["language"] == "Python":
            python_cmds = extract_python_commands(actual_path)
            if python_cmds.get("start_command"):
                results["start_command"] = python_cmds["start_command"]
                print(f"Overriding start_command with: {results['start_command']}")
        # --- Lightweight presence flags for key files ---
        has_package_json = False
        has_requirements_txt = False
        has_manage_py = False

        for root, dirs, files in os.walk(actual_path):
            dirs[:] = [
                d for d in dirs
                if d not in ['node_modules', '__pycache__', '.git', 'venv', '.venv', 'dist', 'build', '.next']
            ]

            if 'package.json' in files:
                has_package_json = True
            if 'requirements.txt' in files:
                has_requirements_txt = True
            if 'manage.py' in files:
                has_manage_py = True

            if has_package_json and has_requirements_txt and has_manage_py:
                break

        has_deno = (
            os.path.exists(os.path.join(actual_path, "deno.json")) or
            os.path.exists(os.path.join(actual_path, "deno.jsonc")) or
            os.path.exists(os.path.join(actual_path, "deps.ts"))
        )
        has_bun = (
            os.path.exists(os.path.join(actual_path, "bun.lockb")) or
            os.path.exists(os.path.join(actual_path, "bunfig.toml"))
        )

        # Deep signal: scan .ts files for runtime serve calls
        if not has_deno and not has_bun:
            try:
                _deno_bun_entries = os.listdir(actual_path)
            except OSError:
                _deno_bun_entries = []
            for fname in _deno_bun_entries:
                if fname.endswith(".ts") or fname.endswith(".js"):
                    try:
                        fpath = os.path.join(actual_path, fname)
                        with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                            snippet = f.read(2000)
                        if "Deno.serve(" in snippet or "Deno.listen(" in snippet:
                            has_deno = True
                            break
                        if "Bun.serve(" in snippet:
                            has_bun = True
                            break
                    except Exception:
                        pass

        results["has_package_json"] = has_package_json
        results["has_requirements_txt"] = has_requirements_txt
        results["has_manage_py"] = has_manage_py

        # Static-only JS/TS project: no package.json or Python server files
        results["static_only"] = (
            results["language"] in ("JavaScript", "TypeScript")
            and not has_package_json
            and not has_requirements_txt
            and not has_manage_py
            and not has_deno
            and not has_bun
        )

        if results["static_only"]:
            # Prefer a static server image; do NOT invent Node/Django commands
            print("Detected static-only JS/TS project (no package.json/manage.py/requirements.txt).")
            results["runtime"] = "nginx:alpine"
            results["port"] = 80
            results["build_command"] = None
            results["start_command"] = None

        if has_deno and not has_package_json:
            results["runtime"] = "denoland/deno:latest"
            _DENO_CANDIDATES = ["main.ts", "server.ts", "index.ts", "app.ts", "mod.ts"]
            deno_entry = next(
                (f for f in _DENO_CANDIDATES
                 if os.path.exists(os.path.join(actual_path, f))),
                "main.ts"  # fallback if none found
            )
            results["start_command"] = (
                f"deno run --allow-net --allow-env {deno_entry}"
            )

        if has_bun and not has_package_json:
            results["runtime"] = "oven/bun:latest"
            _BUN_CANDIDATES = ["index.ts", "server.ts", "main.ts", "app.ts", "index.js"]
            bun_entry = next(
                (f for f in _BUN_CANDIDATES
                 if os.path.exists(os.path.join(actual_path, f))),
                "index.ts"  # fallback if none found
            )
            results["start_command"] = f"bun run {bun_entry}"

        
        # Dependencies (root + nested subprojects like client/server)
        dep_files = {
            "requirements.txt": "requirements.txt",
            "pyproject.toml": "pyproject.toml",
            "Pipfile": "Pipfile",
            "poetry.lock": "poetry.lock",
            "package.json": "package.json",
            "pom.xml": "pom.xml",
            "go.mod": "go.mod",
            "composer.json": "composer.json",
            "Cargo.toml": "Cargo.toml",
        }
        
        visited_dep_files = set()
        detected_files_seen = set(results["detected_files"])
        
        # 1) Root-level dep files
        for filename, file_type in dep_files.items():
            file_path = os.path.join(actual_path, filename)
            if os.path.exists(file_path):
                deps = parse_dependencies_file(file_path, file_type)
                results["dependencies"].extend(deps)
                rel = os.path.relpath(file_path, actual_path).replace("\\", "/")
                if rel not in detected_files_seen:
                    results["detected_files"].append(rel)
                    detected_files_seen.add(rel)
                visited_dep_files.add(os.path.abspath(file_path))
        
        # 2) Nested dep files (e.g. mern/client/package.json, mern/server/package.json)
        for root, dirs, files in os.walk(actual_path):
            dirs[:] = [
                d for d in dirs
                if d not in ['node_modules', '__pycache__', '.git', 'venv', '.venv', 'dist', 'build', '.next']
            ]
            
            for file in files:
                if file in dep_files:
                    full_path = os.path.abspath(os.path.join(root, file))
                    if full_path in visited_dep_files:
                        continue
                    
                    file_type = dep_files[file]
                    deps = parse_dependencies_file(full_path, file_type)
                    results["dependencies"].extend(deps)
                    rel = os.path.relpath(full_path, actual_path).replace("\\", "/")
                    if rel not in detected_files_seen:
                        results["detected_files"].append(rel)
                        detected_files_seen.add(rel)
                    
                    visited_dep_files.add(full_path)
        
        # Docker files
        docker_info = detect_docker_files(actual_path)
        results["dockerfile"] = docker_info["dockerfile"]
        results["docker_compose"] = docker_info["docker_compose"]
        results["detected_files"].extend(docker_info["detected_files"])
        
        # Env vars
        env_vars = detect_env_variables(actual_path)
        if env_vars:
            results["env_variables"] = env_vars
        
        # DB + port detection (using final language/framework + deps/env + base port)
        db_info, ports_info = detect_db_and_ports(
            actual_path,
            results["language"],
            results["framework"],
            results["dependencies"],
            results.get("env_variables", []),
            base_port=results.get("port")
        )

        project_backend_port = ports_info.get("backend_port")
        project_frontend_port = ports_info.get("frontend_port")
        project_backend_port_source = ports_info.get("backend_port_source") or "unknown"
        project_frontend_port_source = ports_info.get("frontend_port_source") or "unknown"
        project_backend_compose_env_port = ports_info.get("backend_compose_env_port")
        project_frontend_compose_env_port = ports_info.get("frontend_compose_env_port")
        
        results["database"] = db_info.get("primary", "Unknown")
        results["databases"] = db_info.get("all", [])
        results["database_detection"] = db_info.get("details", {})
        
        # IMPORTANT: Only set database_port if database is LOCAL (needs container)
        # Cloud databases (is_cloud=True) should have NO port to avoid confusing LLM
        if results.get("database_is_cloud"):
            results["database_port"] = None  # Cloud DB - no container needed
            print("Database is cloud - clearing database_port")
        else:
            results["database_port"] = db_info.get("port")
        
        # Seed runtime ports only when project-level signal is explicit.
        # Service-level inference remains the primary source of truth.
        if project_backend_port is not None and project_backend_port_source in ("env", "source", "compose", "compose_env"):
            results["backend_runtime_port"] = project_backend_port
            results["backend_runtime_port_source"] = project_backend_port_source
            results["backend_port"] = project_backend_port
            results["backend_port_source"] = project_backend_port_source
            results["port"] = project_backend_port  # backwards compatibility
        
        if project_frontend_port is not None and project_frontend_port_source in ("env", "source", "compose", "compose_env"):
            results["frontend_runtime_port"] = project_frontend_port
            results["frontend_runtime_port_source"] = project_frontend_port_source
            results["frontend_port"] = project_frontend_port
            results["frontend_port_source"] = project_frontend_port_source
        
        if "docker_backend_ports" in ports_info:
            results["docker_backend_ports"] = ports_info.get("docker_backend_ports")
        if "docker_frontend_ports" in ports_info:
            results["docker_frontend_ports"] = ports_info.get("docker_frontend_ports")
        if "docker_database_ports" in ports_info:
            results["docker_database_ports"] = ports_info.get("docker_database_ports")
        if "docker_other_ports" in ports_info:
            results["docker_other_ports"] = ports_info.get("docker_other_ports")
        if "docker_backend_container_ports" in ports_info:
            results["docker_backend_container_ports"] = ports_info.get("docker_backend_container_ports")
        if "docker_frontend_container_ports" in ports_info:
            results["docker_frontend_container_ports"] = ports_info.get("docker_frontend_container_ports")
        if "docker_database_container_ports" in ports_info:
            results["docker_database_container_ports"] = ports_info.get("docker_database_container_ports")
        if "docker_other_container_ports" in ports_info:
            results["docker_other_container_ports"] = ports_info.get("docker_other_container_ports")
        if "docker_expose_ports" in ports_info:
            results["docker_expose_ports"] = ports_info.get("docker_expose_ports")

        # Service inference (uses file tree + metadata; compose only as hints)
        try:
            results["services"] = infer_services(
                actual_path,
                results.get("language", "Unknown"),
                results.get("framework", "Unknown"),
                results,
                db_result=db_info,
            )
        except Exception:
            results["services"] = []

        # Ensure cloud/local DB metadata is present even if service inference fails.
        if results.get("database") != "Unknown" and "database_is_cloud" not in results:
            try:
                db_runtime_info = extract_database_info(actual_path, results.get("database"))
                if db_runtime_info.get("db_type"):
                    results["database_is_cloud"] = db_runtime_info.get("is_cloud", False)
                    if db_runtime_info.get("env_var_name"):
                        results["database_env_var"] = db_runtime_info["env_var_name"]
                    if results["database_is_cloud"]:
                        results["database_port"] = None
                    elif results.get("database_port") is None and db_runtime_info.get("default_port") is not None:
                        results["database_port"] = db_runtime_info["default_port"]
            except Exception:
                pass

        def _get_backend_service() -> Optional[Dict]:
            return next(
                (s for s in results.get("services", []) if s.get("type") in ("backend", "monolith")),
                None,
            )

        def _service_abs_path(service: Dict) -> str:
            svc_rel = str(service.get("path", ".") or ".")
            svc_rel_clean = svc_rel.strip("/\\")
            if svc_rel_clean in ("", "."):
                return actual_path
            return os.path.join(actual_path, svc_rel_clean.replace("/", os.sep))

        def _finalize_runtime_and_commands() -> Optional[Dict]:
            backend_service = _get_backend_service()
            if backend_service:
                svc_abs = _service_abs_path(backend_service)
                svc_lang = backend_service.get("language")
                if not svc_lang:
                    if os.path.exists(os.path.join(svc_abs, "package.json")):
                        svc_lang = "JavaScript"
                    elif any(
                        os.path.exists(os.path.join(svc_abs, marker))
                        for marker in ("requirements.txt", "pyproject.toml", "Pipfile", "manage.py")
                    ):
                        svc_lang = "Python"
                    else:
                        svc_lang = results.get("language")
                svc_fw = backend_service.get("framework") or results.get("framework")
                svc_runtime_info = get_runtime_info(svc_lang, svc_fw, svc_abs)
                if svc_runtime_info.get("runtime"):
                    results["runtime"] = svc_runtime_info["runtime"]

                if svc_lang == "Python":
                    svc_cmds = extract_python_commands(svc_abs)
                else:
                    svc_cmds = extract_nodejs_commands(svc_abs)

                final_start = backend_service.get("start_command") or svc_cmds.get("start_command")
                final_entry = backend_service.get("entry_point") or svc_cmds.get("entry_point")
                results["start_command"] = final_start if final_start else None
                results["entry_point"] = final_entry if final_entry else None
                if svc_cmds.get("build_command"):
                    results["build_command"] = svc_cmds["build_command"]
                if svc_cmds.get("build_output"):
                    results["build_output"] = svc_cmds["build_output"]
                return backend_service

            if results.get("static_only"):
                results["runtime"] = "nginx:alpine"
                results["port"] = 80
                results["build_command"] = None
                results["start_command"] = None
                return None

            if results.get("runtime") in ("denoland/deno:latest", "oven/bun:latest"):
                return None

            refreshed_runtime = get_runtime_info(
                results.get("language", "Unknown"),
                results.get("framework", "Unknown"),
                actual_path,
            )
            if refreshed_runtime.get("runtime"):
                results["runtime"] = refreshed_runtime["runtime"]

            if (
                results.get("language") in ("JavaScript", "TypeScript")
                or results.get("framework") in ("Express.js", "Next.js", "React")
            ):
                nodejs_cmds = extract_nodejs_commands(actual_path)
                if nodejs_cmds.get("start_command"):
                    results["start_command"] = nodejs_cmds["start_command"]
                if nodejs_cmds.get("entry_point"):
                    results["entry_point"] = nodejs_cmds["entry_point"]
                if nodejs_cmds.get("build_command"):
                    results["build_command"] = nodejs_cmds["build_command"]
                if nodejs_cmds.get("build_output"):
                    results["build_output"] = nodejs_cmds["build_output"]
            elif results.get("language") == "Python":
                python_cmds = extract_python_commands(actual_path)
                if python_cmds.get("start_command"):
                    results["start_command"] = python_cmds["start_command"]

            return None

        backend_service_for_runtime = _finalize_runtime_and_commands()

        # Propagate resolved framework into the selected Node backend/monolith service.
        resolved_framework = results.get("framework")
        if (
            resolved_framework
            and resolved_framework != "Unknown"
            and backend_service_for_runtime
            and backend_service_for_runtime.get("type") in ("backend", "monolith")
            and backend_service_for_runtime.get("language") != "Python"
        ):
            current_framework = backend_service_for_runtime.get("framework")
            if not current_framework or current_framework == "Unknown":
                backend_service_for_runtime["framework"] = resolved_framework
        
        # =======================================================================
        # PORT CONSOLIDATION (single precedence engine)
        # =======================================================================
        def _normalize_source(source: Optional[str]) -> str:
            s = str(source or "unknown").lower()
            if s.startswith("service_"):
                s = s[len("service_"):]
            if s.startswith("project_"):
                s = s[len("project_"):]
            return s

        def _runtime_rank(source: Optional[str]) -> int:
            s = _normalize_source(source)
            ranks = {
                "env": 100,
                "compose_env": 98,
                "compose": 95,
                "source": 90,
                "package": 70,
                "pkg_json": 70,
                "vite_default": 30,
                "cra_default": 30,
                "next_default": 30,
                "vue_default": 30,
                "angular_default": 30,
                "default": 10,
                "unknown": 0,
            }
            return ranks.get(s, 20)

        def _container_rank(source: Optional[str]) -> int:
            s = _normalize_source(source)
            ranks = {
                "compose": 100,
                "docker_compose": 100,
                "compose_expose": 95,
                "dockerfile_expose": 90,
                "service": 60,
                "dev_server": 30,
                "ssr_default": 26,
                "next_default": 25,
                "nginx_default": 20,
                "frontend_default": 20,
                "runtime_fallback": 15,
                "default": 10,
                "unknown": 0,
            }
            return ranks.get(s, 20)

        def _set_runtime(side: str, port: Optional[int], source: str) -> None:
            if port is None:
                return
            if side == "backend":
                port_key = "backend_runtime_port"
                source_key = "backend_runtime_port_source"
                alias_key = "backend_port"
                alias_source_key = "backend_port_source"
            else:
                port_key = "frontend_runtime_port"
                source_key = "frontend_runtime_port_source"
                alias_key = "frontend_port"
                alias_source_key = "frontend_port_source"

            current_port = results.get(port_key)
            current_source = results.get(source_key, "unknown")
            if current_port is None or _runtime_rank(source) > _runtime_rank(current_source):
                results[port_key] = port
                results[source_key] = source
                results[alias_key] = port
                results[alias_source_key] = source

        def _set_container(side: str, port: Optional[int], source: str) -> None:
            if port is None:
                return
            if side == "backend":
                port_key = "backend_container_port"
                source_key = "backend_container_port_source"
            else:
                port_key = "frontend_container_port"
                source_key = "frontend_container_port_source"

            current_port = results.get(port_key)
            current_source = results.get(source_key, "unknown")
            if current_port is None or _container_rank(source) > _container_rank(current_source):
                results[port_key] = port
                results[source_key] = source

        def _service_depth(path_value: object) -> int:
            path = str(path_value or ".").replace("\\", "/").strip("/")
            return 0 if not path or path == "." else len([p for p in path.split("/") if p])

        service_list = results.get("services", [])
        service_list = sorted(
            service_list,
            key=lambda s: (
                0 if str(s.get("type", "")).lower() in ("backend", "monolith") else (1 if str(s.get("type", "")).lower() == "frontend" else 2),
                -_service_depth(s.get("path")),
                str(s.get("path", ".")).replace("\\", "/").lower(),
                str(s.get("name", "")).lower(),
            ),
        )
        has_backend_like_service = any(
            s.get("type") in ("backend", "monolith", "worker") for s in service_list
        )
        has_frontend_service = any(s.get("type") == "frontend" for s in service_list)

        # Project-level candidates are baseline; service-level candidates (below) can override
        # when they have stronger evidence.
        if project_backend_compose_env_port is not None:
            _set_runtime("backend", project_backend_compose_env_port, "project_compose_env")
        if project_frontend_compose_env_port is not None:
            _set_runtime("frontend", project_frontend_compose_env_port, "project_compose_env")

        if project_backend_port is not None and (has_backend_like_service or not service_list):
            _set_runtime("backend", project_backend_port, f"project_{project_backend_port_source}")
        if project_frontend_port is not None:
            _set_runtime("frontend", project_frontend_port, f"project_{project_frontend_port_source}")

        for svc in service_list:
            svc_type = svc.get("type")
            svc_runtime_port = (
                svc.get("runtime_port")
                if svc.get("runtime_port") is not None
                else (svc.get("dev_port") if svc.get("dev_port") is not None else svc.get("port"))
            )
            svc_container_port = svc.get("container_port")
            svc_port_source = str(svc.get("port_source", "unknown"))
            svc_container_port_source = str(svc.get("container_port_source", "service"))

            if svc_type in ("backend", "monolith"):
                _set_runtime("backend", svc_runtime_port, f"service_{svc_port_source}")
            if svc_type in ("backend", "monolith", "worker"):
                if svc_container_port is None:
                    svc_container_port = svc_runtime_port
                _set_container("backend", svc_container_port, f"service_{svc_container_port_source}")

            if svc_type == "frontend":
                _set_runtime("frontend", svc_runtime_port, f"service_{svc_port_source}")
                _set_container("frontend", svc_container_port, f"service_{svc_container_port_source}")

        if results.get("backend_container_port") is None:
            backend_container_ports = results.get("docker_backend_container_ports") or []
            if backend_container_ports:
                _set_container("backend", backend_container_ports[0], "docker_compose")
            elif results.get("backend_runtime_port") is not None:
                _set_container("backend", results["backend_runtime_port"], "runtime_fallback")

        if results.get("frontend_container_port") is None:
            frontend_container_ports = results.get("docker_frontend_container_ports") or []
            if frontend_container_ports:
                _set_container("frontend", frontend_container_ports[0], "docker_compose")
            elif has_frontend_service:
                _set_container("frontend", 80, "frontend_default")

        if results.get("backend_runtime_port") is not None:
            results["backend_port"] = results["backend_runtime_port"]
            results["port"] = results["backend_runtime_port"]  # legacy alias
        if results.get("frontend_runtime_port") is not None:
            results["frontend_port"] = results["frontend_runtime_port"]
        
        # =======================================================================
        # CLOUD DATABASE: Clear database_port if database is cloud
        # database_is_cloud is set by infer_services -> extract_database_info()
        # This ensures LLM doesn't add a database container for cloud DBs
        # =======================================================================
        if results.get("database_is_cloud"):
            results["database_port"] = None
            print("Cloud database detected - clearing database_port (no container needed)")

        # =======================================================================
        # CONSISTENCY RECONCILIATION PASS
        # Enforce final coherence between language/framework/services/database
        # =======================================================================
        pre_consistency_language = results.get("language")
        pre_consistency_framework = results.get("framework")
        consistency_warnings: List[str] = []

        if (
            results.get("framework") in ("Express.js", "Fastify")
            and results.get("language") not in ("JavaScript", "TypeScript")
        ):
            results["language"] = "JavaScript"
            consistency_warnings.append(
                "Framework is Express/Fastify; language normalized to JavaScript."
            )

        js_frameworks = {
            "Express.js", "Fastify", "NestJS", "Next.js", "React", "Vue", "Angular", "Vite",
            "Gatsby", "Remix",
        }
        if results.get("language") == "Python" and results.get("framework") in js_frameworks:
            results["framework"] = "Unknown"
            consistency_warnings.append(
                "Python language with JS framework is inconsistent; framework reset to Unknown."
            )

        services = results.get("services", [])
        backend_runtime_port = results.get("backend_runtime_port")
        frontend_runtime_port = results.get("frontend_runtime_port")
        has_backend_service_for_collision = any(
            s.get("type") in ("backend", "monolith", "worker") for s in services
        )
        has_frontend_service_for_collision = any(
            s.get("type") == "frontend" for s in services
        )

        if (
            has_backend_service_for_collision
            and has_frontend_service_for_collision
            and backend_runtime_port is not None
            and frontend_runtime_port is not None
            and backend_runtime_port == frontend_runtime_port
        ):
            consistency_warnings.append(
                f"Backend/frontend runtime ports collide at {backend_runtime_port}; review service port hints before compose generation."
            )

        if (
            results.get("language") != pre_consistency_language
            or results.get("framework") != pre_consistency_framework
        ):
            backend_service_for_runtime = _finalize_runtime_and_commands()

        frontend_only = bool(services) and all(s.get("type") == "frontend" for s in services)
        results["missing_backend"] = frontend_only
        if frontend_only:
            consistency_warnings.append(
                "Only frontend services detected; backend service is missing."
            )

        has_backend_service = any(
            s.get("type") in ("backend", "monolith") for s in services
        )
        if results.get("database") != "Unknown" and not has_backend_service:
            warn = (
                "Database detected without backend/monolith service; keeping database metadata unchanged."
            )
            print(f"Warning: {warn}")
            consistency_warnings.append(warn)

        if consistency_warnings:
            existing = results.get("consistency_warnings")
            if isinstance(existing, list):
                existing.extend(consistency_warnings)
            else:
                results["consistency_warnings"] = consistency_warnings
        
        # =======================================================================
        # ENV FILE CHECK: Note missing .env for auto-generation during build
        # We no longer BLOCK deployment — instead the build pipeline auto-generates
        # a .env template using Gemini, so user has zero manual work.
        # =======================================================================
        backend_services = [s for s in results.get("services", []) if s.get("type") in ("backend", "monolith")]
        backend_missing_env = any(
            svc.get("type") in ("backend", "monolith") and not svc.get("env_file")
            for svc in results.get("services", [])
        )

        if backend_services and backend_missing_env:
            results["deploy_blocked"] = False
            results["deploy_blocked_reason"] = None
            results["backend_env_missing"] = True
            if results.get("database") != "Unknown":
                results["deploy_warning"] = (
                    "No .env file detected. One will be auto-generated before build."
                )
                print("Deploy info: Backend .env missing (database detected) — will auto-generate")
            else:
                results["deploy_warning"] = (
                    "No .env detected. Auto-generating template — review and fill in secrets if needed."
                )
                print("Deploy info: Backend .env missing (no database) — will auto-generate template")
        else:
            results["deploy_blocked"] = False
            results["deploy_blocked_reason"] = None
            results["backend_env_missing"] = False
            results["deploy_warning"] = None
        
        # Deduplicate detected_files
        results["detected_files"] = sorted(set(results["detected_files"]))

        # Safe debug print
        try:
            print("Here are all the results:\n" + json.dumps(results, indent=2, default=str))
        except Exception:
            print("Here are all the results (non-serializable parts skipped)")
        
        print(f"\nDetection Complete!")
        print(f"   Language: {results['language']} (confidence: {results['detection_confidence']['language']:.2f})")
        print(f"   Framework: {results['framework']} (confidence: {results['detection_confidence']['framework']:.2f})")
        print(f"   Method: {results['detection_confidence']['method']}\n")
        
        return results
        
    except Exception as e:
        print(f"Detection error: {e}")
        import traceback
        traceback.print_exc()
        return results
