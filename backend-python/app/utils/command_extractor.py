"""
Node.js command extraction utilities for Docker deployment.
Parses package.json to find actual start commands and entry points.
Includes enhanced detection for custom build output directories.
"""
import os
import json
import re
import shlex
import ipaddress
import urllib.parse
from typing import Dict, Optional

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None


def _parse_vite_config(project_path: str) -> Optional[str]:
    """
    Parse vite.config.js/ts for custom outDir.
    Example: build: { outDir: 'public' }
    """
    config_files = ["vite.config.js", "vite.config.ts", "vite.config.mjs"]
    
    for config_file in config_files:
        config_path = os.path.join(project_path, config_file)
        if os.path.exists(config_path):
            try:
                with open(config_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                
                # Pattern: outDir: 'custom_dir' or outDir: "custom_dir"
                patterns = [
                    r"outDir\s*:\s*['\"]([^'\"]+)['\"]",
                    r"outDir\s*:\s*`([^`]+)`",
                ]
                
                for pattern in patterns:
                    match = re.search(pattern, content)
                    if match:
                        out_dir = match.group(1)
                        print(f"📦 Found custom outDir in {config_file}: {out_dir}")
                        return out_dir
                        
            except Exception as e:
                print(f"Error parsing {config_file}: {e}")
    
    return None


def _parse_vue_config(project_path: str) -> Optional[str]:
    """
    Parse vue.config.js for custom outputDir.
    Example: outputDir: 'public'
    """
    config_path = os.path.join(project_path, "vue.config.js")
    
    if os.path.exists(config_path):
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # Pattern: outputDir: 'custom_dir'
            patterns = [
                r"outputDir\s*:\s*['\"]([^'\"]+)['\"]",
            ]
            
            for pattern in patterns:
                match = re.search(pattern, content)
                if match:
                    out_dir = match.group(1)
                    print(f"📦 Found custom outputDir in vue.config.js: {out_dir}")
                    return out_dir
                    
        except Exception as e:
            print(f"Error parsing vue.config.js: {e}")
    
    return None


def _parse_webpack_config(project_path: str) -> Optional[str]:
    """
    Parse webpack.config.js for output.path.
    Example: output: { path: path.resolve(__dirname, 'public') }
    """
    # Extended config paths including nested directories (Fix 4)
    config_files = [
        "webpack.config.js", "webpack.config.ts", "webpack.prod.js",
        # Nested config paths (Fix 4)
        "configs/webpack.config.js", "configs/webpack.prod.js",
        "config/webpack.config.js", "config/webpack.prod.js",
        "build/webpack.config.js", "build/webpack.prod.js",
    ]
    
    for config_file in config_files:
        config_path = os.path.join(project_path, config_file)
        if os.path.exists(config_path):
            try:
                with open(config_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                
                # Patterns for webpack output path
                patterns = [
                    # path.resolve(__dirname, 'dist')
                    r"path\.resolve\s*\([^,]+,\s*['\"]([^'\"]+)['\"]\)",
                    # path.join(__dirname, 'dist')
                    r"path\.join\s*\([^,]+,\s*['\"]([^'\"]+)['\"]\)",
                    # path: 'dist'
                    r"path\s*:\s*['\"]([^'\"]+)['\"]",
                ]
                
                for pattern in patterns:
                    match = re.search(pattern, content)
                    if match:
                        out_dir = match.group(1)
                        # Clean up the path
                        out_dir = out_dir.strip('./')
                        if out_dir and out_dir not in ['__dirname', '']:
                            print(f"📦 Found custom output.path in {config_file}: {out_dir}")
                            return out_dir
                            
            except Exception as e:
                print(f"Error parsing {config_file}: {e}")
    
    return None


def _parse_gitignore_for_build_dir(project_path: str) -> Optional[str]:
    """
    Check .gitignore for common build output patterns.
    Many projects gitignore their build output directory.
    """
    gitignore_path = os.path.join(project_path, ".gitignore")
    
    if os.path.exists(gitignore_path):
        try:
            with open(gitignore_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            
            # Common build output patterns (order matters - more specific first)
            build_patterns = [
                ("build/", "build"),
                ("build", "build"),
                ("/build", "build"),
                ("dist/", "dist"),
                ("dist", "dist"),
                ("/dist", "dist"),
                ("out/", "out"),
                ("/out", "out"),
                ("public/", "public"),
                (".next/", ".next"),
                (".next", ".next"),
            ]
            
            for line in lines:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                    
                for pattern, output in build_patterns:
                    if line == pattern or line.startswith(pattern):
                        print(f"📦 Found build output in .gitignore: {output}")
                        return output
                        
        except Exception as e:
            print(f"Error parsing .gitignore: {e}")
    
    return None


def extract_nodejs_commands(project_path: str) -> Dict[str, Optional[str]]:
    """
    Extract actual start commands from package.json scripts.
    Returns dict with:
      - start_command: The actual start command (e.g., "node app.js", "npm start")
      - entry_point: The main file (e.g., "app.js", "server.js", "index.js")
      - build_command: Build script if exists
      - build_output: Build output directory (e.g., "dist", "build", ".next")
    """
    result = {
        "start_command": None,
        "entry_point": None,
        "build_command": None,
        "build_output": None,  # NEW: Track build output directory
        "has_start_script": False,
    }
    
    # Find package.json (search up to 2 levels deep for monorepos)
    pkg_paths = [os.path.join(project_path, "package.json")]
    try:
        backend_pref = {"backend", "server", "api", "services", "service"}
        frontend_pref = {"frontend", "client", "web", "ui"}

        def _pkg_candidate_order(name: str):
            lname = name.lower()
            if lname in backend_pref or any(tok in lname for tok in ("backend", "server", "api")):
                return (0, lname)
            if lname in frontend_pref or any(tok in lname for tok in ("frontend", "client")):
                return (2, lname)
            return (1, lname)

        for name in sorted(os.listdir(project_path), key=_pkg_candidate_order):
            sub = os.path.join(project_path, name)
            if not os.path.isdir(sub):
                continue
            if name in {"node_modules", ".git", "__pycache__", "dist",
                        "build", ".next", "coverage", "test", "tests"}:
                continue
            candidate = os.path.join(sub, "package.json")
            if os.path.exists(candidate):
                pkg_paths.append(candidate)
    except OSError:
        pass
    
    pkg_json_path = None
    for p in pkg_paths:
        if os.path.exists(p):
            pkg_json_path = p
            break
    
    if not pkg_json_path:
        return result

    pkg_dir = os.path.dirname(pkg_json_path)

    try:
        with open(pkg_json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        scripts = data.get("scripts", {})
        main_entry = data.get("main", None)

        def _normalize_entry_candidate(raw: str) -> Optional[str]:
            token = (raw or "").strip().strip('"').strip("'").strip("`").rstrip(";,")
            if not token:
                return None
            if token in {"&&", "||", ";", "|"}:
                return None
            if "=" in token or token.startswith("-"):
                return None

            token_norm = token.replace("\\", "/")
            lowered = token_norm.lower()
            if lowered in {
                "node", "nodemon", "npx", "ts-node", "tsx",
                "npm", "yarn", "pnpm", "bun", "pm2",
                "run", "start", "dev", "serve", "preview",
            }:
                return None

            path_parts = [p for p in token_norm.split("/") if p]
            if "node_modules" in path_parts:
                return None

            fs_candidate = token_norm
            if not os.path.isabs(fs_candidate):
                fs_candidate = os.path.join(pkg_dir, token_norm)

            _, ext = os.path.splitext(token_norm)
            allowed_exts = {".js", ".ts", ".mjs", ".cjs", ".jsx", ".tsx"}
            if ext:
                if ext.lower() in allowed_exts:
                    if os.path.isfile(fs_candidate):
                        return token_norm
                    # Allow common build-output paths even before artifacts exist
                    # (e.g. "node dist/main.js" in an unbuilt TypeScript project is
                    # still the correct entry point once `npm run build` runs).
                    return token_norm
                return None

            for suffix in (".js", ".ts", ".mjs", ".cjs"):
                if os.path.isfile(fs_candidate + suffix):
                    return f"{token_norm}{suffix}"

            if os.path.isdir(fs_candidate):
                for entry_name in (
                    "index.js", "server.js", "app.js", "main.js",
                    "index.ts", "server.ts", "app.ts", "main.ts",
                ):
                    if os.path.isfile(os.path.join(fs_candidate, entry_name)):
                        return f"{token_norm.rstrip('/')}/{entry_name}"

            return None

        # Helper function to extract entry point from complex scripts (Fix 2)
        def extract_entry_from_script(script: str) -> Optional[str]:
            """
            Extract the actual .js/.ts file from complex scripts like:
            'NODE_ENV=production node --max-old-space-size=4096 server/index.js'
            Skips: env vars (containing '='), flags (starting with '-')
            """
            try:
                parts = shlex.split(script, posix=True)
            except Exception:
                parts = script.split()
            for part in parts:
                candidate = _normalize_entry_candidate(part)
                if candidate:
                    return candidate
            return None

        def extract_entry_from_script_chain(
            script: str,
            depth: int = 0,
            seen: Optional[set[str]] = None,
        ) -> Optional[str]:
            """
            Resolve entry file from a script, including simple nested script calls
            like `npm run start:prod` -> scripts['start:prod'].
            """
            entry = extract_entry_from_script(script)
            if entry:
                return entry

            if depth >= 3:
                return None

            if seen is None:
                seen = set()

            script = (script or "").strip()
            if not script:
                return None

            nested_script = None
            m = re.match(r"^(?:npm|pnpm)\s+run\s+([A-Za-z0-9:_-]+)\b", script)
            if m:
                nested_script = m.group(1)
            else:
                m = re.match(r"^yarn\s+run\s+([A-Za-z0-9:_-]+)\b", script)
                if m:
                    nested_script = m.group(1)

            if not nested_script or nested_script in seen:
                return None
            seen.add(nested_script)

            nested_value = scripts.get(nested_script)
            if isinstance(nested_value, str):
                return extract_entry_from_script_chain(nested_value, depth + 1, seen)
            return None
        
        # Check for start script
        if "start" in scripts:
            result["has_start_script"] = True
            start_script = scripts["start"]
            
            # Parse the start script to extract the actual command
            # Common patterns: "node app.js", "nodemon server.js", "npm run serve"
            if start_script.startswith("node "):
                # Extract the file using smart parsing (Fix 2)
                entry = extract_entry_from_script_chain(start_script)
                if entry:
                    result["entry_point"] = entry
                    result["start_command"] = f"node {entry}"
                else:
                    # Fallback to simple parsing
                    parts = start_script.split()
                    for part in parts[1:]:
                        candidate = _normalize_entry_candidate(part)
                        if candidate:
                            result["entry_point"] = candidate
                            result["start_command"] = f"node {candidate}"
                            break
                    if not result["start_command"]:
                        result["start_command"] = start_script
            elif start_script.startswith("nodemon "):
                # Convert nodemon to node for production
                entry = extract_entry_from_script_chain(start_script)
                if entry:
                    result["entry_point"] = entry
                    result["start_command"] = f"node {entry}"
                else:
                    parts = start_script.split()
                    for part in parts[1:]:
                        candidate = _normalize_entry_candidate(part)
                        if candidate:
                            result["entry_point"] = candidate
                            result["start_command"] = f"node {candidate}"
                            break
                    if not result["start_command"]:
                        result["start_command"] = "npm start"
            elif start_script.startswith("ts-node ") or "tsx " in start_script:
                # TypeScript - needs build first
                entry = extract_entry_from_script_chain(start_script)
                if entry:
                    result["entry_point"] = entry
                result["start_command"] = "npm start"
            elif start_script.startswith("pm2 "):
                # PM2 process manager - don't use ecosystem.config.js as entry
                # Try to get actual entry from dev script instead
                result["start_command"] = "npm start"
                dev_script = scripts.get("dev", "")
                if dev_script:
                    entry = extract_entry_from_script_chain(dev_script)
                    if entry:
                        result["entry_point"] = entry
                        result["start_command"] = f"node {entry}"
                        print(f"📦 PM2 detected, using entry from dev script: {entry}")
            else:
                # Try to infer entry from script content using smart parsing (Fix 2)
                entry = extract_entry_from_script_chain(start_script)
                if entry:
                    result["entry_point"] = entry
                    result["start_command"] = f"node {entry}"
                else:
                    # Use npm start for complex scripts
                    result["start_command"] = "npm start"

                    # Fallback to simple pattern matching
                    for pattern in ["node ", "nodemon "]:
                        if pattern in start_script:
                            idx = start_script.find(pattern) + len(pattern)
                            for part in start_script[idx:].split():
                                candidate = _normalize_entry_candidate(part)
                                if candidate:
                                    result["entry_point"] = candidate
                                    break
                            if result["entry_point"]:
                                break

        # Fallback script-chain resolution when "start" script is absent.
        if not result["has_start_script"]:
            for script_key in ("serve", "start:prod", "start-prod", "prod"):
                script_value = scripts.get(script_key)
                if not isinstance(script_value, str) or not script_value.strip():
                    continue

                entry = extract_entry_from_script_chain(script_value)
                if entry and not result["entry_point"]:
                    result["entry_point"] = entry
                if not result["start_command"]:
                    result["start_command"] = f"npm run {script_key}"

                if result["entry_point"] and result["start_command"]:
                    break

            # Last-resort: use dev script only to infer entry, not as deployment command.
            if not result["entry_point"]:
                dev_script = scripts.get("dev")
                if isinstance(dev_script, str) and dev_script.strip():
                    dev_entry = extract_entry_from_script_chain(dev_script)
                    if dev_entry:
                        result["entry_point"] = dev_entry
                        if not result["start_command"]:
                            result["start_command"] = f"node {dev_entry}"

        # Fallback: check "main" field
        if not result["entry_point"] and main_entry:
            main_entry = str(main_entry).strip()
            if main_entry:
                main_path = os.path.join(pkg_dir, main_entry)
                _, main_ext = os.path.splitext(main_entry)
                allowed_main_exts = {".js", ".ts", ".mjs", ".cjs"}
                if os.path.isfile(main_path):
                    result["entry_point"] = main_entry
                    if not result["start_command"]:
                        result["start_command"] = f"node {main_entry}"
                elif not main_ext:
                    for suffix in allowed_main_exts:
                        if os.path.isfile(main_path + suffix):
                            resolved_main = f"{main_entry}{suffix}"
                            result["entry_point"] = resolved_main
                            if not result["start_command"]:
                                result["start_command"] = f"node {resolved_main}"
                            break
        
        # Fallback: detect common entry files
        if not result["entry_point"]:
            # In command_extractor.py, extend common_entries at line ~308:
            common_entries = [
                # Root level - most common Express entry names (server.js first, app.js last)
                "server.js",
                "index.js",
                "main.js",
                "app.js",
                # src/ subdirectory
                "src/server.js",
                "src/index.js",
                "src/app.js",
                "src/main.js",
                # server/ subdirectory (e.g. docker-node-pg uses server/app.js)
                "server/app.js",
                "server/index.js",
                "server/server.js",
                # js/ subdirectory (e.g. express-react-ts-auth uses js/index.js)
                "js/index.js",
                "js/app.js",
                # bin/ subdirectory - express-generator convention
                "bin/www.js",
                "bin/www",
                # TypeScript equivalents
                "src/server.ts",
                "src/index.ts",
                "src/app.ts",
                "server/index.ts",
                "server/app.ts",
            ]
            for entry in common_entries:
                if os.path.exists(os.path.join(pkg_dir, entry)):
                    result["entry_point"] = entry
                    if not result["start_command"]:
                        result["start_command"] = f"node {entry}"
                    break
        
        # Check for build script and determine build output directory
        if "build" in scripts:
            result["build_command"] = "npm run build"
            
            # Detect build output based on dependencies and framework
            deps = data.get("dependencies", {})
            dev_deps = data.get("devDependencies", {})
            all_deps = {**deps, **dev_deps}
            
            build_script_lower = str(scripts.get("build", "")).lower()
            has_vite = "vite" in all_deps
            has_cra = "react-scripts" in all_deps

            # CRA/Vite disambiguation: when both deps exist, prefer explicit build script.
            if has_vite and has_cra:
                if "vite" in build_script_lower:
                    result["build_output"] = "dist"
                    print("📦 Mixed CRA+Vite deps; build script indicates Vite -> build output: dist/")
                elif "react-scripts" in build_script_lower:
                    result["build_output"] = "build"
                    print("📦 Mixed CRA+Vite deps; build script indicates CRA -> build output: build/")
                else:
                    result["build_output"] = "dist"
                    print("📦 Mixed CRA+Vite deps; defaulting to Vite-style output: dist/")

            # Vite -> outputs to "dist/"
            elif has_vite:
                result["build_output"] = "dist"
                print("📦 Detected Vite -> build output: dist/")

            # Create React App (CRA) -> outputs to "build/"
            elif has_cra:
                result["build_output"] = "build"
                print("📦 Detected Create React App (CRA) -> build output: build/")
            
            # Next.js -> outputs to ".next/" or "out/" for static export
            elif "next" in all_deps:
                build_script = scripts.get("build", "")
                if "next export" in build_script or "export" in scripts:
                    result["build_output"] = "out"
                else:
                    result["build_output"] = ".next"
                print(f"📦 Detected Next.js -> build output: {result['build_output']}/")
            
            # Vue CLI -> outputs to "dist/"
            elif "@vue/cli-service" in all_deps:
                result["build_output"] = "dist"
                print("📦 Detected Vue CLI -> build output: dist/")
            
            # Angular -> outputs to "dist/<project-name>/"
            elif "@angular/cli" in all_deps or "@angular/core" in all_deps:
                result["build_output"] = "dist"
                print("📦 Detected Angular -> build output: dist/")
            
            # Remix -> outputs to "build/" (Fix 3)
            elif "@remix-run/react" in all_deps or "@remix-run/node" in all_deps:
                result["build_output"] = "build"
                print("📦 Detected Remix -> build output: build/")
            
            # Gatsby -> outputs to "public/" (Fix 3)
            elif "gatsby" in all_deps:
                result["build_output"] = "public"
                print("📦 Detected Gatsby -> build output: public/")
            
            # Default to "dist" for unknown build tools
            else:
                result["build_output"] = "dist"
                print("📦 Unknown build tool -> defaulting to: dist/")
            
            # --- Enhanced detection: Check config files for custom outDir ---
            # These can override the dependency-based defaults
            pkg_dir = os.path.dirname(pkg_json_path)
            
            # Check vite.config.js for custom outDir
            vite_out = _parse_vite_config(pkg_dir)
            if vite_out:
                result["build_output"] = vite_out
                print(f"📦 Config override: vite.config -> {vite_out}")
            
            # Check vue.config.js for custom outputDir
            vue_out = _parse_vue_config(pkg_dir)
            if vue_out:
                result["build_output"] = vue_out
                print(f"📦 Config override: vue.config -> {vue_out}")
            
            # Check webpack.config.js for custom output.path
            webpack_out = _parse_webpack_config(pkg_dir)
            if webpack_out:
                result["build_output"] = webpack_out
                print(f"📦 Config override: webpack.config -> {webpack_out}")
            
            # Last resort: check .gitignore for build output patterns
            # Only if we're still using a default value
            if result["build_output"] in ["dist", "build"] and not any([vite_out, vue_out, webpack_out]):
                gitignore_out = _parse_gitignore_for_build_dir(pkg_dir)
                if gitignore_out and gitignore_out != result["build_output"]:
                    # Only override if .gitignore suggests something different
                    print(f"📦 .gitignore suggests different output: {gitignore_out} (current: {result['build_output']})")
                    # Don't auto-override, just log for awareness
        
        print(f"📦 package.json analysis: entry={result['entry_point']}, start={result['start_command']}, build_output={result['build_output']}")
        
    except Exception as e:
        print(f"Error parsing package.json: {e}")
    
    return result


def extract_python_commands(project_path: str) -> Dict[str, Optional[str]]:
    """
    Extract actual start commands for Python projects.
    Looks at manage.py, pyproject.toml, etc.
    """
    result = {
        "start_command": None,
        "entry_point": None,
    }
    
    # Check for Django manage.py
    if os.path.exists(os.path.join(project_path, "manage.py")):
        result["entry_point"] = "manage.py"
        result["start_command"] = "python manage.py runserver 0.0.0.0:8000"
        return result

    # Prefer explicit start commands when present (avoid hardcoding uvicorn <module>:app).
    def _find_explicit_start_command() -> Optional[str]:
        def _looks_like_python_server_cmd(cmd: str) -> bool:
            c = (cmd or "").lower()
            return any(token in c for token in ("uvicorn", "gunicorn", "hypercorn", "daphne"))

        # Procfile (e.g., Heroku)
        procfile_path = os.path.join(project_path, "Procfile")
        if os.path.exists(procfile_path):
            try:
                with open(procfile_path, "r", encoding="utf-8", errors="ignore") as f:
                    lines = [ln.strip() for ln in f if ln.strip() and not ln.lstrip().startswith("#")]

                # Prefer web: if present
                for ln in lines:
                    if ":" not in ln:
                        continue
                    proc_type, cmd = ln.split(":", 1)
                    cmd = cmd.strip()
                    if proc_type.strip() == "web" and _looks_like_python_server_cmd(cmd):
                        return cmd

                # Otherwise, first process containing a known Python ASGI/WSGI server
                for ln in lines:
                    if ":" not in ln:
                        continue
                    _, cmd = ln.split(":", 1)
                    cmd = cmd.strip()
                    if _looks_like_python_server_cmd(cmd):
                        return cmd
            except Exception:
                pass

        # pyproject.toml [tool.scripts] (and common variants like tool.pdm.scripts)
        pyproject_path = os.path.join(project_path, "pyproject.toml")
        if os.path.exists(pyproject_path) and tomllib is not None:
            try:
                with open(pyproject_path, "rb") as f:
                    data = tomllib.load(f) or {}

                tool = data.get("tool") or {}
                script_tables = [
                    tool.get("scripts"),
                    (tool.get("pdm") or {}).get("scripts"),
                ]

                for scripts in script_tables:
                    if not isinstance(scripts, dict):
                        continue
                    for val in scripts.values():
                        cmd = None
                        if isinstance(val, str):
                            cmd = val
                        elif isinstance(val, dict):
                            cmd = val.get("cmd") or val.get("command") or val.get("shell")
                        if cmd and _looks_like_python_server_cmd(cmd):
                            return cmd.strip()
            except Exception:
                pass

        # Common start scripts that may contain uvicorn (start.sh, entrypoint.sh, etc.)
        for fname in [
            "start.sh", "run.sh", "entrypoint.sh", "docker-entrypoint.sh", "startup.sh",
            "start.bat", "run.bat",
        ]:
            fpath = os.path.join(project_path, fname)
            if not os.path.exists(fpath):
                continue
            try:
                with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#"):
                            continue
                        if _looks_like_python_server_cmd(line):
                            if line.startswith("exec "):
                                line = line[5:].strip()
                            return line
            except Exception:
                pass

        return None

    def _infer_entry_from_explicit_start(cmd: str) -> Optional[str]:
        cmd = (cmd or "").strip()
        if not cmd:
            return None

        py_match = re.search(r'\bpython(?:\d+(?:\.\d+)*)?\s+([^\s]+\.py)\b', cmd)
        if py_match:
            return py_match.group(1).strip().strip('"').strip("'")

        if "uvicorn" in cmd or "gunicorn" in cmd:
            target_match = re.search(r'\b([A-Za-z0-9_./-]+):[A-Za-z_][A-Za-z0-9_]*\b', cmd)
            if target_match:
                target = target_match.group(1).strip().strip('"').strip("'")
                if target.endswith(".py"):
                    return target
                if "/" in target or "\\" in target:
                    return f"{target}.py"
                return f"{target.replace('.', '/')}.py"

        return None

    explicit = _find_explicit_start_command()
    if explicit:
        result["start_command"] = explicit
        inferred_entry = _infer_entry_from_explicit_start(explicit)
        if inferred_entry:
            result["entry_point"] = inferred_entry
        return result
    
    # Check for common entry files
    for entry in ["app.py", "main.py", "run.py", "server.py", "wsgi.py"]:
        if os.path.exists(os.path.join(project_path, entry)):
            result["entry_point"] = entry
            
            # Check if it's FastAPI/uvicorn
            try:
                with open(
                    os.path.join(project_path, entry),
                    'r', encoding='utf-8', errors='ignore'
                ) as f:
                    content = f.read()
            except Exception:
                content = ""
            if "FastAPI" in content or "fastapi" in content:
                # Try regex first for the common single-line case
                app_var = "app"  # safe default
                var_match = re.search(
                    r'^(\w+)\s*=\s*FastAPI\s*\(', content, re.MULTILINE
                )
                if var_match:
                    app_var = var_match.group(1)
                else:
                    # Fallback: use ast for annotated or multi-line instantiation
                    try:
                        import ast as _ast
                        tree = _ast.parse(content)
                        for node in _ast.walk(tree):
                            if isinstance(node, _ast.Assign):
                                if (isinstance(node.value, _ast.Call) and
                                    isinstance(node.value.func, _ast.Name) and
                                    node.value.func.id == "FastAPI"):
                                    if node.targets and isinstance(node.targets[0], _ast.Name):
                                        app_var = node.targets[0].id
                                        break
                    except Exception:
                        pass  # keep default "app"

                result["start_command"] = (
                    f"uvicorn {entry[:-3]}:{app_var} --host 0.0.0.0 --port 8000"
                )
            elif "Flask" in content:
                result["start_command"] = "flask run --host=0.0.0.0"
            else:
                result["start_command"] = f"python {entry}"
            break
    
    return result


# ============================================================================
# PORT EXTRACTION FUNCTIONS
# ============================================================================

def _parse_env_for_port(project_path: str, allow_backend_keys: bool = True) -> Optional[int]:
    """
    Parse .env variants for known port keys.
    For frontend reads, frontend-specific keys are prioritized over generic PORT.
    """
    # Prefer real env files. Template files are fallback-only.
    env_file_groups = [
        [".env.local", ".env.development", ".env.production", ".env"],
        [".env.example", ".env.sample"],
    ]

    if allow_backend_keys:
        key_priority = [
            "BACKEND_PORT", "SERVER_PORT", "API_PORT",
            "PORT",
            "FRONTEND_PORT", "CLIENT_PORT", "VITE_PORT",
            "REACT_APP_PORT", "NEXT_PUBLIC_PORT", "VITE_DEV_PORT",
        ]
    else:
        # Frontend extraction only trusts frontend-specific keys. A generic
        # PORT is ambiguous (commonly refers to the backend/server process),
        # so it's never used to override a frontend framework default here.
        key_priority = [
            "FRONTEND_PORT", "CLIENT_PORT", "VITE_PORT",
            "REACT_APP_PORT", "NEXT_PUBLIC_PORT", "VITE_DEV_PORT",
        ]
    valid_keys = set(key_priority)

    for env_files in env_file_groups:
        for env_file in env_files:
            env_path = os.path.join(project_path, env_file)
            if not os.path.exists(env_path):
                continue
            try:
                found_ports: Dict[str, int] = {}
                with open(env_path, 'r', encoding='utf-8', errors='ignore') as f:
                    for raw_line in f:
                        line = raw_line.strip()
                        if not line or line.startswith("#") or "=" not in line:
                            continue

                        key, value = line.split("=", 1)
                        key = key.strip().upper()
                        if key not in valid_keys:
                            continue

                        value = value.strip().strip('"').strip("'")
                        m = re.search(r"\b(\d{2,5})\b", value)
                        if not m:
                            continue
                        try:
                            port = int(m.group(1))
                        except ValueError:
                            continue
                        if 1 <= port <= 65535:
                            found_ports[key] = port

                for key in key_priority:
                    if key in found_ports:
                        port = found_ports[key]
                        print(f"PORT key detected: {key}={port} in {env_file}")
                        return port
            except Exception as e:
                print(f"Error parsing {env_file}: {e}")

    return None


def _scan_source_for_port(project_path: str, language: str = "javascript") -> Optional[int]:
    """
    Scan source files for port patterns like app.listen(4000) or server.listen(5000).
    Returns the port number if found, None otherwise.
    """
    if language in ("javascript", "typescript", "JavaScript", "TypeScript"):
        # Node.js source files to scan (including TypeScript and lib folder)
        source_files = [
            # JavaScript files
            "server.js", "app.js", "index.js", "main.js",
            "src/server.js", "src/app.js", "src/index.js", "src/main.js",
            "server/index.js", "server/app.js",
            # TypeScript files (Fix 1)
            "server.ts", "app.ts", "index.ts", "main.ts",
            "src/server.ts", "src/app.ts", "src/index.ts", "src/main.ts",
            "server/index.ts", "server/app.ts",
            # lib folder (Fix 5) - including CLI tools
            "lib/index.js", "lib/server.js", "lib/app.js", "lib/cli.js",
            "lib/index.ts", "lib/server.ts", "lib/app.ts", "lib/cli.ts",
        ]

        priority_patterns = [
            r"\.listen\s*\(\s*(\d{4,5})\s*[,)]",
            r"process\.env\.PORT\s*\|\|\s*(\d{4,5})",
            r"process\.env\.PORT\s*\?\?\s*(\d{4,5})",
            r"parseInt\s*\(\s*process\.env\.PORT\s*(?:\|\||\?\?)\s*['\"]?(\d{4,5})",
            r"Number\s*\(\s*process\.env\.PORT\s*\)\s*(?:\|\||\?\?)\s*(\d{4,5})",
            r"(?:const|let|var)\s+PORT\s*=\s*(\d{4,5})",
            r"process\.env\.\w+\s*\|\|\s*(\d{4,5})",
        ]
        fallback_patterns = [
            r"port\s*:\s*(\d{4,5})",
        ]
    elif language in ("python", "Python"):
        # Python source files
        source_files = [
            "app.py", "main.py", "server.py", "run.py", "wsgi.py",
            "src/main.py", "src/app.py"
        ]

        # Python port patterns
        patterns = [
            # port=8000 or port = 8000
            r"port\s*=\s*(\d{4,5})",
            # app.run(port=5000)
            r"run\s*\([^)]*port\s*=\s*(\d{4,5})",
            # uvicorn.run(..., port=8000)
            r"uvicorn\.run\s*\([^)]*port\s*=\s*(\d{4,5})",
        ]
    else:
        return None

    if language in ("javascript", "typescript", "JavaScript", "TypeScript"):
        # Pass 1: priority patterns across fixed source file list
        for source_file in source_files:
            file_path = os.path.join(project_path, source_file)
            if not os.path.exists(file_path):
                continue
            try:
                with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()

                for pattern in priority_patterns:
                    match = re.search(pattern, content, re.IGNORECASE)
                    if match:
                        port = int(match.group(1))
                        if 1000 <= port <= 65535:
                            print(f"🔌 Found port {port} in {source_file}")
                            return port
            except Exception as e:
                print(f"Error scanning {source_file}: {e}")

        # Pass 2: fallback pattern across fixed source file list
        for source_file in source_files:
            file_path = os.path.join(project_path, source_file)
            if not os.path.exists(file_path):
                continue
            try:
                with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()

                for pattern in fallback_patterns:
                    match = re.search(pattern, content, re.IGNORECASE)
                    if match:
                        port = int(match.group(1))
                        if 1000 <= port <= 65535:
                            print(f"🔌 Found port {port} in {source_file}")
                            return port
            except Exception as e:
                print(f"Error scanning {source_file}: {e}")

        # Fallback for JS/TS: recursively scan source files when fixed-name list misses.
        skip_dirs = {"node_modules", "dist", "build", ".next", ".git", ".venv", "venv"}
        ignore_name_parts = (
            ".test.", ".spec.",
            "vite.config", "webpack.config", "next.config", "jest.config",
        )
        scanned_files = 0
        candidate_files = []

        for root, dirs, files in os.walk(project_path):
            dirs[:] = [d for d in dirs if d not in skip_dirs]

            for file in files:
                if not file.lower().endswith((".js", ".ts")):
                    continue
                file_lower = file.lower()
                if any(part in file_lower for part in ignore_name_parts):
                    continue
                if scanned_files >= 100:
                    break

                scanned_files += 1
                candidate_files.append(os.path.join(root, file))

            if scanned_files >= 100:
                break

        # Recursive pass 1: priority patterns
        for file_path in candidate_files:
            try:
                with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read(8000)

                for pattern in priority_patterns:
                    match = re.search(pattern, content, re.IGNORECASE)
                    if match:
                        port = int(match.group(1))
                        if 1000 <= port <= 65535:
                            rel_path = os.path.relpath(file_path, project_path).replace("\\", "/")
                            print(f"🔌 Found port {port} in {rel_path}")
                            return port
            except Exception as e:
                rel_path = os.path.relpath(file_path, project_path).replace("\\", "/")
                print(f"Error scanning {rel_path}: {e}")

        # Recursive pass 2: fallback pattern
        for file_path in candidate_files:
            try:
                with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read(8000)

                for pattern in fallback_patterns:
                    match = re.search(pattern, content, re.IGNORECASE)
                    if match:
                        port = int(match.group(1))
                        if 1000 <= port <= 65535:
                            rel_path = os.path.relpath(file_path, project_path).replace("\\", "/")
                            print(f"🔌 Found port {port} in {rel_path}")
                            return port
            except Exception as e:
                rel_path = os.path.relpath(file_path, project_path).replace("\\", "/")
                print(f"Error scanning {rel_path}: {e}")
    else:
        for source_file in source_files:
            file_path = os.path.join(project_path, source_file)
            if os.path.exists(file_path):
                try:
                    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                        content = f.read()

                    for pattern in patterns:
                        match = re.search(pattern, content, re.IGNORECASE)
                        if match:
                            port = int(match.group(1))
                            # Validate port is reasonable (not 0 or too large)
                            if 1000 <= port <= 65535:
                                print(f"🔌 Found port {port} in {source_file}")
                                return port
                except Exception as e:
                    print(f"Error scanning {source_file}: {e}")

    return None


def _get_framework_default_port(framework: str, language: str) -> int:
    """
    Get default port based on framework/language.
    """
    framework_ports = {
        "Express.js": 3000,
        "Fastify": 3000,
        "NestJS": 3000,
        "Next.js": 3000,
        "Vite": 5173,
        "React": 3000,  # CRA default
        "Vue": 8080,
        "Angular": 4200,
        "Flask": 5000,
        "Django": 8000,
        "FastAPI": 8000,
        "Spring Boot": 8080,
        "Rails": 3000,
        "Gatsby": 8000,  # Fix 6
        "Remix": 3000,   # Fix 6
    }
    
    language_ports = {
        "JavaScript": 3000,
        "TypeScript": 3000,
        "Python": 8000,
        "Java": 8080,
        "Go": 8080,
        "Ruby": 3000,
    }
    
    return framework_ports.get(framework) or language_ports.get(language) or 3000


def extract_port_from_project(project_path: str, framework: str = "Unknown", language: str = "Unknown") -> Dict[str, any]:
    """
    Extract backend port using priority:
    1. .env file
    2. Source code scanning
    3. Framework defaults
    
    Returns dict with:
      - port: The detected port number
      - source: Where the port was detected from ("env", "source", "default")
    """
    result = {
        "port": None,
        "source": None,
    }
    
    # Priority 1: Check .env files
    env_port = _parse_env_for_port(project_path, allow_backend_keys=True)
    if env_port:
        result["port"] = env_port
        result["source"] = "env"
        print(f"Port detected from .env: {env_port}")
        return result
    
    # Priority 2: Scan source code
    source_port = _scan_source_for_port(project_path, language)
    if source_port:
        result["port"] = source_port
        result["source"] = "source"
        print(f"Port detected from source code: {source_port}")
        return result
    
    # Priority 3: Framework defaults
    default_port = _get_framework_default_port(framework, language)
    result["port"] = default_port
    result["source"] = "default"
    print(f"Using framework default port: {default_port}")
    
    return result


def extract_frontend_port(project_path: str) -> Dict[str, any]:
    """
    Extract frontend dev server port from vite.config.js or other frontend configs.
    """
    result = {
        "port": None,
        "source": None,
    }
    
    # Check vite.config.js
    vite_configs = ["vite.config.js", "vite.config.ts", "vite.config.mjs"]
    for config_file in vite_configs:
        config_path = os.path.join(project_path, config_file)
        if os.path.exists(config_path):
            try:
                with open(config_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                
                # Pattern: port: 5173 or port: 3000
                match = re.search(r'port\s*:\s*(\d{4,5})', content)
                if match:
                    result["port"] = int(match.group(1))
                    result["source"] = config_file
                    print(f"Frontend port from {config_file}: {result['port']}")
                    return result
            except Exception:
                pass
    
    # Check .env for VITE_PORT or similar
    env_port = _parse_env_for_port(project_path, allow_backend_keys=False)
    if env_port:
        result["port"] = env_port
        result["source"] = "env"
        return result
    
    # Check package.json dependencies for default
    pkg_path = os.path.join(project_path, "package.json")
    if os.path.exists(pkg_path):
        try:
            with open(pkg_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
            
            if "vite" in deps:
                result["port"] = 5173
                result["source"] = "vite_default"
            elif "react-scripts" in deps:
                result["port"] = 3000
                result["source"] = "cra_default"
            elif "next" in deps:
                result["port"] = 3000
                result["source"] = "next_default"
            elif "@vue/cli-service" in deps:
                result["port"] = 8080
                result["source"] = "vue_default"
            elif "@angular/cli" in deps:
                result["port"] = 4200
                result["source"] = "angular_default"
            else:
                result["port"] = None
                result["source"] = "default"
                
        except Exception:
            result["port"] = None
            result["source"] = "default"
    else:
        result["port"] = None
        result["source"] = "default"
    
    print(f"Frontend port: {result['port']} (source: {result['source']})")
    return result


# ============================================================================
# DATABASE DETECTION FUNCTIONS
# ============================================================================

# Database env var names (in priority order)
DB_ENV_VARS = [
    # MongoDB-specific
    "MONGO_URI",
    "MONGODB_URI",
    "MONGO_URL",
    "MONGODB_URL",
    "MONGO_CONNECTION_STRING",
    # PostgreSQL-specific
    "POSTGRES_URL",
    "POSTGRES_URI",
    "PG_URL",
    # MySQL-specific
    "MYSQL_URL",
    "MYSQL_URI",
    # Redis-specific
    "REDIS_URL",
    "REDIS_URI",
    # Generic - type inferred from URL content only
    "DATABASE_URL",
    "DB_URL",
]

# Cloud database indicators
CLOUD_DB_PATTERNS = [
    # MongoDB Atlas
    "mongodb+srv://",
    "mongodb.net",
    ".mongodb.com",
    # AWS
    ".amazonaws.com",
    ".rds.amazonaws.com",
    # Google Cloud
    ".cloudsql.",
    # Azure
    ".database.azure.com",
    ".cosmos.azure.com",
    # Heroku
    ".heroku.com",
    # ElephantSQL
    ".elephantsql.com",
    # PlanetScale
    ".psdb.cloud",
    # Supabase
    ".supabase.co",
    # Neon
    ".neon.tech",
    # Railway
    ".railway.app",
]


COMPOSE_HOSTNAMES = {
    "mongo", "mongodb", "postgres", "postgresql",
    "mysql", "mariadb", "redis", "db", "database"
}


def _is_local_host(h: str) -> bool:
    h = (h or "").strip().lower()
    if not h:
        return False
    if h in ("localhost", "127.0.0.1", "host.docker.internal"):
        return True
    if h in COMPOSE_HOSTNAMES:
        return True
    if h.endswith((".local", ".internal", ".localhost", ".test")):
        return True
    if "." not in h:
        return True
    try:
        return ipaddress.ip_address(h).is_private
    except ValueError:
        return False


def _parse_env_for_database(project_path: str) -> Dict[str, any]:
    """
    Parse .env files for database connection URLs.
    Returns dict with:
      - db_type: "mongodb", "postgresql", "mysql", "redis", or None
      - is_cloud: True if cloud database, False if local
      - env_var_name: Name of the environment variable (e.g., "MONGO_URI")
      - connection_url: The actual URL (for analysis, not to expose)
    """
    result = {
        "db_type": None,
        "is_cloud": False,
        "env_var_name": None,
        "connection_url": None,
    }
    
    # Prefer real env files. Template files are fallback-only.
    env_file_groups = [
        [".env", ".env.local", ".env.development", ".env.production"],
        [".env.example", ".env.sample"],
    ]
    
    for env_files in env_file_groups:
        for env_file in env_files:
            env_path = os.path.join(project_path, env_file)
            if os.path.exists(env_path):
                try:
                    with open(env_path, 'r', encoding='utf-8', errors='ignore') as f:
                        content = f.read()
                    
                    for var_name in DB_ENV_VARS:
                        # Pattern: VAR_NAME=value or VAR_NAME = value
                        pattern = rf'^{var_name}\s*=\s*(.+)$'
                        match = re.search(pattern, content, re.MULTILINE | re.IGNORECASE)
                        if match:
                            url = match.group(1).strip().strip('"\'')
                            result["env_var_name"] = var_name
                            result["connection_url"] = url
                            
                            # Detect database type from URL
                            url_lower = url.lower()
                            if "mongodb" in url_lower or "mongo" in var_name.lower():
                                result["db_type"] = "mongodb"
                            elif "postgres" in url_lower or "pg" in var_name.lower():
                                result["db_type"] = "postgresql"
                            elif "mysql" in url_lower:
                                result["db_type"] = "mysql"
                            elif "redis" in url_lower:
                                result["db_type"] = "redis"
                            
                            try:
                                parsed = urllib.parse.urlparse(url_lower)
                                hostname = parsed.hostname or ""
                            except Exception:
                                hostname = ""

                            is_local = _is_local_host(hostname)
                            is_cloud_url = any(p in url_lower for p in CLOUD_DB_PATTERNS)

                            # Prefer explicit locality over cloud-pattern shortcut.
                            if is_local:
                                result["is_cloud"] = False
                                print(f"🏠 Detected LOCAL database: {result['db_type']} (from {env_file})")
                            elif is_cloud_url:
                                result["is_cloud"] = True
                                print(f"☁️ Detected CLOUD database: {result['db_type']} (from {env_file})")
                            else:
                                result["is_cloud"] = True  # unknown host, conservative assumption
                                print(f"☁️ Detected database (assuming cloud): {result['db_type']} (from {env_file})")
                            
                            return result
                            
                except Exception as e:
                    print(f"Error parsing {env_file} for database: {e}")
    
    return result


def extract_database_info(project_path: str, detected_db: str = None) -> Dict[str, any]:
    """
    Extract complete database information for Docker deployment.
    
    Returns dict with:
      - db_type: "mongodb", "postgresql", "mysql", "redis", or None
      - is_cloud: True if cloud database (don't add container)
      - needs_container: True if should add database container to compose
      - env_var_name: Name of env var to pass through
      - default_port: Default port for this database type
      - docker_image: Docker image to use if container needed
    """
    result = {
        "db_type": None,
        "is_cloud": False,
        "needs_container": False,
        "env_var_name": None,
        "connection_url": None,
        "default_port": None,
        "docker_image": None,
        "source": None,
    }
    
    # Database defaults
    db_defaults = {
        "mongodb": {"port": 27017, "image": "mongo:latest"},
        "postgresql": {"port": 5432, "image": "postgres:15-alpine"},
        "mysql": {"port": 3306, "image": "mysql:8"},
        "redis": {"port": 6379, "image": "redis:alpine"},
    }

    synthesized_env_defaults = {
        "mongodb": ("MONGO_URI", "mongodb://mongo:27017/app"),
        "postgresql": ("DATABASE_URL", "postgresql://postgres:postgres@postgres:5432/app"),
        "mysql": ("DATABASE_URL", "mysql://root:root@mysql:3306/app"),
        "redis": ("REDIS_URL", "redis://redis:6379/0"),
    }
    
    # First, check .env for database URL
    env_db = _parse_env_for_database(project_path)
    
    if env_db.get("db_type"):
        result["source"] = "env"
        result["db_type"] = env_db["db_type"]
        result["is_cloud"] = env_db["is_cloud"]
        result["env_var_name"] = env_db["env_var_name"]
        result["connection_url"] = env_db.get("connection_url")
        
        # Only need container if LOCAL database
        result["needs_container"] = not env_db["is_cloud"]
        
        if result["db_type"] in db_defaults:
            result["default_port"] = db_defaults[result["db_type"]]["port"]
            result["docker_image"] = db_defaults[result["db_type"]]["image"]
    
    # Fallback to detected_db from dependency analysis
    elif detected_db:
        result["source"] = "detected"
        db_lower = detected_db.lower()
        for db_name in db_defaults:
            if db_name in db_lower:
                result["db_type"] = db_name
                result["default_port"] = db_defaults[db_name]["port"]
                result["docker_image"] = db_defaults[db_name]["image"]
                # No .env found, assume local development
                result["is_cloud"] = False
                result["needs_container"] = True
                print(f"🏠 No DB URL in .env, assuming LOCAL {db_name} container needed")
                break

    if result["needs_container"] and not result["env_var_name"] and result["db_type"] in synthesized_env_defaults:
        synth_key, synth_url = synthesized_env_defaults[result["db_type"]]
        result["env_var_name"] = synth_key
        result["connection_url"] = synth_url
    
    print(f"🗄️ Database detection: type={result['db_type']}, cloud={result['is_cloud']}, needs_container={result['needs_container']}")
    return result
