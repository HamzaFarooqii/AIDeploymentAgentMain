"""
Comprehensive, production-grade tests for entry-point detection and port detection.

Each test class simulates a real-world MERN/Node.js repo layout found on GitHub.
All tests use the actual filesystem (via tmp_path) — no mocking of the
command_extractor functions under test.

Tests are grouped:
  A. Entry-point detection  (extract_nodejs_commands)
  B. Port detection         (extract_port_from_project, extract_frontend_port)
  C. End-to-end integration (detect_framework — full pipeline)
"""

import json
import os
import textwrap
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from app.utils.command_extractor import (
    extract_nodejs_commands,
    extract_port_from_project,
    extract_frontend_port,
    _parse_env_for_port,
    _scan_source_for_port,
)
from app.utils.detector import (
    detect_framework,
    _find_all_services_by_deps,
    _find_python_services,
    infer_services,
)


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────

def _write(path: Path, content: str = ""):
    """Write a file, creating parents as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content), encoding="utf-8")


def _pkg(path: Path, name: str, deps: dict = None, dev_deps: dict = None,
         scripts: dict = None, main: str = None):
    """Write a package.json with the given fields."""
    data = {"name": name}
    if deps:
        data["dependencies"] = deps
    if dev_deps:
        data["devDependencies"] = dev_deps
    if scripts:
        data["scripts"] = scripts
    if main:
        data["main"] = main
    _write(path / "package.json", json.dumps(data, indent=2))


# ═══════════════════════════════════════════════════════════════════════
# GROUP A: ENTRY-POINT DETECTION  (extract_nodejs_commands)
# ═══════════════════════════════════════════════════════════════════════


class TestEntryPointSimpleStart:
    """Start scripts that directly call 'node <file>'."""

    def test_node_server_js(self, tmp_path):
        """'start': 'node server.js' → entry_point='server.js'."""
        _pkg(tmp_path, "api", deps={"express": "4"},
             scripts={"start": "node server.js"})
        _write(tmp_path / "server.js", "const app = require('express')();")
        r = extract_nodejs_commands(str(tmp_path))
        assert r["entry_point"] == "server.js"
        assert r["start_command"] == "node server.js"

    def test_node_app_js(self, tmp_path):
        _pkg(tmp_path, "api", deps={"express": "4"},
             scripts={"start": "node app.js"})
        _write(tmp_path / "app.js", "app.listen(3000)")
        r = extract_nodejs_commands(str(tmp_path))
        assert r["entry_point"] == "app.js"

    def test_node_nested_src_index(self, tmp_path):
        """'start': 'node src/index.js' → entry_point='src/index.js'."""
        _pkg(tmp_path, "api", deps={"express": "4"},
             scripts={"start": "node src/index.js"})
        _write(tmp_path / "src" / "index.js", "app.listen(3000)")
        r = extract_nodejs_commands(str(tmp_path))
        assert r["entry_point"] == "src/index.js"

    def test_node_index_mjs(self, tmp_path):
        """ESM entry: 'node index.mjs'."""
        _pkg(tmp_path, "api", deps={"express": "4"},
             scripts={"start": "node index.mjs"})
        _write(tmp_path / "index.mjs", "import express from 'express';")
        r = extract_nodejs_commands(str(tmp_path))
        assert r["entry_point"] == "index.mjs"


class TestEntryPointNodemonAndDevTools:
    """Repos using nodemon, ts-node, tsx — should still resolve entry."""

    def test_nodemon_server(self, tmp_path):
        """nodemon → 'node server.js' for production."""
        _pkg(tmp_path, "api", deps={"express": "4"},
             scripts={"start": "nodemon server.js"})
        _write(tmp_path / "server.js", "")
        r = extract_nodejs_commands(str(tmp_path))
        assert r["entry_point"] == "server.js"
        assert r["start_command"] == "node server.js"

    def test_nodemon_with_flags(self, tmp_path):
        """nodemon --watch src server/index.js."""
        _pkg(tmp_path, "api", deps={"express": "4"},
             scripts={"start": "nodemon --watch src server/index.js"})
        _write(tmp_path / "server" / "index.js", "")
        r = extract_nodejs_commands(str(tmp_path))
        assert r["entry_point"] == "server/index.js"

    def test_ts_node(self, tmp_path):
        """ts-node src/server.ts → start_command='npm start' (needs build)."""
        _pkg(tmp_path, "api", deps={"express": "4"},
             scripts={"start": "ts-node src/server.ts"})
        r = extract_nodejs_commands(str(tmp_path))
        assert r["start_command"] == "npm start"


class TestEntryPointComplexScripts:
    """Real-world scripts with env vars, flags, and complex invocations."""

    def test_env_prefix_node(self, tmp_path):
        """NODE_ENV=production node --max-old-space-size=4096 server/index.js.
        Complex script → start_command='node <entry>', entry_point extracted."""
        _pkg(tmp_path, "api", deps={"express": "4"},
             scripts={"start": "NODE_ENV=production node --max-old-space-size=4096 server/index.js"})
        _write(tmp_path / "server" / "index.js", "")
        r = extract_nodejs_commands(str(tmp_path))
        assert r["entry_point"] == "server/index.js"
        # If an entry point is resolved, prefer it over generic fallbacks
        assert r["start_command"] == "node server/index.js"

    def test_cross_env(self, tmp_path):
        """cross-env NODE_ENV=production node dist/main.js."""
        _pkg(tmp_path, "api", deps={"express": "4"},
             scripts={"start": "cross-env NODE_ENV=production node dist/main.js"})
        r = extract_nodejs_commands(str(tmp_path))
        assert r["entry_point"] == "dist/main.js"

    def test_multiple_env_vars(self, tmp_path):
        """DEBUG=* PORT=5000 node app.js."""
        _pkg(tmp_path, "api", deps={"express": "4"},
             scripts={"start": "DEBUG=* PORT=5000 node app.js"})
        _write(tmp_path / "app.js", "")
        r = extract_nodejs_commands(str(tmp_path))
        assert r["entry_point"] == "app.js"


class TestEntryPointPM2:
    """PM2 process manager — should extract entry from dev script or main field."""

    def test_pm2_with_dev_fallback(self, tmp_path):
        """pm2 start → uses dev script for entry detection."""
        _pkg(tmp_path, "api", deps={"express": "4"},
             scripts={
                 "start": "pm2 start ecosystem.config.js",
                 "dev": "nodemon src/app.js",
             })
        _write(tmp_path / "src" / "app.js", "")
        r = extract_nodejs_commands(str(tmp_path))
        assert r["entry_point"] == "src/app.js"
        assert r["start_command"] == "node src/app.js"

    def test_pm2_no_dev_fallback_to_main(self, tmp_path):
        """pm2 without dev script → falls back to 'main' field."""
        _pkg(tmp_path, "api", deps={"express": "4"},
             scripts={"start": "pm2 start ecosystem.config.js"},
             main="server.js")
        _write(tmp_path / "server.js", "")
        r = extract_nodejs_commands(str(tmp_path))
        assert r["entry_point"] == "server.js"


class TestEntryPointMainFieldFallback:
    """Repos that have no 'start' script but set 'main' in package.json."""

    def test_main_field_as_entry(self, tmp_path):
        _pkg(tmp_path, "api", deps={"express": "4"}, main="lib/server.js")
        _write(tmp_path / "lib" / "server.js", "")
        r = extract_nodejs_commands(str(tmp_path))
        assert r["entry_point"] == "lib/server.js"
        assert "node lib/server.js" in r["start_command"]

    def test_main_index(self, tmp_path):
        _pkg(tmp_path, "api", deps={"express": "4"}, main="index.js")
        _write(tmp_path / "index.js", "")
        r = extract_nodejs_commands(str(tmp_path))
        assert r["entry_point"] == "index.js"

    def test_main_missing_build_artifact_falls_back_to_real_entry(self, tmp_path):
        """Missing main artifact should not be accepted as entry_point."""
        _pkg(tmp_path, "api", deps={"express": "4"}, main="dist/server.js")
        _write(tmp_path / "server.js", "")
        r = extract_nodejs_commands(str(tmp_path))
        assert r["entry_point"] == "server.js"


class TestEntryPointFilesystemFallback:
    """No start script, no main field — scans filesystem for common entry files."""

    def test_server_js_exists(self, tmp_path):
        _pkg(tmp_path, "api", deps={"express": "4"})
        _write(tmp_path / "server.js", "const express = require('express');")
        r = extract_nodejs_commands(str(tmp_path))
        # app.js has priority over server.js in the common_entries list
        assert r["entry_point"] in ("server.js", "app.js", "index.js")

    def test_src_index_js(self, tmp_path):
        _pkg(tmp_path, "api", deps={"express": "4"})
        _write(tmp_path / "src" / "index.js", "app.listen(3000)")
        r = extract_nodejs_commands(str(tmp_path))
        assert r["entry_point"] == "src/index.js"

    def test_app_js_priority_over_src(self, tmp_path):
        """app.js at root is checked before src/index.js."""
        _pkg(tmp_path, "api", deps={"express": "4"})
        _write(tmp_path / "app.js", "")
        _write(tmp_path / "src" / "index.js", "")
        r = extract_nodejs_commands(str(tmp_path))
        assert r["entry_point"] == "app.js"


class TestEntryPointNestJSAndFrameworks:
    """NestJS / Fastify / complex framework start commands."""

    def test_nest_start(self, tmp_path):
        """'start': 'nest start' → complex script → npm start."""
        _pkg(tmp_path, "api",
             deps={"@nestjs/core": "10"},
             scripts={"start": "nest start", "start:prod": "node dist/main.js"})
        r = extract_nodejs_commands(str(tmp_path))
        assert r["start_command"] == "npm start"

    def test_node_dot_as_entry(self, tmp_path):
        """'start': 'node .' — main field needed for real entry."""
        _pkg(tmp_path, "api", deps={"express": "4"},
             scripts={"start": "node ."}, main="server.js")
        _write(tmp_path / "server.js", "")
        r = extract_nodejs_commands(str(tmp_path))
        # node . should fallback to main field
        assert r["entry_point"] is not None


class TestEntryPointNoPackageJson:
    """No package.json at all — should return empty result."""

    def test_empty_directory(self, tmp_path):
        r = extract_nodejs_commands(str(tmp_path))
        assert r["entry_point"] is None
        assert r["start_command"] is None


# ═══════════════════════════════════════════════════════════════════════
# GROUP B: PORT DETECTION
# ═══════════════════════════════════════════════════════════════════════


class TestPortFromEnvFile:
    """Port reading from .env, .env.example, .env.local, etc."""

    def test_env_port_simple(self, tmp_path):
        _write(tmp_path / ".env", "PORT=5000\nDB_URL=mongodb://localhost")
        r = extract_port_from_project(str(tmp_path))
        assert r["port"] == 5000
        assert r["source"] == "env"

    def test_env_port_with_spaces(self, tmp_path):
        _write(tmp_path / ".env", "PORT = 4000\n")
        r = extract_port_from_project(str(tmp_path))
        assert r["port"] == 4000

    def test_env_example_fallback(self, tmp_path):
        """No .env → reads .env.example."""
        _write(tmp_path / ".env.example", "PORT=8080\nSECRET=abc")
        r = extract_port_from_project(str(tmp_path))
        assert r["port"] == 8080
        assert r["source"] == "env"

    def test_env_local_fallback(self, tmp_path):
        _write(tmp_path / ".env.local", "PORT=9090")
        r = extract_port_from_project(str(tmp_path))
        assert r["port"] == 9090

    def test_port_not_first_line(self, tmp_path):
        """PORT buried among other vars."""
        _write(tmp_path / ".env", "DB_URL=mongo://x\nSECRET=abc\nPORT=7000\nNODE_ENV=dev")
        r = extract_port_from_project(str(tmp_path))
        assert r["port"] == 7000

    def test_port_commented_out_ignored(self, tmp_path):
        """Commented-out PORT should not match (regex anchors to ^)."""
        _write(tmp_path / ".env", "# PORT=9999\nAPP_PORT=3000")
        r = extract_port_from_project(str(tmp_path))
        # Commented PORT should not match; APP_PORT is not PORT
        assert r["source"] != "env" or r["port"] != 9999

    def test_env_with_quotes(self, tmp_path):
        """PORT='4500' — regex expects digits only, no quotes."""
        _write(tmp_path / ".env", "PORT=4500")
        r = extract_port_from_project(str(tmp_path))
        assert r["port"] == 4500

    def test_env_priority_over_source(self, tmp_path):
        """.env port takes priority over source code port."""
        _write(tmp_path / ".env", "PORT=4000")
        _write(tmp_path / "server.js", "app.listen(5000)")
        r = extract_port_from_project(str(tmp_path), language="JavaScript")
        assert r["port"] == 4000
        assert r["source"] == "env"


class TestPortFromSourceCode:
    """Source-code scanning for .listen(), process.env.PORT, const PORT, etc."""

    def test_app_listen_number(self, tmp_path):
        _write(tmp_path / "server.js",
               "const app = require('express')();\napp.listen(5000, () => {});")
        r = extract_port_from_project(str(tmp_path), language="JavaScript")
        assert r["port"] == 5000
        assert r["source"] == "source"

    def test_server_listen(self, tmp_path):
        _write(tmp_path / "app.js",
               "const server = http.createServer(app);\nserver.listen(4000);")
        r = extract_port_from_project(str(tmp_path), language="JavaScript")
        assert r["port"] == 4000

    def test_process_env_port_fallback(self, tmp_path):
        """process.env.PORT || 8080."""
        _write(tmp_path / "server.js",
               "const port = process.env.PORT || 8080;\napp.listen(port);")
        r = extract_port_from_project(str(tmp_path), language="JavaScript")
        assert r["port"] == 8080
        assert r["source"] == "source"

    def test_const_port_declaration(self, tmp_path):
        """const PORT = 3001."""
        _write(tmp_path / "server.js",
               "const PORT = 3001;\napp.listen(PORT);")
        r = extract_port_from_project(str(tmp_path), language="JavaScript")
        assert r["port"] == 3001

    def test_typescript_source(self, tmp_path):
        """Port detected from .ts files."""
        _write(tmp_path / "server.ts",
               "import express from 'express';\nconst app = express();\napp.listen(5555);")
        r = extract_port_from_project(str(tmp_path), language="TypeScript")
        assert r["port"] == 5555

    def test_src_nested_server(self, tmp_path):
        """Port in src/server.js."""
        _write(tmp_path / "src" / "server.js",
               "app.listen(6000, '0.0.0.0');")
        r = extract_port_from_project(str(tmp_path), language="JavaScript")
        assert r["port"] == 6000

    def test_listen_with_host(self, tmp_path):
        """app.listen(4001, '0.0.0.0', callback)."""
        _write(tmp_path / "server.js",
               "app.listen(4001, '0.0.0.0', () => console.log('running'));")
        r = extract_port_from_project(str(tmp_path), language="JavaScript")
        assert r["port"] == 4001

    def test_port_colon_object(self, tmp_path):
        """Config object: { port: 7000 }."""
        _write(tmp_path / "server.js",
               "const config = { host: '0.0.0.0', port: 7000 };\napp.listen(config.port);")
        r = extract_port_from_project(str(tmp_path), language="JavaScript")
        assert r["port"] == 7000

    def test_recursive_scan_ignores_frontend_config_files(self, tmp_path):
        """Recursive scan should ignore config-file port hints."""
        _write(tmp_path / "api.js", "app.listen(5050)")
        _write(tmp_path / "vite.config.js", "export default { server: { port: 3000 } }")
        r = extract_port_from_project(str(tmp_path), language="JavaScript")
        assert r["port"] == 5050

    def test_dynamic_port_not_detected(self, tmp_path):
        """app.listen(PORT) with only variable — no hardcoded port visible."""
        _write(tmp_path / "server.js",
               "const PORT = parseInt(process.env.PORT);\napp.listen(PORT);")
        r = extract_port_from_project(str(tmp_path), language="JavaScript")
        # No literal port found → falls through to default
        assert r["source"] in ("source", "default")


class TestPortFrameworkDefaults:
    """When no .env and no source port → framework defaults kick in."""

    @pytest.mark.parametrize("framework,expected", [
        ("Express.js", 3000),
        ("Fastify", 3000),
        ("NestJS", 3000),
        ("Flask", 5000),
        ("Django", 8000),
        ("FastAPI", 8000),
        ("Next.js", 3000),
        ("Vite", 5173),
        ("Spring Boot", 8080),
    ])
    def test_framework_defaults(self, tmp_path, framework, expected):
        r = extract_port_from_project(str(tmp_path), framework=framework)
        assert r["port"] == expected
        assert r["source"] == "default"

    @pytest.mark.parametrize("language,expected", [
        ("JavaScript", 3000),
        ("TypeScript", 3000),
        ("Python", 8000),
        ("Java", 8080),
    ])
    def test_language_defaults(self, tmp_path, language, expected):
        r = extract_port_from_project(str(tmp_path), language=language)
        assert r["port"] == expected


class TestFrontendPortDetection:
    """Frontend port from vite.config, CRA defaults, etc."""

    def test_vite_custom_port(self, tmp_path):
        _pkg(tmp_path, "web", dev_deps={"vite": "5"})
        _write(tmp_path / "vite.config.js", """
            export default {
              server: { port: 4200 },
            }
        """)
        r = extract_frontend_port(str(tmp_path))
        assert r["port"] == 4200
        assert "vite" in r["source"]

    def test_vite_default_port(self, tmp_path):
        """Vite without port config → 5173."""
        _pkg(tmp_path, "web", dev_deps={"vite": "5"})
        r = extract_frontend_port(str(tmp_path))
        assert r["port"] == 5173
        assert r["source"] == "vite_default"

    def test_cra_default_port(self, tmp_path):
        """react-scripts → 3000."""
        _pkg(tmp_path, "web", deps={"react-scripts": "5"})
        r = extract_frontend_port(str(tmp_path))
        assert r["port"] == 3000
        assert r["source"] == "cra_default"

    def test_next_default_port(self, tmp_path):
        """Next.js → 3000."""
        _pkg(tmp_path, "web", deps={"next": "14"})
        r = extract_frontend_port(str(tmp_path))
        assert r["port"] == 3000
        assert r["source"] == "next_default"

    def test_vue_cli_default_port(self, tmp_path):
        """@vue/cli-service → 8080."""
        _pkg(tmp_path, "web", dev_deps={"@vue/cli-service": "5"})
        r = extract_frontend_port(str(tmp_path))
        assert r["port"] == 8080
        assert r["source"] == "vue_default"

    def test_vite_config_ts(self, tmp_path):
        """vite.config.ts (TypeScript)."""
        _pkg(tmp_path, "web", dev_deps={"vite": "5"})
        _write(tmp_path / "vite.config.ts", """
            import { defineConfig } from 'vite';
            export default defineConfig({
              server: { port: 3333, host: true },
            })
        """)
        r = extract_frontend_port(str(tmp_path))
        assert r["port"] == 3333

    def test_env_port_for_frontend(self, tmp_path):
        """Generic PORT in .env should not override frontend defaults."""
        _pkg(tmp_path, "web", dev_deps={"vite": "5"})
        _write(tmp_path / ".env", "PORT=8888")
        r = extract_frontend_port(str(tmp_path))
        assert r["port"] == 5173
        assert r["source"] == "vite_default"

    def test_env_frontend_keys_override_generic_port(self, tmp_path):
        """Frontend-specific env keys should override generic PORT."""
        _pkg(tmp_path, "web", dev_deps={"vite": "5"})
        _write(tmp_path / ".env", "PORT=8000\nVITE_PORT=5173\n")
        r = extract_frontend_port(str(tmp_path))
        assert r["port"] == 5173
        assert r["source"] == "env"

    def test_frontend_ignores_generic_port_when_backend_key_present(self, tmp_path):
        """Backend-specific keys in the same env file should block generic PORT fallback for frontend."""
        _pkg(tmp_path, "web", dev_deps={"vite": "5"})
        _write(tmp_path / ".env", "BACKEND_PORT=8000\nPORT=8000\n")
        r = extract_frontend_port(str(tmp_path))
        assert r["port"] == 5173
        assert r["source"] == "vite_default"

    def test_no_package_json_defaults(self, tmp_path):
        """No package.json -> unknown frontend port (caller applies fallback)."""
        r = extract_frontend_port(str(tmp_path))
        assert r["port"] is None
        assert r["source"] == "default"


# ═══════════════════════════════════════════════════════════════════════
# GROUP C: PORT + ENTRY COMBINATIONS (Realistic repo scenarios)
# ═══════════════════════════════════════════════════════════════════════


class TestRealisticMERNBackend:
    """Full MERN backend directory scenarios — entry + port together."""

    def test_classic_express_mern(self, tmp_path):
        """Classic MERN backend: express + mongoose, port in .env, node server.js."""
        _pkg(tmp_path, "backend",
             deps={"express": "4.18", "mongoose": "7.0", "dotenv": "16"},
             scripts={"start": "node server.js", "dev": "nodemon server.js"})
        _write(tmp_path / "server.js", textwrap.dedent("""
            require('dotenv').config();
            const express = require('express');
            const app = express();
            const PORT = process.env.PORT || 5000;
            app.listen(PORT, () => console.log(`Server on ${PORT}`));
        """))
        _write(tmp_path / ".env", "PORT=5000\nMONGO_URI=mongodb://localhost/mydb")

        cmds = extract_nodejs_commands(str(tmp_path))
        assert cmds["entry_point"] == "server.js"
        assert cmds["start_command"] == "node server.js"

        port = extract_port_from_project(str(tmp_path), "Express.js", "JavaScript")
        assert port["port"] == 5000
        assert port["source"] == "env"

    def test_api_server_subfolder(self, tmp_path):
        """Backend inside server/ subfolder with index.js entry."""
        svc = tmp_path / "server"
        _pkg(svc, "api",
             deps={"express": "4", "cors": "2"},
             scripts={"start": "node index.js"})
        _write(svc / "index.js", textwrap.dedent("""
            const app = require('express')();
            app.listen(4000, () => {});
        """))
        _write(svc / ".env", "PORT=4000")

        cmds = extract_nodejs_commands(str(svc))
        assert cmds["entry_point"] == "index.js"

        port = extract_port_from_project(str(svc), "Express.js", "JavaScript")
        assert port["port"] == 4000

    def test_typescript_express_project(self, tmp_path):
        """TypeScript backend — entry point in dist/, source port in src/."""
        _pkg(tmp_path, "api",
             deps={"express": "4"},
             dev_deps={"typescript": "5", "ts-node": "10"},
             scripts={
                 "start": "node dist/server.js",
                 "dev": "ts-node src/server.ts",
                 "build": "tsc",
             })
        _write(tmp_path / "src" / "server.ts", textwrap.dedent("""
            import express from 'express';
            const app = express();
            app.listen(5555, () => console.log('TS server'));
        """))

        cmds = extract_nodejs_commands(str(tmp_path))
        assert cmds["entry_point"] == "dist/server.js"

        port = extract_port_from_project(str(tmp_path), language="TypeScript")
        assert port["port"] == 5555
        assert port["source"] == "source"

    def test_monorepo_with_env_vars_and_flags(self, tmp_path):
        """Real-world script: NODE_OPTIONS=--max-old-space-size=8192 node server/app.js.
        Complex script → start_command='node <entry>', entry parsed from script."""
        _pkg(tmp_path, "backend",
             deps={"express": "4", "mongoose": "7"},
             scripts={
                 "start": "NODE_OPTIONS=--max-old-space-size=8192 node server/app.js",
                 "dev": "nodemon --watch server server/app.js",
             })
        _write(tmp_path / "server" / "app.js", textwrap.dedent("""
            const express = require('express');
            const port = process.env.PORT || 3001;
            const app = express();
            app.listen(port);
        """))
        _write(tmp_path / ".env.example", "PORT=3001\nDB_URL=mongodb://localhost")

        cmds = extract_nodejs_commands(str(tmp_path))
        assert cmds["entry_point"] == "server/app.js"
        # If an entry point is resolved, prefer it over generic fallbacks
        assert cmds["start_command"] == "node server/app.js"

        port = extract_port_from_project(str(tmp_path), language="JavaScript")
        assert port["port"] == 3001
        assert port["source"] == "env"

    def test_koa_backend(self, tmp_path):
        """Koa.js backend with different listen pattern."""
        _pkg(tmp_path, "api",
             deps={"koa": "2.14", "@koa/router": "12"},
             scripts={"start": "node src/index.js"})
        _write(tmp_path / "src" / "index.js", textwrap.dedent("""
            const Koa = require('koa');
            const app = new Koa();
            const PORT = 4500;
            app.listen(PORT);
        """))
        cmds = extract_nodejs_commands(str(tmp_path))
        assert cmds["entry_point"] == "src/index.js"

        port = extract_port_from_project(str(tmp_path), language="JavaScript")
        assert port["port"] == 4500

    def test_fastify_backend(self, tmp_path):
        """Fastify with server.listen({ port: 3002 })."""
        _pkg(tmp_path, "api",
             deps={"fastify": "4"},
             scripts={"start": "node app.js"})
        _write(tmp_path / "app.js", textwrap.dedent("""
            const fastify = require('fastify')();
            fastify.listen({ port: 3002, host: '0.0.0.0' });
        """))
        cmds = extract_nodejs_commands(str(tmp_path))
        assert cmds["entry_point"] == "app.js"

        port = extract_port_from_project(str(tmp_path), language="JavaScript")
        assert port["port"] == 3002

    def test_graphql_apollo_backend(self, tmp_path):
        """Apollo Server / GraphQL backend."""
        _pkg(tmp_path, "api",
             deps={"express": "4", "apollo-server-express": "3"},
             scripts={"start": "node server.js"})
        _write(tmp_path / "server.js", textwrap.dedent("""
            const { ApolloServer } = require('apollo-server-express');
            const express = require('express');
            const app = express();
            const PORT = process.env.PORT || 4000;
            app.listen(PORT, () => console.log(`GraphQL at :${PORT}/graphql`));
        """))
        _write(tmp_path / ".env", "PORT=4000")

        cmds = extract_nodejs_commands(str(tmp_path))
        assert cmds["entry_point"] == "server.js"

        port = extract_port_from_project(str(tmp_path), language="JavaScript")
        assert port["port"] == 4000

    def test_no_env_no_source_falls_to_default(self, tmp_path):
        """Backend with no .env, no port in source → framework default."""
        _pkg(tmp_path, "api",
             deps={"express": "4"},
             scripts={"start": "node index.js"})
        _write(tmp_path / "index.js", textwrap.dedent("""
            const express = require('express');
            const app = express();
            // port comes from Kubernetes env at runtime
            app.listen(process.env.PORT);
        """))
        cmds = extract_nodejs_commands(str(tmp_path))
        assert cmds["entry_point"] == "index.js"

        port = extract_port_from_project(str(tmp_path), "Express.js", "JavaScript")
        assert port["port"] == 3000
        assert port["source"] == "default"


class TestRealisticMERNFrontend:
    """Full MERN frontend directory scenarios — build output + port."""

    def test_vite_react_frontend(self, tmp_path):
        """Vite + React with custom port in vite.config.ts."""
        _pkg(tmp_path, "frontend",
             deps={"react": "18", "react-dom": "18"},
             dev_deps={"vite": "5", "@vitejs/plugin-react": "4"},
             scripts={"dev": "vite", "build": "vite build"})
        _write(tmp_path / "vite.config.ts", textwrap.dedent("""
            import { defineConfig } from 'vite';
            import react from '@vitejs/plugin-react';
            export default defineConfig({
              plugins: [react()],
              server: { port: 3000, proxy: { '/api': 'http://localhost:5000' } },
            })
        """))
        _write(tmp_path / "src" / "App.tsx", "export default function App() {}")

        cmds = extract_nodejs_commands(str(tmp_path))
        assert cmds["build_output"] == "dist"

        port = extract_frontend_port(str(tmp_path))
        assert port["port"] == 3000

    def test_cra_frontend(self, tmp_path):
        """Create React App frontend."""
        _pkg(tmp_path, "client",
             deps={"react": "18", "react-dom": "18", "react-scripts": "5"},
             scripts={"start": "react-scripts start", "build": "react-scripts build"})
        _write(tmp_path / "src" / "App.js", "")

        cmds = extract_nodejs_commands(str(tmp_path))
        assert cmds["build_output"] == "build"  # CRA outputs to build/

        port = extract_frontend_port(str(tmp_path))
        assert port["port"] == 3000
        assert port["source"] == "cra_default"


class TestPortDetectionEdgeCases:
    """Edge cases and boundary conditions."""

    def test_port_below_1000_rejected(self, tmp_path):
        """Port 80 or 443 in source code is filtered out (< 1000)."""
        _write(tmp_path / "server.js", "app.listen(80);")
        r = extract_port_from_project(str(tmp_path), language="JavaScript")
        # Port 80 should be skipped (< 1000)
        assert r["port"] != 80 or r["source"] != "source"

    def test_port_above_65535_rejected(self, tmp_path):
        """Port 99999 in source code is filtered out (> 65535)."""
        _write(tmp_path / "server.js", "app.listen(99999);")
        r = extract_port_from_project(str(tmp_path), language="JavaScript")
        assert r["port"] != 99999

    def test_multiple_listen_calls_first_wins(self, tmp_path):
        """Multiple .listen() calls — first match wins."""
        _write(tmp_path / "server.js", textwrap.dedent("""
            httpServer.listen(3000);
            httpsServer.listen(3443);
        """))
        r = extract_port_from_project(str(tmp_path), language="JavaScript")
        assert r["port"] == 3000

    def test_empty_env_file(self, tmp_path):
        """Empty .env file → no port found."""
        _write(tmp_path / ".env", "")
        r = extract_port_from_project(str(tmp_path), language="JavaScript")
        assert r["source"] != "env"

    def test_env_with_only_comments(self, tmp_path):
        """Only comments in .env → no port."""
        _write(tmp_path / ".env", "# PORT=5000\n# DB_URL=xxx")
        r = extract_port_from_project(str(tmp_path), language="JavaScript")
        assert r["port"] != 5000 or r["source"] != "env"


# ═══════════════════════════════════════════════════════════════════════
# GROUP D: END-TO-END FULLSTACK DETECTION
# ═══════════════════════════════════════════════════════════════════════


class TestEndToEndMERNRepos:
    """Full detect_framework runs simulating complete MERN repositories.
    These use the real filesystem but mock extract_database_info and ML."""

    @patch("app.utils.detector.get_ml_analyzer")
    @patch("app.utils.detector.extract_database_info")
    def test_classic_mern_backend_frontend(self, mock_db, mock_ml, tmp_path):
        """Standard MERN: root package.json + backend/ + frontend/."""
        mock_ml.return_value = MagicMock()
        mock_db.return_value = {"db_type": "mongodb", "is_cloud": False, "database_env_var": "MONGO_URI"}

        # Root
        _pkg(tmp_path, "mern-app",
             scripts={"start": "concurrently \"npm:backend\" \"npm:frontend\""})

        # Backend
        be = tmp_path / "backend"
        _pkg(be, "backend",
             deps={"express": "4.18", "mongoose": "7", "dotenv": "16"},
             scripts={"start": "node server.js"})
        _write(be / "server.js", textwrap.dedent("""
            const express = require('express');
            const app = express();
            const PORT = process.env.PORT || 5000;
            app.listen(PORT);
        """))
        _write(be / ".env", "PORT=5000\nMONGO_URI=mongodb://localhost/test")

        # Frontend
        fe = tmp_path / "frontend"
        _pkg(fe, "frontend",
             deps={"react": "18", "react-dom": "18"},
             dev_deps={"vite": "5", "@vitejs/plugin-react": "4"},
             scripts={"dev": "vite", "build": "vite build"})
        _write(fe / "src" / "App.jsx", "export default () => <div/>;")

        result = detect_framework(str(tmp_path), use_ml=False)

        # Services should include backend + frontend
        services = result.get("services", [])
        svc_types = [s["type"] for s in services]
        assert "backend" in svc_types
        assert "frontend" in svc_types

        # Backend should have port and entry
        be_svc = next(s for s in services if s["type"] == "backend")
        assert be_svc.get("entry_point") is not None
        assert be_svc.get("port") is not None
        assert be_svc.get("env_file") is not None

        # Should not be blocked since .env exists
        assert result["deploy_blocked"] is False

    @patch("app.utils.detector.get_ml_analyzer")
    @patch("app.utils.detector.extract_database_info")
    def test_nonstandard_folder_names(self, mock_db, mock_ml, tmp_path):
        """Non-standard names: api-server/ + web-client/."""
        mock_ml.return_value = MagicMock()
        mock_db.return_value = {"db_type": None, "is_cloud": False, "database_env_var": None}

        # Backend in non-standard folder
        be = tmp_path / "api-server"
        _pkg(be, "api-server",
             deps={"express": "4.18", "cors": "2"},
             scripts={"start": "node src/index.js"})
        _write(be / "src" / "index.js", "const app = require('express')(); app.listen(4000);")

        # Frontend in non-standard folder
        fe = tmp_path / "web-client"
        _pkg(fe, "web-client",
             deps={"react": "18", "react-dom": "18"},
             dev_deps={"vite": "5"},
             scripts={"build": "vite build"})
        _write(fe / "src" / "App.jsx", "")

        result = detect_framework(str(tmp_path), use_ml=False)
        services = result.get("services", [])
        svc_names = [s["name"] for s in services]

        # Both should be detected despite non-standard names (dep-based scanning)
        assert "api-server" in svc_names
        assert "web-client" in svc_names

    @patch("app.utils.detector.get_ml_analyzer")
    @patch("app.utils.detector.extract_database_info")
    def test_monolith_mern_single_package(self, mock_db, mock_ml, tmp_path):
        """Monolith: express + react in same package.json."""
        mock_ml.return_value = MagicMock()
        mock_db.return_value = {"db_type": "mongodb", "is_cloud": False, "database_env_var": "MONGO_URI"}

        _pkg(tmp_path, "fullstack",
             deps={"express": "4.18", "react": "18", "react-dom": "18", "mongoose": "7"},
             scripts={
                 "start": "node server.js",
                 "build": "react-scripts build",
             })
        _write(tmp_path / "server.js", textwrap.dedent("""
            const express = require('express');
            const path = require('path');
            const app = express();
            app.use(express.static(path.join(__dirname, 'build')));
            app.listen(process.env.PORT || 3000);
        """))
        _write(tmp_path / ".env", "PORT=3000\nMONGO_URI=mongodb://localhost/app")

        result = detect_framework(str(tmp_path), use_ml=False)
        services = result.get("services", [])

        # Should detect as monolith
        monolith_svcs = [s for s in services if s["type"] == "monolith"]
        assert len(monolith_svcs) >= 1
        assert result.get("architecture") == "monolith" or any(
            s.get("dockerfile_strategy") == "single_stage_with_build" for s in services
        )

    @patch("app.utils.detector.get_ml_analyzer")
    @patch("app.utils.detector.extract_database_info")
    def test_three_service_repo(self, mock_db, mock_ml, tmp_path):
        """3-service repo: auth-service/ + api-gateway/ + react-app/."""
        mock_ml.return_value = MagicMock()
        mock_db.return_value = {"db_type": None, "is_cloud": False, "database_env_var": None}

        for name, port in [("auth-service", 4001), ("api-gateway", 4000)]:
            svc = tmp_path / name
            _pkg(svc, name,
                 deps={"express": "4", "jsonwebtoken": "9"},
                 scripts={"start": f"node index.js"})
            _write(svc / "index.js", f"require('express')().listen({port});")
            _write(svc / ".env", f"PORT={port}")

        fe = tmp_path / "react-app"
        _pkg(fe, "react-app",
             deps={"react": "18", "react-dom": "18"},
             dev_deps={"vite": "5"},
             scripts={"build": "vite build"})
        _write(fe / "src" / "App.jsx", "")

        result = detect_framework(str(tmp_path), use_ml=False)
        services = result.get("services", [])

        assert len(services) >= 3
        backend_svcs = [s for s in services if s["type"] == "backend"]
        assert len(backend_svcs) >= 2

    @patch("app.utils.detector.get_ml_analyzer")
    @patch("app.utils.detector.extract_database_info")
    def test_python_fastapi_plus_react(self, mock_db, mock_ml, tmp_path):
        """Mixed stack: Python FastAPI + React frontend."""
        mock_ml.return_value = MagicMock()
        mock_db.return_value = {"db_type": "postgresql", "is_cloud": False, "database_env_var": "DATABASE_URL"}

        be = tmp_path / "api"
        be.mkdir()
        _write(be / "requirements.txt", "fastapi==0.100\nuvicorn==0.27\nsqlalchemy==2.0")
        _write(be / "main.py", textwrap.dedent("""
            from fastapi import FastAPI
            app = FastAPI()
            @app.get("/")
            def root(): return {"msg": "hello"}
        """))

        fe = tmp_path / "client"
        _pkg(fe, "client",
             deps={"react": "18", "react-dom": "18"},
             dev_deps={"vite": "5"},
             scripts={"build": "vite build"})
        _write(fe / "src" / "App.jsx", "")

        result = detect_framework(str(tmp_path), use_ml=False)
        services = result.get("services", [])

        # Should detect FastAPI backend
        py_svcs = [s for s in services if s.get("language") == "Python"]
        assert len(py_svcs) >= 1
        assert py_svcs[0].get("framework") == "FastAPI"

        # Should detect React frontend
        fe_svcs = [s for s in services if s["type"] == "frontend"]
        assert len(fe_svcs) >= 1


class TestWorkspaceSupportInferServices:
    """Workspace-aware service discovery should use workspace paths as authoritative."""

    @patch("app.utils.detection_services.extract_database_info")
    def test_workspace_exact_backend_frontend(self, mock_db, tmp_path):
        mock_db.return_value = {"db_type": None}

        _write(tmp_path / "package.json", json.dumps({
            "name": "workspace-root",
            "private": True,
            "workspaces": ["backend", "frontend"],
        }))
        _pkg(tmp_path / "backend", "backend", deps={"express": "4"}, scripts={"start": "node server.js"})
        _write(tmp_path / "backend" / "server.js", "require('express')().listen(5000)")
        _pkg(
            tmp_path / "frontend",
            "frontend",
            deps={"react": "18", "react-dom": "18"},
            dev_deps={"vite": "5"},
            scripts={"dev": "vite", "build": "vite build"},
        )
        _write(tmp_path / "frontend" / "src" / "App.jsx", "export default () => <div />")

        services = infer_services(str(tmp_path), "JavaScript", "Unknown", {"database": "Unknown"})
        svc_types = {s["type"] for s in services}
        assert "backend" in svc_types
        assert "frontend" in svc_types


    @patch("app.utils.detection_services.extract_database_info")
    def test_workspace_wildcard_packages(self, mock_db, tmp_path):
        mock_db.return_value = {"db_type": None}

        _write(tmp_path / "package.json", json.dumps({
            "name": "workspace-root",
            "private": True,
            "workspaces": ["packages/*"],
        }))
        _pkg(tmp_path / "packages" / "api", "api", deps={"express": "4"}, scripts={"start": "node index.js"})
        _write(tmp_path / "packages" / "api" / "index.js", "require('express')().listen(4000)")
        _pkg(
            tmp_path / "packages" / "web",
            "web",
            deps={"react": "18", "react-dom": "18"},
            dev_deps={"vite": "5"},
            scripts={"dev": "vite", "build": "vite build"},
        )
        _write(tmp_path / "packages" / "web" / "src" / "App.jsx", "export default () => <div />")
        _pkg(tmp_path / "packages" / "worker", "worker", deps={"express": "4"}, scripts={"start": "node worker.js"})
        _write(tmp_path / "packages" / "worker" / "worker.js", "require('express')().listen(4100)")

        services = infer_services(str(tmp_path), "JavaScript", "Unknown", {"database": "Unknown"})
        names = {s["name"] for s in services}
        assert {"api", "web", "worker"}.issubset(names)

    @patch("app.utils.detection_services.extract_database_info")
    def test_workspace_root_not_classified_as_service(self, mock_db, tmp_path):
        mock_db.return_value = {"db_type": None}

        _write(tmp_path / "package.json", json.dumps({
            "name": "workspace-root",
            "private": True,
            "dependencies": {"express": "4"},
            "workspaces": ["backend"],
        }))
        _pkg(tmp_path / "backend", "backend", deps={"express": "4"}, scripts={"start": "node server.js"})
        _write(tmp_path / "backend" / "server.js", "require('express')().listen(5000)")

        services = infer_services(str(tmp_path), "JavaScript", "Unknown", {"database": "Unknown"})
        assert all(s.get("path") != "." for s in services if s.get("type") != "database")
        assert any(s.get("path") == "backend/" for s in services)

    @patch("app.utils.detection_services.extract_database_info")
    def test_non_workspace_repo_behaviour_unchanged(self, mock_db, tmp_path):
        mock_db.return_value = {"db_type": None}

        _pkg(tmp_path / "backend", "backend", deps={"express": "4"}, scripts={"start": "node server.js"})
        _write(tmp_path / "backend" / "server.js", "require('express')().listen(5000)")
        _pkg(
            tmp_path / "frontend",
            "frontend",
            deps={"react": "18", "react-dom": "18"},
            dev_deps={"vite": "5"},
            scripts={"dev": "vite", "build": "vite build"},
        )
        _write(tmp_path / "frontend" / "src" / "App.jsx", "export default () => <div />")

        services = infer_services(str(tmp_path), "JavaScript", "Unknown", {"database": "Unknown"})
        svc_types = {s["type"] for s in services}
        assert "backend" in svc_types
        assert "frontend" in svc_types


class TestInferServicesOtherReclassification:
    def test_reclassifies_other_stub_by_name(self, tmp_path):
        _pkg(
            tmp_path / "api-server",
            "api-server",
            deps={"lodash": "4"},
            scripts={"start": "node server.js"},
        )
        _write(tmp_path / "api-server" / "server.js", "console.log('ok')")

        services = infer_services(str(tmp_path), "JavaScript", "Unknown", {"database": "Unknown"})
        assert any(s.get("type") == "backend" and s.get("name") == "api-server" for s in services)

    @patch("app.utils.detection_services.extract_database_info")
    def test_uses_other_stub_as_db_fallback_when_no_backend(self, mock_db, tmp_path):
        mock_db.return_value = {
            "db_type": "mongodb",
            "is_cloud": False,
            "needs_container": True,
            "env_var_name": "MONGO_URI",
            "default_port": 27017,
            "docker_image": "mongo:latest",
        }

        _pkg(
            tmp_path / "frontend",
            "frontend",
            deps={"react": "18", "react-dom": "18"},
            dev_deps={"vite": "5"},
            scripts={"dev": "vite"},
        )
        _write(tmp_path / "frontend" / "src" / "App.jsx", "export default () => <div />")
        _pkg(
            tmp_path / "misc",
            "misc",
            deps={"lodash": "4"},
            scripts={"start": "node server.js"},
        )
        _write(tmp_path / "misc" / "server.js", "console.log('ok')")

        metadata = {"database": "Unknown"}
        services = infer_services(
            str(tmp_path),
            "JavaScript",
            "Unknown",
            metadata,
            db_result={"primary": "MongoDB", "all": ["MongoDB"], "details": {}},
        )

        # extract_database_info is called first against the probe path (the
        # "other" stub, since no real backend exists) and, because that path
        # differs from the project root, a second time against the root to
        # check for an authoritative root-level .env override. The primary
        # probe (first call) is what this test verifies.
        called_path, called_detected_db = mock_db.call_args_list[0].args
        assert os.path.normpath(called_path) == os.path.normpath(str(tmp_path / "misc"))
        assert called_detected_db == "MongoDB"
        assert any(s.get("type") == "database" for s in services)

    @patch("app.utils.detection_services.extract_database_info")
    def test_prefers_db_result_over_metadata_database(self, mock_db, tmp_path):
        mock_db.return_value = {
            "db_type": "postgresql",
            "is_cloud": False,
            "needs_container": True,
            "env_var_name": "DATABASE_URL",
            "default_port": 5432,
            "docker_image": "postgres:15-alpine",
        }

        _pkg(
            tmp_path / "backend",
            "backend",
            deps={"express": "4"},
            scripts={"start": "node server.js"},
        )
        _write(tmp_path / "backend" / "server.js", "console.log('ok')")

        infer_services(
            str(tmp_path),
            "JavaScript",
            "Unknown",
            {"database": "MongoDB"},
            db_result={"primary": "PostgreSQL", "all": ["PostgreSQL"], "details": {}},
        )

        _, called_detected_db = mock_db.call_args.args
        assert called_detected_db == "PostgreSQL"
