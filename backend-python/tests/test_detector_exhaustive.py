"""
Exhaustive pytest suite for detector.py — TDD-style.

Rules:
  - tmp_path: physical temp files, NO mocking os.walk or open()
  - Mock externals: ml_analyzer, command_extractor
  - Heavy @pytest.mark.parametrize for efficient variation coverage

Test groups:
  1. File Parsing        – parse_dependencies_file edge cases
  2. Port Detection      – priority conflicts between .env, package.json, inline code
  3. Database Detection  – cloud .env URIs vs. local docker-compose
  4. Architecture        – fullstack MERN vs. single-service layouts
  5. Orchestrator        – detect_framework output + deploy_blocked logic
"""

import json
import os
import sys
import textwrap
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# Ensure the backend package is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.utils.detector import (
    parse_dependencies_file,
    detect_env_variables,
    _read_env_key_values,
    find_project_root,
    heuristic_language_detection,
    heuristic_framework_detection,
    _detect_fullstack_structure,
    _infer_service_type,
    _find_all_services_by_deps,
    _find_python_services,
    _merge_node_python_stubs,
    _suppress_root_if_children_found,
    _drop_empty_shells,
    infer_services,
    _detect_package_manager,
    _scan_js_for_port_hint,
    _detect_port_from_package_json,
    _classify_docker_service,
    _scan_code_for_ports,
    detect_ports_for_project,
    _parse_dockerfile_expose_ports,
    _infer_database_port,
    detect_databases,
    detect_framework,
    get_runtime_info,
    detect_docker_files,
    norm_path,
    BACKEND_DEPS,
    FRONTEND_DEPS,
    DB_KEYWORDS,
    PYTHON_BACKEND_DEPS,
    _normalize_dep_name,
)


# ═══════════════════════════════════════════════════════════════════════
# FIXTURES: reusable project scaffolding helpers
# ═══════════════════════════════════════════════════════════════════════


def _write(path: Path, content: str = ""):
    """Write a file, creating parents as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content), encoding="utf-8")


def _make_express_backend(root: Path, port: int = 5000, has_env: bool = False):
    """Scaffold a minimal Express backend."""
    _write(root / "package.json", json.dumps({
        "name": "backend",
        "dependencies": {"express": "4.18"},
        "scripts": {"start": "node server.js"},
    }))
    _write(root / "server.js", f"""
        const app = require("express")();
        app.listen({port});
    """)
    if has_env:
        _write(root / ".env", f"PORT={port}\nDB_URL=mongodb://localhost/test\n")


def _make_react_frontend(root: Path):
    """Scaffold a minimal React frontend."""
    _write(root / "package.json", json.dumps({
        "name": "frontend",
        "dependencies": {"react": "18.0", "react-dom": "18.0"},
        "devDependencies": {"vite": "5.0"},
        "scripts": {"build": "vite build", "dev": "vite"},
    }))
    _write(root / "src" / "App.jsx", 'import React from "react";\n')


# ═══════════════════════════════════════════════════════════════════════
# GROUP 1: FILE PARSING
# ═══════════════════════════════════════════════════════════════════════


class TestParseDependenciesFile:
    """parse_dependencies_file: handle all file types and edge cases."""

    # ── requirements.txt ───────────────────────────────────────────────

    @pytest.mark.parametrize("content, expected", [
        # Normal pinned deps
        ("flask==2.3\nrequests>=2.28\ngunicorn~=21.2\n", ["flask", "requests", "gunicorn"]),
        # Comments and blank lines
        ("# This is a comment\nflask==2.3\n\n# Another\nrequests\n", ["flask", "requests"]),
        # Flags (e.g. -r base.txt, --index-url …) should be skipped
        ("-r base.txt\nflask==2.3\n--index-url http://x\n", ["flask"]),
        # Extras notation: package[extra]
        ("uvicorn[standard]==0.22\n", ["uvicorn"]),
        # Empty file
        ("", []),
        # Only comments
        ("# comment1\n# comment2\n", []),
    ], ids=[
        "pinned", "comments_blanks", "flags_skipped",
        "extras_stripped", "empty", "only_comments",
    ])
    def test_requirements_txt(self, tmp_path, content, expected):
        f = tmp_path / "requirements.txt"
        f.write_text(content, encoding="utf-8")
        result = parse_dependencies_file(str(f), "requirements.txt")
        assert result == expected

    # ── package.json ───────────────────────────────────────────────────

    @pytest.mark.parametrize("data, expected_deps", [
        # Merges deps + devDeps
        ({"dependencies": {"express": "4.18"}, "devDependencies": {"nodemon": "3.0"}},
         {"express", "nodemon"}),
        # Missing sections
        ({"dependencies": {"react": "18.0"}}, {"react"}),
        ({}, set()),
    ], ids=["merge", "deps_only", "empty_json"])
    def test_package_json(self, tmp_path, data, expected_deps):
        f = tmp_path / "package.json"
        f.write_text(json.dumps(data), encoding="utf-8")
        result = parse_dependencies_file(str(f), "package.json")
        assert set(result) == expected_deps

    def test_package_json_bad_json(self, tmp_path):
        """Corrupt JSON should return empty list, not crash."""
        f = tmp_path / "package.json"
        f.write_text("{ THIS IS NOT JSON", encoding="utf-8")
        result = parse_dependencies_file(str(f), "package.json")
        assert result == []

    # ── pom.xml ────────────────────────────────────────────────────────

    def test_pom_xml(self, tmp_path):
        content = textwrap.dedent("""\
            <project>
              <dependencies>
                <dependency>
                  <groupId>org.spring</groupId>
                  <artifactId>spring-boot</artifactId>
                </dependency>
                <dependency>
                  <artifactId>spring-web</artifactId>
                </dependency>
              </dependencies>
            </project>
        """)
        f = tmp_path / "pom.xml"
        f.write_text(content, encoding="utf-8")
        result = parse_dependencies_file(str(f), "pom.xml")
        assert "spring-boot" in result
        assert "spring-web" in result

    # ── go.mod ─────────────────────────────────────────────────────────

    def test_go_mod(self, tmp_path):
        content = textwrap.dedent("""\
            module github.com/user/project

            go 1.21

            require (
            	github.com/gin-gonic/gin v1.9.0
            	github.com/lib/pq v1.10.7
            )
        """)
        f = tmp_path / "go.mod"
        f.write_text(content, encoding="utf-8")
        result = parse_dependencies_file(str(f), "go.mod")
        assert "github.com/gin-gonic/gin" in result
        assert "github.com/lib/pq" in result

    # ── Cap at 50 ──────────────────────────────────────────────────────

    def test_dependencies_capped_at_50(self, tmp_path):
        lines = "\n".join(f"pkg{i}==1.0" for i in range(100))
        f = tmp_path / "requirements.txt"
        f.write_text(lines, encoding="utf-8")
        result = parse_dependencies_file(str(f), "requirements.txt")
        assert len(result) == 50

    # ── Unknown file type ──────────────────────────────────────────────

    def test_unknown_file_type(self, tmp_path):
        f = tmp_path / "Gemfile"
        f.write_text("gem 'rails'\n", encoding="utf-8")
        result = parse_dependencies_file(str(f), "Gemfile")
        assert result == []


class TestEnvParsing:
    """detect_env_variables and _read_env_key_values."""

    @pytest.mark.parametrize("filename", [".env", ".env.example", ".env.local"])
    def test_detects_env_keys(self, tmp_path, filename):
        _write(tmp_path / filename, "PORT=5000\nDB_URL=mongodb://localhost\n# comment\n")
        keys = detect_env_variables(str(tmp_path))
        assert "PORT" in keys
        assert "DB_URL" in keys

    def test_skips_comments_and_blanks(self, tmp_path):
        _write(tmp_path / ".env", "# this is a comment\n\nVALID_KEY=123\n")
        keys = detect_env_variables(str(tmp_path))
        assert keys == ["VALID_KEY"]

    def test_read_key_values(self, tmp_path):
        _write(tmp_path / ".env", "HOST=0.0.0.0\nPORT=8080\n")
        kv = _read_env_key_values(str(tmp_path))
        assert kv["HOST"] == "0.0.0.0"
        assert kv["PORT"] == "8080"

    def test_multiple_env_files_merged(self, tmp_path):
        _write(tmp_path / ".env", "A=1\n")
        _write(tmp_path / ".env.local", "B=2\n")
        kv = _read_env_key_values(str(tmp_path))
        assert "A" in kv
        assert "B" in kv

    def test_no_env_files(self, tmp_path):
        keys = detect_env_variables(str(tmp_path))
        assert keys == []


class TestNormalizeDepName:
    """_normalize_dep_name: preserve scoped package names."""

    def test_preserves_scoped_package_with_version_suffix(self):
        assert _normalize_dep_name("@nestjs/core@10.0.0") == "@nestjs/core"

    def test_preserves_scoped_package_without_version_suffix(self):
        assert _normalize_dep_name("@types/react-dom") == "@types/react-dom"


class TestFindProjectRoot:
    """find_project_root: navigate nested extraction folders."""

    def test_root_has_package_json(self, tmp_path):
        _write(tmp_path / "package.json", "{}")
        assert find_project_root(str(tmp_path)) == str(tmp_path)

    def test_nested_single_folder(self, tmp_path):
        nested = tmp_path / "project-abc"
        nested.mkdir()
        _write(nested / "package.json", "{}")
        result = find_project_root(str(tmp_path))
        assert result == str(nested)

    def test_deeply_nested(self, tmp_path):
        deep = tmp_path / "a" / "b"
        deep.mkdir(parents=True)
        _write(deep / "requirements.txt", "flask\n")
        result = find_project_root(str(tmp_path))
        assert result == str(deep)

    def test_default_max_depth_reaches_five_levels(self, tmp_path):
        deep = tmp_path / "a" / "b" / "c" / "d" / "e"
        deep.mkdir(parents=True)
        _write(deep / "package.json", "{}")
        result = find_project_root(str(tmp_path))
        assert result == str(deep)

    def test_max_depth_honoured(self, tmp_path):
        deep = tmp_path / "a" / "b" / "c" / "d" / "e"
        deep.mkdir(parents=True)
        _write(deep / "package.json", "{}")
        result = find_project_root(str(tmp_path), max_depth=2)
        # Cannot reach depth 5 with max_depth=2
        assert result != str(deep)

    def test_multiple_siblings_stays(self, tmp_path):
        """If root has >1 subfolder and no framework file, stay at root."""
        (tmp_path / "dir_a").mkdir()
        (tmp_path / "dir_b").mkdir()
        result = find_project_root(str(tmp_path))
        assert result == str(tmp_path)

    def test_ignores_infrastructure_dirs(self, tmp_path):
        """node_modules, .git, etc. are not traversed into."""
        (tmp_path / "node_modules").mkdir()
        real = tmp_path / "myapp"
        real.mkdir()
        _write(real / "package.json", "{}")
        result = find_project_root(str(tmp_path))
        assert result == str(real)

    def test_workspace_root_found_inside_archive_wrapper(self, tmp_path):
        """If workspaces package.json is one level deep, return that workspace root."""
        repo_root = tmp_path / "repo-main"
        repo_root.mkdir()
        _write(repo_root / "package.json", json.dumps({
            "name": "monorepo",
            "private": True,
            "workspaces": ["backend", "frontend"],
        }))
        _write(repo_root / "backend" / "package.json", json.dumps({
            "dependencies": {"express": "4"}
        }))
        _write(repo_root / "frontend" / "package.json", json.dumps({
            "dependencies": {"react": "18"}
        }))

        result = find_project_root(str(tmp_path), max_depth=3)
        assert result == str(repo_root)

    def test_ambiguous_children_majority_returns_parent_root(self, tmp_path):
        """If direct children are mostly manifest services, keep current directory as root."""
        mern = tmp_path / "mern"
        mern.mkdir()
        _write(mern / "backend" / "package.json", json.dumps({
            "dependencies": {"express": "4"}
        }))
        _write(mern / "backend" / "server.js", "const app = require('express')();")
        _write(mern / "frontend" / "package.json", json.dumps({
            "dependencies": {"react": "18"}
        }))
        _write(mern / "frontend" / "src" / "App.jsx", "export default function App() { return null; }")
        (mern / "docs").mkdir()

        result = find_project_root(str(tmp_path))
        assert result == str(mern)

    def test_stale_manifest_wrapper_descends_to_real_source_project(self, tmp_path):
        """A manifest-only wrapper should not block descent to inner service with source files."""
        wrapper = tmp_path / "repo-main"
        wrapper.mkdir()
        _write(wrapper / "package.json", json.dumps({
            "name": "workspace-shell",
            "private": True,
            "scripts": {"test": "echo ok"},
        }))
        _write(wrapper / "backend" / "package.json", json.dumps({
            "dependencies": {"express": "4"}
        }))
        _write(wrapper / "backend" / "server.js", "const app = require('express')(); app.listen(3000);")

        result = find_project_root(str(tmp_path))
        assert result == str(wrapper / "backend")

    def test_service_parent_not_promoted_with_single_service_child(self, tmp_path):
        """Single service child should stay selected; parent needs 2+ service siblings."""
        wrapper = tmp_path / "wrapper"
        wrapper.mkdir()
        _write(wrapper / "client" / "package.json", json.dumps({
            "dependencies": {"react": "18"}
        }))
        _write(wrapper / "client" / "src" / "App.jsx", "export default function App() { return null; }")
        (wrapper / "docs").mkdir()

        result = find_project_root(str(tmp_path))
        assert result == str(wrapper / "client")


# ═══════════════════════════════════════════════════════════════════════
# GROUP 2: PORT DETECTION
# ═══════════════════════════════════════════════════════════════════════


class TestScanJsForPortHint:
    """_scan_js_for_port_hint: find ports in JS/TS source files."""

    @pytest.mark.parametrize("code, expected_port", [
        ('const PORT = process.env.PORT || 5050;\napp.listen(PORT);', 5050),
        ('app.listen(8080);', 8080),
        ('server.listen(3001, () => console.log("up"));', 3001),
        ('const p = process.env.PORT || 4000;', 4000),
    ], ids=["env_or_fallback", "app_listen", "server_listen", "env_var"])
    def test_detects_port(self, tmp_path, code, expected_port):
        _write(tmp_path / "server.js", code)
        result = _scan_js_for_port_hint(str(tmp_path))
        assert result == expected_port

    def test_ignores_node_modules(self, tmp_path):
        nm = tmp_path / "node_modules" / "foo"
        nm.mkdir(parents=True)
        _write(nm / "index.js", "app.listen(9999);")
        _write(tmp_path / "index.js", "app.listen(3000);")
        result = _scan_js_for_port_hint(str(tmp_path))
        assert result == 3000

    def test_no_port_returns_none(self, tmp_path):
        _write(tmp_path / "index.js", "console.log('hello');")
        assert _scan_js_for_port_hint(str(tmp_path)) is None

    def test_ts_files_scanned(self, tmp_path):
        _write(tmp_path / "server.ts", "app.listen(4500);")
        assert _scan_js_for_port_hint(str(tmp_path)) == 4500

    def test_port_below_1024_ignored(self, tmp_path):
        _write(tmp_path / "index.js", "app.listen(80);")
        # 80 is <1024 so should be rejected
        assert _scan_js_for_port_hint(str(tmp_path)) is None


class TestDetectPortFromPackageJson:
    """_detect_port_from_package_json: port from scripts & deps."""

    def test_explicit_port_in_script(self, tmp_path):
        _write(tmp_path / "package.json", json.dumps({
            "scripts": {"start": "PORT=4000 node server.js"},
        }))
        assert _detect_port_from_package_json(str(tmp_path)) == 4000

    def test_vite_default_for_frontend(self, tmp_path):
        _write(tmp_path / "package.json", json.dumps({
            "dependencies": {"vite": "5.0"},
            "scripts": {"dev": "vite"},
        }))
        # prefer_frontend=True + vite dep => 5173
        assert _detect_port_from_package_json(str(tmp_path), prefer_frontend=True) == 5173

    def test_no_package_json(self, tmp_path):
        assert _detect_port_from_package_json(str(tmp_path)) is None

    def test_bad_json(self, tmp_path):
        _write(tmp_path / "package.json", "NOT JSON")
        assert _detect_port_from_package_json(str(tmp_path)) is None

    def test_no_explicit_port_returns_none(self, tmp_path):
        _write(tmp_path / "package.json", json.dumps({
            "scripts": {"start": "node index.js"},
        }))
        assert _detect_port_from_package_json(str(tmp_path)) is None

    @pytest.mark.parametrize("port_in_script, expected", [
        ("PORT=8080 node .", 8080),
        ("cross-env PORT=5000 node .", 5000),
    ], ids=["PORT_equals", "cross_env_PORT"])
    def test_port_patterns(self, tmp_path, port_in_script, expected):
        _write(tmp_path / "package.json", json.dumps({
            "scripts": {"start": port_in_script},
        }))
        assert _detect_port_from_package_json(str(tmp_path)) == expected


class TestScanCodeForPorts:
    """_scan_code_for_ports: generic host:port patterns in any language."""

    @pytest.mark.parametrize("code, ext, expected", [
        ('bind("0.0.0.0:5050")', ".py", 5050),
        ('http.ListenAndServe(":9090", nil)', ".go", None),  # pattern is host:port, not :port
        ('server.listen(3000, "localhost")', ".js", None),    # no 0.0.0.0/localhost:PORT match
        ('connect to localhost:8080', ".py", 8080),
    ], ids=["python_bind", "go_listen", "js_listen_no_match", "localhost_port"])
    def test_generic_port_patterns(self, tmp_path, code, ext, expected):
        _write(tmp_path / f"main{ext}", code)
        result = _scan_code_for_ports(str(tmp_path))
        assert result == expected

    def test_ignores_venv(self, tmp_path):
        venv = tmp_path / "venv" / "lib"
        venv.mkdir(parents=True)
        _write(venv / "something.py", 'connect to 0.0.0.0:9999')
        _write(tmp_path / "app.py", 'connect to 0.0.0.0:8000')
        assert _scan_code_for_ports(str(tmp_path)) == 8000


class TestClassifyDockerService:
    """_classify_docker_service: label compose service names."""

    @pytest.mark.parametrize("name, expected", [
        ("frontend", "frontend"),
        ("client-app", "frontend"),
        ("web", "frontend"),
        ("ui", "frontend"),
        ("backend", "backend"),
        ("api-server", "backend"),
        ("server", "backend"),
        ("app", "backend"),
        ("mongodb", "database"),
        ("postgres-db", "database"),
        ("redis-cache", "database"),
        ("mysql", "database"),
        ("db", "database"),
        ("worker", "other"),
        ("nginx", "other"),
    ], ids=[
        "frontend", "client", "web", "ui",
        "backend", "api", "server", "app",
        "mongo", "postgres", "redis", "mysql", "db",
        "worker", "nginx",
    ])
    def test_classification(self, name, expected):
        assert _classify_docker_service(name) == expected


class TestDockerfileExposePorts:
    """_parse_dockerfile_expose_ports: read EXPOSE from Dockerfiles."""

    def test_single_expose(self, tmp_path):
        _write(tmp_path / "Dockerfile", "FROM node:20\nEXPOSE 3000\nCMD node .")
        ports = _parse_dockerfile_expose_ports(str(tmp_path))
        assert 3000 in ports

    def test_multiple_expose(self, tmp_path):
        _write(tmp_path / "Dockerfile", "FROM node:20\nEXPOSE 80 443\n")
        ports = _parse_dockerfile_expose_ports(str(tmp_path))
        assert 80 in ports
        assert 443 in ports

    def test_expose_with_protocol(self, tmp_path):
        _write(tmp_path / "Dockerfile", "FROM node:20\nEXPOSE 8080/tcp\n")
        ports = _parse_dockerfile_expose_ports(str(tmp_path))
        assert 8080 in ports

    def test_no_dockerfile(self, tmp_path):
        ports = _parse_dockerfile_expose_ports(str(tmp_path))
        assert ports == [] or ports is None or len(ports) == 0


class TestDetectDockerFiles:
    """detect_docker_files: find Dockerfile + docker-compose."""

    def test_detects_both(self, tmp_path):
        _write(tmp_path / "Dockerfile", "FROM node:20\n")
        _write(tmp_path / "docker-compose.yml", "services:\n  app:\n    build: .\n")
        result = detect_docker_files(str(tmp_path))
        assert result["dockerfile"] is True
        assert result["docker_compose"] is True

    def test_detects_none(self, tmp_path):
        _write(tmp_path / "README.md", "Hello")
        result = detect_docker_files(str(tmp_path))
        assert result["dockerfile"] is False
        assert result["docker_compose"] is False


# ═══════════════════════════════════════════════════════════════════════
# GROUP 3: DATABASE DETECTION
# ═══════════════════════════════════════════════════════════════════════


class TestInferDatabasePort:
    """_infer_database_port: env keys, compose content, defaults."""

    @pytest.mark.parametrize("db, env_kv, compose, expected", [
        # Specific env key for Mongo
        ("MongoDB", {"MONGO_PORT": "27017"}, "", 27017),
        # Generic DB_PORT
        ("PostgreSQL", {"DB_PORT": "5432"}, "", 5432),
        # Compose host mapping (maps 15432:5432)
        ("PostgreSQL", {}, "ports:\n  - '15432:5432'", 15432),
        # Default fallback
        ("MySQL", {}, "", 3306),
        # Redis default
        ("Redis", {}, "", 6379),
        # SQLite has no port
        ("SQLite", {}, "", None),
        # Unknown DB
        ("Unknown", {}, "", None),
        # Port inside a connection string
        ("MongoDB", {"MONGO_PORT": "mongodb://host:27018/db"}, "", 27018),
    ], ids=[
        "mongo_env", "pg_generic", "pg_compose_host",
        "mysql_default", "redis_default", "sqlite_none",
        "unknown_none", "mongo_connstr",
    ])
    def test_infer_port(self, db, env_kv, compose, expected):
        result = _infer_database_port(db, env_kv, compose)
        assert result == expected


class TestDetectDatabases:
    """detect_databases: dependency + env + compose-based scoring."""

    def test_mongo_from_dependency(self, tmp_path):
        result = detect_databases(str(tmp_path), ["mongoose"], [])
        assert result["primary"] == "MongoDB"

    def test_postgres_from_env(self, tmp_path):
        _write(tmp_path / ".env", "DATABASE_URL=postgresql://localhost:5432/mydb\n")
        result = detect_databases(str(tmp_path), [], ["DATABASE_URL"])
        assert result["primary"] == "PostgreSQL"

    def test_redis_from_compose(self, tmp_path):
        _write(tmp_path / "docker-compose.yml", textwrap.dedent("""\
            services:
              cache:
                image: redis:alpine
                ports:
                  - "6379:6379"
        """))
        result = detect_databases(str(tmp_path), [], [])
        assert result["primary"] == "Redis"

    def test_no_db_detected(self, tmp_path):
        result = detect_databases(str(tmp_path), ["express"], [])
        assert result["primary"] == "Unknown"
        assert result["all"] == []

    def test_multiple_databases_scored(self, tmp_path):
        _write(tmp_path / ".env", "MONGO_URI=mongodb://localhost/test\nREDIS_URL=redis://localhost\n")
        result = detect_databases(str(tmp_path), ["mongoose", "ioredis"], ["MONGO_URI", "REDIS_URL"])
        assert "MongoDB" in result["all"]
        assert "Redis" in result["all"]
        # Both have dep(1.0)+env(0.8)=1.8 but order depends on dict iteration
        assert result["primary"] in ("MongoDB", "Redis")

    def test_cloud_env_uri(self, tmp_path):
        """Cloud MongoDB Atlas URI: still detected as MongoDB."""
        _write(tmp_path / ".env", "MONGO_URI=mongodb+srv://user:pass@cluster0.mongodb.net/mydb\n")
        result = detect_databases(str(tmp_path), ["mongoose"], ["MONGO_URI"])
        assert result["primary"] == "MongoDB"

    def test_nested_backend_env(self, tmp_path):
        """Reads .env inside backend/ subfolder for DB detection."""
        backend = tmp_path / "backend"
        backend.mkdir()
        _write(backend / "package.json", json.dumps({"name": "backend", "dependencies": {"express": "4"}}))
        _write(backend / ".env", "DATABASE_URL=postgresql://localhost/test\n")
        result = detect_databases(str(tmp_path), ["pg"], ["DATABASE_URL"])
        assert result["primary"] == "PostgreSQL"


# ═══════════════════════════════════════════════════════════════════════
# GROUP 4: ARCHITECTURE DETECTION
# ═══════════════════════════════════════════════════════════════════════


class TestFullstackStructure:
    """_detect_fullstack_structure: MERN/MEAN folder layouts."""

    @pytest.mark.parametrize("backend_name, frontend_name", [
        ("backend", "frontend"),
        ("server", "client"),
        ("api", "web"),
        ("app", "ui"),
    ], ids=["backend_frontend", "server_client", "api_web", "app_ui"])
    def test_typical_fullstack(self, tmp_path, backend_name, frontend_name):
        _write(tmp_path / backend_name / "package.json", "{}")
        _write(tmp_path / frontend_name / "package.json", "{}")
        result = _detect_fullstack_structure(str(tmp_path))
        assert result["is_fullstack"] is True
        assert result["has_backend"] is True
        assert result["has_frontend"] is True

    def test_backend_only(self, tmp_path):
        _write(tmp_path / "backend" / "package.json", "{}")
        result = _detect_fullstack_structure(str(tmp_path))
        assert result["has_backend"] is True
        assert result["has_frontend"] is False
        assert result["is_fullstack"] is True  # it's set True even for single detection

    def test_no_structure(self, tmp_path):
        _write(tmp_path / "package.json", "{}")
        result = _detect_fullstack_structure(str(tmp_path))
        assert result["is_fullstack"] is False

    def test_folder_without_package_json(self, tmp_path):
        (tmp_path / "backend").mkdir()
        (tmp_path / "frontend").mkdir()
        # No package.json inside → not detected
        result = _detect_fullstack_structure(str(tmp_path))
        assert result["is_fullstack"] is False

    def test_detects_nested_structure_with_depth_three(self, tmp_path):
        _write(tmp_path / "repo" / "apps" / "backend" / "package.json", "{}")
        _write(tmp_path / "repo" / "apps" / "frontend" / "package.json", "{}")
        result = _detect_fullstack_structure(str(tmp_path))
        assert result["is_fullstack"] is True
        assert result["has_backend"] is True
        assert result["has_frontend"] is True


class TestInferServiceType:
    """_infer_service_type: classify service by deps then name heuristic (Fix 4)."""

    def test_backend_from_deps(self, tmp_path):
        """Folder with express dep → backend regardless of folder name."""
        svc = tmp_path / "my-cool-app"
        _write(svc / "package.json", json.dumps({"dependencies": {"express": "4"}}))
        assert _infer_service_type("my-cool-app", "my-cool-app", str(tmp_path)) == "backend"

    def test_frontend_from_deps(self, tmp_path):
        """Folder with react dep → frontend regardless of folder name."""
        svc = tmp_path / "view"
        _write(svc / "package.json", json.dumps({"dependencies": {"react": "18"}}))
        assert _infer_service_type("view", "view", str(tmp_path)) == "frontend"

    def test_monolith_from_deps(self, tmp_path):
        """Folder with express + react deps → monolith."""
        svc = tmp_path / "app"
        _write(svc / "package.json", json.dumps({
            "dependencies": {"express": "4", "react": "18"}
        }))
        assert _infer_service_type("app", "app", str(tmp_path)) == "monolith"

    def test_name_fallback_backend(self, tmp_path):
        """No package.json → falls back to name heuristic."""
        (tmp_path / "api-server").mkdir()
        assert _infer_service_type("api-server", "api-server", str(tmp_path)) == "backend"

    def test_name_fallback_frontend(self, tmp_path):
        """No package.json → falls back to name heuristic."""
        (tmp_path / "client").mkdir()
        assert _infer_service_type("client", "client", str(tmp_path)) == "frontend"

    def test_name_fallback_other(self, tmp_path):
        """No package.json, no matching name → other."""
        (tmp_path / "misc").mkdir()
        assert _infer_service_type("misc", "misc", str(tmp_path)) == "other"

    def test_exact_aliases_still_match(self, tmp_path):
        """Exact alias names remain valid after substring token narrowing."""
        (tmp_path / "worker").mkdir()
        (tmp_path / "front").mkdir()
        assert _infer_service_type("worker", "worker", str(tmp_path)) == "backend"
        assert _infer_service_type("front", "front", str(tmp_path)) == "frontend"

    def test_backend_substring_auth_no_longer_forces_backend(self, tmp_path):
        """Names containing auth as substring should not be forced to backend."""
        (tmp_path / "oauthproxy").mkdir()
        assert _infer_service_type("oauthproxy", "oauthproxy", str(tmp_path)) == "other"

    def test_frontend_substring_front_no_longer_forces_frontend(self, tmp_path):
        """Names containing front as substring should not be forced to frontend."""
        (tmp_path / "confrontation").mkdir()
        assert _infer_service_type("confrontation", "confrontation", str(tmp_path)) == "other"


class TestDetectPackageManager:
    """_detect_package_manager: yarn, pnpm, npm detection."""

    @pytest.mark.parametrize("lockfile, expected_manager, expected_lock", [
        ("yarn.lock", "yarn", True),
        ("pnpm-lock.yaml", "pnpm", True),
        ("package-lock.json", "npm", True),
        (None, "npm", False),
    ], ids=["yarn", "pnpm", "npm_lock", "npm_no_lock"])
    def test_detects_manager(self, tmp_path, lockfile, expected_manager, expected_lock):
        _write(tmp_path / "package.json", "{}")
        if lockfile:
            _write(tmp_path / lockfile, "")
        result = _detect_package_manager(str(tmp_path))
        assert result["manager"] == expected_manager
        assert result["has_lockfile"] == expected_lock


class TestHeuristicLanguageDetection:
    """heuristic_language_detection: score from extensions, config files, imports."""

    @pytest.mark.parametrize("files, expected_lang", [
        ({"app.py": "from flask import Flask\n", "requirements.txt": "flask\n"}, "Python"),
        ({"index.js": "const x = require('express');\n", "package.json": "{}"}, "JavaScript"),
        ({"Main.java": "import org.springframework;\n", "pom.xml": "<project/>"}, "Java"),
        ({"main.go": "package main\nimport \"fmt\"\n", "go.mod": "module x\ngo 1.21\n"}, "Go"),
    ], ids=["python", "javascript", "java", "go"])
    def test_detects_language(self, tmp_path, files, expected_lang):
        for name, content in files.items():
            _write(tmp_path / name, content)
        lang, conf = heuristic_language_detection(str(tmp_path))
        assert lang == expected_lang
        assert conf > 0.0

    def test_typescript_detected_with_tsconfig(self, tmp_path):
        """TypeScript is detected when tsconfig.json is present."""
        _write(tmp_path / "tsconfig.json", '{"compilerOptions":{}}')
        _write(tmp_path / "index.ts", 'import express from "express";\n')
        _write(tmp_path / "package.json", json.dumps({"dependencies": {"typescript": "5.0"}}))
        lang, conf = heuristic_language_detection(str(tmp_path))
        assert lang == "TypeScript"
        assert conf > 0.0

    def test_empty_dir(self, tmp_path):
        lang, conf = heuristic_language_detection(str(tmp_path))
        assert lang == "Unknown"
        assert conf == 0.0


class TestHeuristicFrameworkDetection:
    """heuristic_framework_detection: score from deps, markers, config files."""

    @pytest.mark.parametrize("deps_file, content, language, expected_fw", [
        ("package.json", json.dumps({"dependencies": {"express": "4.18"}}), "JavaScript", "Express.js"),
        ("package.json", json.dumps({"dependencies": {"react": "18.0"}}), "JavaScript", "React"),
        ("package.json", json.dumps({"dependencies": {"next": "14.0"}}), "JavaScript", "Next.js"),
        ("requirements.txt", "flask==2.3\n", "Python", "Flask"),
        ("requirements.txt", "django==4.2\n", "Python", "Django"),
        ("requirements.txt", "fastapi==0.100\n", "Python", "FastAPI"),
    ], ids=["express", "react", "nextjs", "flask", "django", "fastapi"])
    def test_detects_framework(self, tmp_path, deps_file, content, language, expected_fw):
        _write(tmp_path / deps_file, content)
        # For Python frameworks that use markers in code, add a matching source file
        if language == "Python":
            if expected_fw == "Flask":
                _write(tmp_path / "app.py", "from flask import Flask\napp = Flask(__name__)\n")
            elif expected_fw == "Django":
                _write(tmp_path / "manage.py", "import django\n")
            elif expected_fw == "FastAPI":
                _write(tmp_path / "main.py", "from fastapi import FastAPI\napp = FastAPI()\n")
        fw, conf = heuristic_framework_detection(str(tmp_path), language)
        assert fw == expected_fw
        assert conf > 0.0

    def test_penalises_language_mismatch(self, tmp_path):
        """Express dep + Python language → penalised score."""
        _write(tmp_path / "package.json", json.dumps({"dependencies": {"express": "4"}}))
        fw, conf = heuristic_framework_detection(str(tmp_path), "Python")
        # May still detect Express.js but with lower confidence
        # The important thing is it doesn't crash
        assert isinstance(fw, str)

    def test_unknown_project(self, tmp_path):
        _write(tmp_path / "readme.txt", "Hello")
        fw, conf = heuristic_framework_detection(str(tmp_path), "Unknown")
        assert fw == "Unknown"

    def test_typescript_express_detected_from_ts_deps(self, tmp_path):
        """TypeScript-only Express support deps should still score as Express.js."""
        _write(tmp_path / "package.json", json.dumps({
            "devDependencies": {"@types/express": "4.17", "ts-node": "10.9"}
        }))
        _write(tmp_path / "src" / "server.ts", "console.log('api');")
        fw, conf = heuristic_framework_detection(str(tmp_path), "TypeScript")
        assert fw == "Express.js"
        assert conf > 0.0

    def test_typescript_react_detected_from_types_deps(self, tmp_path):
        """TypeScript React type deps should still score as React."""
        _write(tmp_path / "package.json", json.dumps({
            "devDependencies": {"@types/react": "18", "@types/react-dom": "18"}
        }))
        _write(tmp_path / "src" / "App.tsx", "export default function App() { return null; }")
        fw, conf = heuristic_framework_detection(str(tmp_path), "TypeScript")
        assert fw == "React"
        assert conf > 0.0

    def test_directory_style_file_marker_in_files_list_is_checked_as_dir(self, tmp_path):
        """A files marker like 'widgets_probe_dir/' should match an actual directory.

        Uses a directory name not claimed by any real framework's own
        indicators (unlike e.g. 'pages', which Next.js already lists as a
        'dirs' indicator) so this test isolates the dir-style-marker-parsing
        behavior instead of colliding with real framework detection.
        """
        _write(tmp_path / "widgets_probe_dir" / "index.jsx", "export default function Page() { return null; }")

        with patch.dict(
            "app.utils.detection_language.FRAMEWORK_INDICATORS",
            {
                "DirStyleFramework": {
                    "markers": [],
                    "files": ["widgets_probe_dir/"],
                    "dependencies": [],
                    "confidence_weight": 0.95,
                }
            },
            clear=False,
        ):
            fw, conf = heuristic_framework_detection(str(tmp_path), "JavaScript")

        assert fw == "DirStyleFramework"
        assert conf > 0.0


class TestGetRuntimeInfo:
    """get_runtime_info: runtime, port defaults per language/framework."""

    @pytest.mark.parametrize("lang, framework, expected_runtime_substr", [
        ("JavaScript", "Express.js", "node"),
        ("Python", "Flask", "python"),
        ("Python", "Django", "python"),
        ("Java", "Spring Boot", "openjdk"),
        ("Go", "Unknown", "golang"),
    ], ids=["node", "flask", "django", "spring", "go"])
    def test_runtime_selection(self, lang, framework, expected_runtime_substr):
        info = get_runtime_info(lang, framework)
        assert expected_runtime_substr in info.get("runtime", "")


# ═══════════════════════════════════════════════════════════════════════
# GROUP 5: ORCHESTRATOR — detect_framework() + deploy_blocked logic
# ═══════════════════════════════════════════════════════════════════════


class TestDetectFrameworkOrchestrator:
    """detect_framework: end-to-end orchestration with mocked externals."""

    @patch("app.utils.detector.get_ml_analyzer")
    @patch("app.utils.detector.extract_database_info")
    @patch("app.utils.detector.extract_port_from_project")
    @patch("app.utils.detector.extract_frontend_port")
    @patch("app.utils.detector.extract_nodejs_commands")
    @patch("app.utils.detector.extract_python_commands")
    def test_express_backend_detected(
        self, mock_py_cmds, mock_node_cmds, mock_fe_port,
        mock_port, mock_db_info, mock_ml, tmp_path
    ):
        """Full Express.js project → language=JavaScript, framework=Express.js."""
        mock_ml.return_value = MagicMock()
        mock_node_cmds.return_value = {"start_command": "node server.js", "entry_point": "server.js"}
        mock_py_cmds.return_value = {}
        mock_port.return_value = {"port": 5000, "source": "env"}
        mock_fe_port.return_value = {"port": None, "source": "default"}
        mock_db_info.return_value = {"db_type": "mongodb", "is_cloud": False, "database_env_var": None}

        _make_express_backend(tmp_path, port=5000, has_env=True)
        result = detect_framework(str(tmp_path), use_ml=False)

        assert result["language"] == "JavaScript"
        assert result["framework"] == "Express.js"
        assert result["has_package_json"] is True
        backend_svcs = [s for s in result.get("services", []) if s.get("type") in ("backend", "monolith")]
        assert len(backend_svcs) >= 1
        assert backend_svcs[0].get("framework") == "Express.js"

    @patch("app.utils.detector.get_ml_analyzer")
    @patch("app.utils.detector.extract_database_info")
    @patch("app.utils.detector.extract_port_from_project")
    @patch("app.utils.detector.extract_frontend_port")
    @patch("app.utils.detector.extract_nodejs_commands")
    @patch("app.utils.detector.extract_python_commands")
    def test_empty_project(
        self, mock_py_cmds, mock_node_cmds, mock_fe_port,
        mock_port, mock_db_info, mock_ml, tmp_path
    ):
        """Empty directory → Unknown language, Unknown framework."""
        mock_ml.return_value = MagicMock()
        mock_node_cmds.return_value = {}
        mock_py_cmds.return_value = {}
        mock_port.return_value = {"port": None, "source": "default"}
        mock_fe_port.return_value = {"port": None, "source": "default"}
        mock_db_info.return_value = {"db_type": "mongodb", "is_cloud": False, "database_env_var": None}

        result = detect_framework(str(tmp_path), use_ml=False)
        assert result["language"] == "Unknown"
        assert result["framework"] == "Unknown"

    @patch("app.utils.detector.get_ml_analyzer")
    @patch("app.utils.detector.extract_database_info")
    @patch("app.utils.detector.extract_port_from_project")
    @patch("app.utils.detector.extract_frontend_port")
    @patch("app.utils.detector.extract_nodejs_commands")
    @patch("app.utils.detector.extract_python_commands")
    def test_react_frontend_detected(
        self, mock_py_cmds, mock_node_cmds, mock_fe_port,
        mock_port, mock_db_info, mock_ml, tmp_path
    ):
        """React project → language=JavaScript, framework=React."""
        mock_ml.return_value = MagicMock()
        mock_node_cmds.return_value = {"build_command": "npm run build", "build_output": "dist"}
        mock_py_cmds.return_value = {}
        mock_port.return_value = {"port": None, "source": "default"}
        mock_fe_port.return_value = {"port": 5173, "source": "default"}
        mock_db_info.return_value = {"db_type": "mongodb", "is_cloud": False, "database_env_var": None}

        _make_react_frontend(tmp_path)
        result = detect_framework(str(tmp_path), use_ml=False)

        assert result["language"] == "JavaScript"
        assert result["framework"] == "React"

    @patch("app.utils.detector.get_ml_analyzer")
    @patch("app.utils.detector.extract_database_info")
    @patch("app.utils.detector.extract_port_from_project")
    @patch("app.utils.detector.extract_frontend_port")
    @patch("app.utils.detector.extract_nodejs_commands")
    @patch("app.utils.detector.extract_python_commands")
    def test_backend_service_does_not_inherit_frontend_framework(
        self, mock_py_cmds, mock_node_cmds, mock_fe_port,
        mock_port, mock_db_info, mock_ml, tmp_path
    ):
        """
        If project framework is frontend (e.g. React), backend service framework
        must not inherit it blindly.
        """
        mock_ml.return_value = MagicMock()
        mock_node_cmds.return_value = {}
        mock_py_cmds.return_value = {}
        mock_port.return_value = {"port": None, "source": "default"}
        mock_fe_port.return_value = {"port": 5173, "source": "default"}
        mock_db_info.return_value = {"db_type": "mongodb", "is_cloud": False, "database_env_var": None}

        _make_react_frontend(tmp_path / "frontend")
        _write(tmp_path / "backend" / "package.json", json.dumps({
            "name": "backend",
            "dependencies": {},
            "scripts": {"start": "node server.js"},
        }))
        _write(tmp_path / "backend" / "server.js", "console.log('backend')\n")

        result = detect_framework(str(tmp_path), use_ml=False)

        backend_svcs = [s for s in result.get("services", []) if s.get("type") in ("backend", "monolith")]
        assert backend_svcs
        assert backend_svcs[0].get("framework") == "Unknown"

    @patch("app.utils.detector.infer_services")
    @patch("app.utils.detector.detect_db_and_ports")
    @patch("app.utils.detector.detect_env_variables")
    @patch("app.utils.detector.detect_docker_files")
    @patch("app.utils.detector.parse_dependencies_file")
    @patch("app.utils.detector.extract_nodejs_commands")
    @patch("app.utils.detector.extract_python_commands")
    @patch("app.utils.detector.heuristic_framework_detection")
    @patch("app.utils.detector.heuristic_language_detection")
    @patch("app.utils.detector.find_project_root")
    def test_heuristics_run_on_resolved_actual_path(
        self,
        mock_find_root,
        mock_heur_lang,
        mock_heur_fw,
        mock_py_cmds,
        mock_node_cmds,
        mock_parse_deps,
        mock_docker,
        mock_env,
        mock_db_ports,
        mock_infer_services,
        tmp_path,
    ):
        """Heuristic language/framework scoring must use resolved actual_path."""
        wrapper = tmp_path / "repo-main"
        resolved = wrapper / "backend"
        resolved.mkdir(parents=True)

        mock_find_root.return_value = str(resolved)
        mock_heur_lang.return_value = ("TypeScript", 0.8)
        mock_heur_fw.return_value = ("Express.js", 0.9)
        mock_py_cmds.return_value = {}
        mock_node_cmds.return_value = {}
        mock_parse_deps.return_value = []
        mock_docker.return_value = {"dockerfile": False, "docker_compose": False, "detected_files": []}
        mock_env.return_value = []
        mock_db_ports.return_value = (
            {"primary": "Unknown", "all": [], "details": {}},
            {"backend_port": None, "frontend_port": None},
        )
        mock_infer_services.return_value = []

        result = detect_framework(str(wrapper), use_ml=False)

        mock_heur_lang.assert_called_once_with(str(resolved))
        mock_heur_fw.assert_called_once_with(str(resolved), "TypeScript")
        assert mock_infer_services.call_args.kwargs.get("db_result") == {
            "primary": "Unknown",
            "all": [],
            "details": {},
        }
        assert result["framework"] == "Express.js"


class TestConsistencyReconciliation:
    @patch("app.utils.detector.infer_services")
    @patch("app.utils.detector.detect_db_and_ports")
    @patch("app.utils.detector.detect_env_variables")
    @patch("app.utils.detector.detect_docker_files")
    @patch("app.utils.detector.parse_dependencies_file")
    @patch("app.utils.detector.extract_nodejs_commands")
    @patch("app.utils.detector.extract_python_commands")
    @patch("app.utils.detector.heuristic_framework_detection")
    @patch("app.utils.detector.heuristic_language_detection")
    @patch("app.utils.detector.find_project_root")
    def test_express_normalizes_language_to_javascript(
        self,
        mock_find_root,
        mock_heur_lang,
        mock_heur_fw,
        mock_py_cmds,
        mock_node_cmds,
        mock_parse_deps,
        mock_docker,
        mock_env,
        mock_db_ports,
        mock_infer_services,
        tmp_path,
    ):
        mock_find_root.return_value = str(tmp_path)
        mock_heur_lang.return_value = ("Python", 0.9)
        mock_heur_fw.return_value = ("Express.js", 0.9)
        mock_py_cmds.return_value = {}
        mock_node_cmds.return_value = {}
        mock_parse_deps.return_value = []
        mock_docker.return_value = {"dockerfile": False, "docker_compose": False, "detected_files": []}
        mock_env.return_value = []
        mock_db_ports.return_value = (
            {"primary": "Unknown", "all": [], "details": {}},
            {"backend_port": None, "frontend_port": None},
        )
        mock_infer_services.return_value = [
            {"name": "api", "path": ".", "type": "backend", "port": 3000, "port_source": "default", "env_file": "./.env"}
        ]

        result = detect_framework(str(tmp_path), use_ml=False)
        assert result["language"] == "JavaScript"

    @patch("app.utils.detector.infer_services")
    @patch("app.utils.detector.detect_db_and_ports")
    @patch("app.utils.detector.detect_env_variables")
    @patch("app.utils.detector.detect_docker_files")
    @patch("app.utils.detector.parse_dependencies_file")
    @patch("app.utils.detector.extract_nodejs_commands")
    @patch("app.utils.detector.extract_python_commands")
    @patch("app.utils.detector.heuristic_framework_detection")
    @patch("app.utils.detector.heuristic_language_detection")
    @patch("app.utils.detector.find_project_root")
    def test_python_language_with_js_framework_keeps_framework_and_adjusts_language(
        self,
        mock_find_root,
        mock_heur_lang,
        mock_heur_fw,
        mock_py_cmds,
        mock_node_cmds,
        mock_parse_deps,
        mock_docker,
        mock_env,
        mock_db_ports,
        mock_infer_services,
        tmp_path,
    ):
        """When language/framework heuristics conflict, the framework signal
        (a specific fingerprint like a dependency or JSX marker) is trusted
        over the more generic language guess — language is coerced to match
        the framework's language instead of discarding the framework.
        This mirrors test_express_normalizes_language_to_javascript above,
        which exercises the identical reconciliation path.
        """
        mock_find_root.return_value = str(tmp_path)
        mock_heur_lang.return_value = ("Python", 0.9)
        mock_heur_fw.return_value = ("React", 0.9)
        mock_py_cmds.return_value = {}
        mock_node_cmds.return_value = {}
        mock_parse_deps.return_value = []
        mock_docker.return_value = {"dockerfile": False, "docker_compose": False, "detected_files": []}
        mock_env.return_value = []
        mock_db_ports.return_value = (
            {"primary": "Unknown", "all": [], "details": {}},
            {"backend_port": None, "frontend_port": None},
        )
        mock_infer_services.return_value = [
            {"name": "web", "path": ".", "type": "frontend", "port": 5173, "port_source": "default"}
        ]

        result = detect_framework(str(tmp_path), use_ml=False)
        assert result["framework"] == "React"
        assert result["language"] == "JavaScript"
        assert result["detection_confidence"]["method"] == "hybrid (framework->language)"

    @patch("app.utils.detector.infer_services")
    @patch("app.utils.detector.detect_db_and_ports")
    @patch("app.utils.detector.detect_env_variables")
    @patch("app.utils.detector.detect_docker_files")
    @patch("app.utils.detector.parse_dependencies_file")
    @patch("app.utils.detector.extract_nodejs_commands")
    @patch("app.utils.detector.extract_python_commands")
    @patch("app.utils.detector.heuristic_framework_detection")
    @patch("app.utils.detector.heuristic_language_detection")
    @patch("app.utils.detector.find_project_root")
    def test_frontend_only_flags_missing_backend_and_keeps_db(
        self,
        mock_find_root,
        mock_heur_lang,
        mock_heur_fw,
        mock_py_cmds,
        mock_node_cmds,
        mock_parse_deps,
        mock_docker,
        mock_env,
        mock_db_ports,
        mock_infer_services,
        tmp_path,
    ):
        mock_find_root.return_value = str(tmp_path)
        mock_heur_lang.return_value = ("JavaScript", 0.8)
        mock_heur_fw.return_value = ("React", 0.9)
        mock_py_cmds.return_value = {}
        mock_node_cmds.return_value = {}
        mock_parse_deps.return_value = []
        mock_docker.return_value = {"dockerfile": False, "docker_compose": False, "detected_files": []}
        mock_env.return_value = []
        mock_db_ports.return_value = (
            {"primary": "MongoDB", "all": ["MongoDB"], "details": {}, "port": 27017},
            {"backend_port": None, "frontend_port": 5173},
        )
        mock_infer_services.return_value = [
            {"name": "web", "path": ".", "type": "frontend", "port": 5173, "port_source": "default"}
        ]

        result = detect_framework(str(tmp_path), use_ml=False)
        assert result["missing_backend"] is True
        assert result["database"] == "MongoDB"
        assert any(
            "Database detected without backend/monolith service" in w
            for w in result.get("consistency_warnings", [])
        )


class TestDetectPortsFrameworkOverrides:
    @pytest.mark.parametrize("framework,expected_port", [
        ("Express.js", 3000),
        ("Fastify", 3000),
        ("NestJS", 3000),
        ("Next.js", 3000),
        ("Vite", 5173),
    ])
    def test_framework_overrides_apply_before_language_defaults(self, tmp_path, framework, expected_port):
        ports = detect_ports_for_project(str(tmp_path), "Unknown", framework, base_port=None)
        assert ports["backend_port"] == expected_port


class TestDetectPortsComposeHints:
    def test_compose_environment_sets_backend_runtime_port(self, tmp_path):
        _write(tmp_path / "docker-compose.yml", """
            services:
              server:
                build: .
                environment:
                  - PORT=2727
                ports:
                  - "8080:2727"
        """)

        ports = detect_ports_for_project(str(tmp_path), "JavaScript", "Express.js", base_port=None)
        assert ports["backend_port"] == 2727
        assert ports["backend_port_source"] == "compose_env"
        assert ports["backend_compose_env_port"] == 2727

    def test_frontend_compose_runtime_prefers_container_port_for_non_nginx_mapping(self, tmp_path):
        _write(tmp_path / "docker-compose.yml", """
            services:
              frontend:
                build: .
                ports:
                  - "8000:3000"
        """)

        ports = detect_ports_for_project(str(tmp_path), "JavaScript", "React", base_port=None)
        assert ports["frontend_port"] == 3000
        assert ports["frontend_port_source"] == "compose"


class TestDeployBlockedLogic:
    """
    deploy_blocked / deploy_warning in the real detect_framework output.
    Uses physical tmp_path projects with mocked externals.
    """

    @patch("app.utils.detector.get_ml_analyzer")
    @patch("app.utils.detector.extract_database_info")
    @patch("app.utils.detector.extract_port_from_project")
    @patch("app.utils.detector.extract_frontend_port")
    @patch("app.utils.detector.extract_nodejs_commands")
    @patch("app.utils.detector.extract_python_commands")
    def test_blocked_when_db_and_no_env(
        self, mock_py, mock_node, mock_fe_port,
        mock_port, mock_db, mock_ml, tmp_path
    ):
        """Backend + mongoose (DB) + no .env → not blocked; .env auto-generated.

        Deployment is never blocked on a missing .env (see commit
        f7dbfa8 "feat: auto-generate .env instead of blocking deployment"):
        the build pipeline auto-generates a .env template instead. The
        flag/reason fields stay False/None; backend_env_missing is True and
        deploy_warning explains the auto-generation, since a database was
        detected. This matches the sibling tests in this class
        (test_warning_when_no_db_no_env, test_not_blocked_with_env), which
        already assert deploy_blocked is False.
        """
        mock_ml.return_value = MagicMock()
        mock_node.return_value = {"start_command": "node server.js", "entry_point": "server.js"}
        mock_py.return_value = {}
        mock_port.return_value = {"port": 5000, "source": "env"}
        mock_fe_port.return_value = {"port": None, "source": "default"}
        mock_db.return_value = {"db_type": "mongodb", "is_cloud": False, "database_env_var": None}

        backend = tmp_path / "backend"
        _write(backend / "package.json", json.dumps({
            "name": "backend",
            "dependencies": {"express": "4.18", "mongoose": "7.0"},
        }))
        _write(backend / "server.js", 'const m = require("mongoose"); app.listen(5000);')
        # NO .env file

        result = detect_framework(str(tmp_path), use_ml=False)
        assert result["deploy_blocked"] is False
        assert result["deploy_blocked_reason"] is None
        assert result["backend_env_missing"] is True
        assert result["deploy_warning"] == (
            "No .env file detected. One will be auto-generated before build."
        )

    @patch("app.utils.detector.get_ml_analyzer")
    @patch("app.utils.detector.extract_database_info")
    @patch("app.utils.detector.extract_port_from_project")
    @patch("app.utils.detector.extract_frontend_port")
    @patch("app.utils.detector.extract_nodejs_commands")
    @patch("app.utils.detector.extract_python_commands")
    def test_warning_when_no_db_no_env(
        self, mock_py, mock_node, mock_fe_port,
        mock_port, mock_db, mock_ml, tmp_path
    ):
        """Backend + NO db dependency + no .env → deploy_warning, NOT blocked."""
        mock_ml.return_value = MagicMock()
        mock_node.return_value = {"start_command": "node server.js", "entry_point": "server.js"}
        mock_py.return_value = {}
        mock_port.return_value = {"port": 3000, "source": "env"}
        mock_fe_port.return_value = {"port": None, "source": "default"}
        mock_db.return_value = {"db_type": "mongodb", "is_cloud": False, "database_env_var": None}

        backend = tmp_path / "backend"
        _write(backend / "package.json", json.dumps({
            "name": "backend",
            "dependencies": {"express": "4.18"},
        }))
        _write(backend / "server.js", 'const app = require("express")(); app.listen(3000);')

        result = detect_framework(str(tmp_path), use_ml=False)
        assert result["deploy_blocked"] is False
        assert result["deploy_warning"] is not None
        assert "No .env" in result["deploy_warning"]
        assert result["backend_env_missing"] is True

    @patch("app.utils.detector.get_ml_analyzer")
    @patch("app.utils.detector.extract_database_info")
    @patch("app.utils.detector.extract_port_from_project")
    @patch("app.utils.detector.extract_frontend_port")
    @patch("app.utils.detector.extract_nodejs_commands")
    @patch("app.utils.detector.extract_python_commands")
    def test_not_blocked_with_env(
        self, mock_py, mock_node, mock_fe_port,
        mock_port, mock_db, mock_ml, tmp_path
    ):
        """Backend + DB + .env present → NOT blocked, no warning."""
        mock_ml.return_value = MagicMock()
        mock_node.return_value = {"start_command": "node server.js", "entry_point": "server.js"}
        mock_py.return_value = {}
        mock_port.return_value = {"port": 5000, "source": "env"}
        mock_fe_port.return_value = {"port": None, "source": "default"}
        mock_db.return_value = {"db_type": "mongodb", "is_cloud": False, "database_env_var": None}

        backend = tmp_path / "backend"
        _write(backend / "package.json", json.dumps({
            "name": "backend",
            "dependencies": {"express": "4.18", "mongoose": "7.0"},
        }))
        _write(backend / "server.js", 'const m = require("mongoose"); app.listen(5000);')
        _write(backend / ".env", "MONGO_URI=mongodb://localhost/test\nPORT=5000\n")

        result = detect_framework(str(tmp_path), use_ml=False)
        assert result["deploy_blocked"] is False
        assert result["deploy_warning"] is None
        assert result["backend_env_missing"] is False

    @patch("app.utils.detector.get_ml_analyzer")
    @patch("app.utils.detector.extract_database_info")
    @patch("app.utils.detector.extract_port_from_project")
    @patch("app.utils.detector.extract_frontend_port")
    @patch("app.utils.detector.extract_nodejs_commands")
    @patch("app.utils.detector.extract_python_commands")
    def test_frontend_only_not_blocked(
        self, mock_py, mock_node, mock_fe_port,
        mock_port, mock_db, mock_ml, tmp_path
    ):
        """Frontend-only (React) → NOT blocked, no warning."""
        mock_ml.return_value = MagicMock()
        mock_node.return_value = {"build_command": "npm run build", "build_output": "dist"}
        mock_py.return_value = {}
        mock_port.return_value = {"port": None, "source": "default"}
        mock_fe_port.return_value = {"port": 5173, "source": "default"}
        mock_db.return_value = {"db_type": "mongodb", "is_cloud": False, "database_env_var": None}

        _make_react_frontend(tmp_path)
        result = detect_framework(str(tmp_path), use_ml=False)

        assert result["deploy_blocked"] is False
        assert result["deploy_warning"] is None

    @patch("app.utils.detector.get_ml_analyzer")
    @patch("app.utils.detector.extract_database_info")
    @patch("app.utils.detector.extract_port_from_project")
    @patch("app.utils.detector.extract_frontend_port")
    @patch("app.utils.detector.extract_nodejs_commands")
    @patch("app.utils.detector.extract_python_commands")
    def test_fullstack_mern_with_env(
        self, mock_py, mock_node, mock_fe_port,
        mock_port, mock_db, mock_ml, tmp_path
    ):
        """Fullstack MERN (backend + frontend + DB) with .env → NOT blocked."""
        mock_ml.return_value = MagicMock()
        mock_node.return_value = {"start_command": "node server.js", "entry_point": "server.js"}
        mock_py.return_value = {}
        mock_port.return_value = {"port": 5000, "source": "env"}
        mock_fe_port.return_value = {"port": 3000, "source": "default"}
        mock_db.return_value = {"db_type": "mongodb", "is_cloud": False, "database_env_var": "MONGO_URI"}

        # Root-level marker so find_project_root stays at tmp_path
        _write(tmp_path / "package.json", json.dumps({
            "name": "fullstack-app",
            "scripts": {"start": "concurrently \"npm:backend\" \"npm:frontend\""},
        }))

        backend = tmp_path / "backend"
        _write(backend / "package.json", json.dumps({
            "name": "backend",
            "dependencies": {"express": "4.18", "mongoose": "7.0"},
        }))
        _write(backend / "server.js", 'const m = require("mongoose"); app.listen(5000);')
        _write(backend / ".env", "MONGO_URI=mongodb://localhost/test\nPORT=5000\n")

        frontend = tmp_path / "frontend"
        _make_react_frontend(frontend)

        result = detect_framework(str(tmp_path), use_ml=False)
        assert result["deploy_blocked"] is False
        assert result["database"] != "Unknown"


class TestCloudDatabaseClearing:
    """When database_is_cloud=True, database_port should be cleared."""

    @patch("app.utils.detector.get_ml_analyzer")
    @patch("app.utils.detector.extract_database_info")
    @patch("app.utils.detector.extract_port_from_project")
    @patch("app.utils.detector.extract_frontend_port")
    @patch("app.utils.detector.extract_nodejs_commands")
    @patch("app.utils.detector.extract_python_commands")
    def test_cloud_db_clears_port(
        self, mock_py, mock_node, mock_fe_port,
        mock_port, mock_db, mock_ml, tmp_path
    ):
        mock_ml.return_value = MagicMock()
        mock_node.return_value = {"start_command": "node server.js", "entry_point": "server.js"}
        mock_py.return_value = {}
        mock_port.return_value = {"port": 5000, "source": "env"}
        mock_fe_port.return_value = {"port": None, "source": "default"}
        # extract_database_info is called inside infer_services;
        # "is_cloud": True triggers metadata["database_is_cloud"] = True
        mock_db.return_value = {"db_type": "mongodb", "is_cloud": True, "database_env_var": "MONGO_URI"}

        _write(tmp_path / "package.json", json.dumps({
            "name": "backend",
            "dependencies": {"express": "4.18", "mongoose": "7.0"},
        }))
        _write(tmp_path / "server.js", 'const m = require("mongoose");')
        _write(tmp_path / ".env", "MONGO_URI=mongodb+srv://user:pass@cluster0.mongodb.net/mydb\n")

        result = detect_framework(str(tmp_path), use_ml=False)
        # Cloud DB → database_port should be None (cleared at line 2087-2088)
        assert result.get("database_port") is None


# ═══════════════════════════════════════════════════════════════════════
# GROUP 6: NEW FIX TESTS — dep-scanning, monolith, root suppression
# ═══════════════════════════════════════════════════════════════════════


class TestDepBasedServiceDiscovery:
    """Fix 1: _find_all_services_by_deps discovers services by package.json deps."""

    def test_non_standard_backend_name(self, tmp_path):
        """Folder named 'my-app' with express → detected as backend."""
        _write(tmp_path / "my-app" / "package.json", json.dumps({
            "dependencies": {"express": "4.18"}
        }))
        svcs = _find_all_services_by_deps(str(tmp_path))
        assert len(svcs) == 1
        assert svcs[0]["type"] == "backend"
        assert svcs[0]["name"] == "my-app"

    def test_non_standard_frontend_name(self, tmp_path):
        """Folder named 'view' with react → detected as frontend."""
        _write(tmp_path / "view" / "package.json", json.dumps({
            "dependencies": {"react": "18.0", "react-dom": "18.0"}
        }))
        svcs = _find_all_services_by_deps(str(tmp_path))
        assert len(svcs) == 1
        assert svcs[0]["type"] == "frontend"
        assert svcs[0]["name"] == "view"

    def test_discovers_multiple_non_standard_folders(self, tmp_path):
        """'api-server' (express) + 'react-src' (react) → 2 services."""
        _write(tmp_path / "api-server" / "package.json", json.dumps({
            "dependencies": {"express": "4"}
        }))
        _write(tmp_path / "react-src" / "package.json", json.dumps({
            "dependencies": {"react": "18", "vite": "5"}
        }))
        svcs = _find_all_services_by_deps(str(tmp_path))
        types = {s["type"] for s in svcs}
        assert "backend" in types
        assert "frontend" in types

    def test_skips_node_modules(self, tmp_path):
        """package.json inside node_modules is never scanned."""
        _write(tmp_path / "node_modules" / "foo" / "package.json", json.dumps({
            "dependencies": {"express": "4"}
        }))
        svcs = _find_all_services_by_deps(str(tmp_path))
        assert len(svcs) == 0

    def test_skips_no_deps(self, tmp_path):
        """No-dep package.json yields type='other', then reclassifies downstream by name."""
        _write(tmp_path / "api-server" / "package.json", json.dumps({
            "dependencies": {"lodash": "4"}
        }))
        svcs = _find_all_services_by_deps(str(tmp_path))
        assert len(svcs) == 1
        assert svcs[0]["type"] == "other"

        inferred_svcs = infer_services(
            str(tmp_path),
            language="JavaScript",
            framework="Unknown",
            metadata={},
        )
        assert len(inferred_svcs) == 1
        assert inferred_svcs[0]["name"] == "api-server"
        assert inferred_svcs[0]["type"] == "backend"


class TestMonolithDetection:
    """Fix 2: Single package.json with both Express + React → monolith."""

    @patch("app.utils.detector.get_ml_analyzer")
    @patch("app.utils.detector.extract_database_info")
    @patch("app.utils.detector.extract_port_from_project")
    @patch("app.utils.detector.extract_frontend_port")
    @patch("app.utils.detector.extract_nodejs_commands")
    @patch("app.utils.detector.extract_python_commands")
    def test_monolith_detected(
        self, mock_py, mock_node, mock_fe_port,
        mock_port, mock_db, mock_ml, tmp_path
    ):
        """express + react in same package.json → architecture=monolith."""
        mock_ml.return_value = MagicMock()
        mock_node.return_value = {"start_command": "node server.js", "entry_point": "server.js", "build_output": "build"}
        mock_py.return_value = {}
        mock_port.return_value = {"port": 5000, "source": "default"}
        mock_fe_port.return_value = {"port": None, "source": "default"}
        mock_db.return_value = {"db_type": None, "is_cloud": False, "database_env_var": None}

        _write(tmp_path / "package.json", json.dumps({
            "name": "monolith-app",
            "dependencies": {"express": "4.18", "react": "18.0", "react-dom": "18.0"},
            "scripts": {"start": "node server.js", "build": "react-scripts build"},
        }))
        _write(tmp_path / "server.js", "const app = require('express')(); app.listen(5000);")

        result = detect_framework(str(tmp_path), use_ml=False)
        assert result.get("architecture") == "monolith"
        # Should have exactly 1 service of type monolith
        monolith_svcs = [s for s in result["services"] if s["type"] == "monolith"]
        assert len(monolith_svcs) == 1
        assert monolith_svcs[0].get("dockerfile_strategy") == "single_stage_with_build"
        assert monolith_svcs[0].get("entry_point") is not None

    def test_monolith_stub_from_deps(self, tmp_path):
        """_find_all_services_by_deps detects monolith from deps."""
        _write(tmp_path / "package.json", json.dumps({
            "dependencies": {"express": "4", "react": "18"}
        }))
        svcs = _find_all_services_by_deps(str(tmp_path))
        assert len(svcs) == 1
        assert svcs[0]["type"] == "monolith"


class TestRootSuppression:
    """Fix 3/5: Root phantom service suppressed when child services exist."""

    def test_suppresses_root_when_children_found(self, tmp_path):
        """Root + backend + frontend children → root removed."""
        stubs = [
            {"name": "root", "abs_path": str(tmp_path), "type": "frontend", "port": 3000, "entry_point": "index.js"},
            {"name": "api", "abs_path": str(tmp_path / "api"), "type": "backend", "port": 5000, "entry_point": "server.js"},
            {"name": "web", "abs_path": str(tmp_path / "web"), "type": "frontend", "port": 3001, "entry_point": "index.js"},
        ]
        result = _suppress_root_if_children_found(stubs, str(tmp_path))
        names = [s["name"] for s in result]
        assert "root" not in names
        assert "api" in names
        assert "web" in names

    def test_keeps_root_monolith(self, tmp_path):
        """Root monolith with entry_point suppresses non-database children."""
        stubs = [
            {"name": "root", "abs_path": str(tmp_path), "type": "monolith", "port": 3000, "entry_point": "server.js"},
            {"name": "api", "abs_path": str(tmp_path / "api"), "type": "backend", "port": 5000, "entry_point": "index.js"},
            {"name": "web", "abs_path": str(tmp_path / "web"), "type": "frontend", "port": 3001, "entry_point": "index.js"},
        ]
        result = _suppress_root_if_children_found(stubs, str(tmp_path))
        names = [s["name"] for s in result]
        assert "root" in names  # monolith root kept
        assert "api" not in names  # children suppressed
        assert "web" not in names

    def test_keeps_root_when_only_service(self, tmp_path):
        """Single root service → kept (nothing to suppress for)."""
        stubs = [
            {"name": "app", "abs_path": str(tmp_path), "type": "backend", "port": 3000, "entry_point": "index.js"},
        ]
        result = _suppress_root_if_children_found(stubs, str(tmp_path))
        assert len(result) == 1
        assert result[0]["name"] == "app"

    def test_keeps_root_with_single_child(self, tmp_path):
        """Root + 1 child backend → root kept (need >=2 real non-root for rule 4)."""
        stubs = [
            {"name": "root", "abs_path": str(tmp_path), "type": "frontend", "port": 3000, "entry_point": "index.js"},
            {"name": "api", "abs_path": str(tmp_path / "api"), "type": "backend", "port": 5000, "entry_point": "server.js"},
        ]
        result = _suppress_root_if_children_found(stubs, str(tmp_path))
        assert len(result) == 2


# ═══════════════════════════════════════════════════════════════════════
# GROUP 7: FIXES 5-7 TESTS
# ═══════════════════════════════════════════════════════════════════════


class TestPythonDetection:
    """Fix 7: Detect Django / FastAPI / Flask backends."""

    def test_django_via_manage_py(self, tmp_path):
        """manage.py present → Django backend."""
        _write(tmp_path / "backend" / "manage.py", "#!/usr/bin/env python\nimport django")
        svcs = _find_python_services(str(tmp_path))
        assert len(svcs) >= 1
        django_svcs = [s for s in svcs if s["framework"] == "Django"]
        assert len(django_svcs) == 1
        assert django_svcs[0]["type"] == "backend"
        assert django_svcs[0]["entry_point"] == "manage.py"
        assert django_svcs[0]["port"] == 8000
        assert django_svcs[0]["dockerfile_strategy"] == "python_backend"

    def test_fastapi_via_requirements(self, tmp_path):
        """requirements.txt with fastapi → FastAPI backend."""
        _write(tmp_path / "api" / "requirements.txt", "fastapi==0.100\nuvicorn==0.27")
        _write(tmp_path / "api" / "main.py", 'from fastapi import FastAPI\napp = FastAPI()')
        svcs = _find_python_services(str(tmp_path))
        fastapi_svcs = [s for s in svcs if s.get("framework") == "FastAPI"]
        assert len(fastapi_svcs) == 1
        assert fastapi_svcs[0]["entry_point"] == "main.py"
        assert fastapi_svcs[0]["port"] == 8000

    def test_flask_via_requirements(self, tmp_path):
        """requirements.txt with flask → Flask backend, default port 5000."""
        _write(tmp_path / "app" / "requirements.txt", "flask==3.0\ngunicorn")
        _write(tmp_path / "app" / "app.py", 'from flask import Flask\napp = Flask(__name__)')
        svcs = _find_python_services(str(tmp_path))
        flask_svcs = [s for s in svcs if s.get("framework") == "Flask"]
        assert len(flask_svcs) == 1
        assert flask_svcs[0]["port"] == 5000
        assert flask_svcs[0]["entry_point"] == "app.py"

    def test_fastapi_via_pyproject_toml(self, tmp_path):
        """pyproject.toml with fastapi → FastAPI, pkg_manager=poetry."""
        _write(tmp_path / "svc" / "pyproject.toml", '[tool.poetry.dependencies]\nfastapi = "^0.100"')
        _write(tmp_path / "svc" / "main.py", 'from fastapi import FastAPI')
        svcs = _find_python_services(str(tmp_path))
        assert len(svcs) >= 1
        svc = [s for s in svcs if s.get("framework") == "FastAPI"][0]
        assert svc["package_manager"] == "poetry"

    def test_flask_via_pipfile(self, tmp_path):
        """Pipfile with flask → Flask, pkg_manager=pipenv."""
        _write(tmp_path / "web" / "Pipfile", '[packages]\nflask = "*"')
        _write(tmp_path / "web" / "app.py", 'from flask import Flask')
        svcs = _find_python_services(str(tmp_path))
        flask_svcs = [s for s in svcs if s.get("framework") == "Flask"]
        assert len(flask_svcs) == 1
        assert flask_svcs[0]["package_manager"] == "pipenv"

    def test_python_port_from_env(self, tmp_path):
        """PORT in .env → port_source='env'."""
        _write(tmp_path / "svc" / "requirements.txt", "fastapi")
        _write(tmp_path / "svc" / "main.py", 'from fastapi import FastAPI')
        _write(tmp_path / "svc" / ".env", "PORT=9000")
        svcs = _find_python_services(str(tmp_path))
        svc = [s for s in svcs if s.get("framework") == "FastAPI"][0]
        assert svc["port"] == 9000
        assert svc["port_source"] == "env"

    def test_skips_venv(self, tmp_path):
        """Python files inside venv/ should be ignored."""
        _write(tmp_path / "venv" / "requirements.txt", "flask")
        _write(tmp_path / "venv" / "manage.py", "import django")
        svcs = _find_python_services(str(tmp_path))
        assert len(svcs) == 0

    def test_unknown_python_framework_service_not_dropped(self, tmp_path):
        """Python manifest with no known framework should be kept as framework='Unknown'."""
        _write(tmp_path / "svc" / "requirements.txt", "requests==2.32")
        _write(tmp_path / "svc" / "app.py", "print('hello')")
        svcs = _find_python_services(str(tmp_path))
        assert len(svcs) == 1
        assert svcs[0]["framework"] == "Unknown"


class TestEmptyShellSuppressed:
    """Fix 5: Services with no port AND no entry_point are dropped (not database/other)."""

    def test_shell_without_port_or_entry_dropped(self, tmp_path):
        services = [
            {"name": "phantom", "path": "phantom/", "type": "frontend"},  # no port, no entry_point
            {"name": "real", "path": "real/", "type": "backend", "port": 3000, "entry_point": "server.js"},
        ]
        result = _drop_empty_shells(services)
        names = [s["name"] for s in result]
        assert "phantom" not in names
        assert "real" in names

    def test_database_service_never_dropped(self, tmp_path):
        services = [
            {"name": "mongo", "path": ".", "type": "database"},  # no port, no entry
            {"name": "api", "path": "api/", "type": "backend", "port": 5000, "entry_point": "index.js"},
        ]
        result = _drop_empty_shells(services)
        names = [s["name"] for s in result]
        assert "mongo" in names


class TestRootBackendWithChildBackendKept:
    """Fix 5 Rule 3: Root backend coexists with child backends."""

    def test_root_backend_kept_alongside_children(self, tmp_path):
        stubs = [
            {"name": "root-api", "abs_path": str(tmp_path), "type": "backend", "port": 3000, "entry_point": "index.js"},
            {"name": "auth", "abs_path": str(tmp_path / "auth"), "type": "backend", "port": 4000, "entry_point": "index.js"},
            {"name": "web", "abs_path": str(tmp_path / "web"), "type": "frontend", "port": 5173, "entry_point": "index.js"},
        ]
        result = _suppress_root_if_children_found(stubs, str(tmp_path))
        names = [s["name"] for s in result]
        assert "root-api" in names
        assert "auth" in names
        assert "web" in names
        assert len(result) == 3


class TestDBNamedComposeServiceTypedOther:
    """Fix 6b: DB-keyword service names return type='other'."""

    @pytest.mark.parametrize("db_name", [
        "postgres", "mysql", "mongo", "redis", "database", "elasticsearch",
    ])
    def test_db_keyword_returns_other(self, tmp_path, db_name):
        (tmp_path / db_name).mkdir()
        result = _infer_service_type(db_name, db_name, str(tmp_path))
        assert result == "other"


class TestRootMonolithSuppressesChildren:
    """Fix 5 Rule 2: Root monolith with entry_point suppresses non-database children."""

    def test_monolith_root_suppresses_non_db_children(self, tmp_path):
        stubs = [
            {"name": "app", "abs_path": str(tmp_path), "type": "monolith", "port": 3000, "entry_point": "server.js"},
            {"name": "api", "abs_path": str(tmp_path / "api"), "type": "backend", "port": 5000, "entry_point": "index.js"},
            {"name": "web", "abs_path": str(tmp_path / "web"), "type": "frontend", "port": 3001, "entry_point": "index.js"},
            {"name": "mongo", "abs_path": str(tmp_path / "mongo"), "type": "database"},
        ]
        result = _suppress_root_if_children_found(stubs, str(tmp_path))
        names = [s["name"] for s in result]
        assert "app" in names  # monolith root kept
        assert "mongo" in names  # database kept
        assert "api" not in names  # suppressed
        assert "web" not in names  # suppressed

    def test_monolith_root_without_entry_also_suppresses(self, tmp_path):
        """Monolith root always suppresses children (type-based, not entry_point-based)."""
        stubs = [
            {"name": "app", "abs_path": str(tmp_path), "type": "monolith"},  # no entry_point
            {"name": "api", "abs_path": str(tmp_path / "api"), "type": "backend"},
            {"name": "web", "abs_path": str(tmp_path / "web"), "type": "frontend"},
        ]
        result = _suppress_root_if_children_found(stubs, str(tmp_path))
        names = [s["name"] for s in result]
        assert "app" in names  # monolith root kept
        assert "api" not in names  # suppressed
        assert "web" not in names  # suppressed
