# DevOps AutoPilot - Complete System Documentation

**Author:** Abdul Ahad Abbassi
**Date:** April 18, 2026
**Purpose:** Comprehensive technical reference covering all inputs, AI prompts, environment handling, and data flow

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Architecture](#2-architecture)
3. [Configuration & Settings](#3-configuration--settings)
4. [API Endpoints (Full Reference)](#4-api-endpoints-full-reference)
5. [User Upload Flow](#5-user-upload-flow)
6. [Project Extraction Flow](#6-project-extraction-flow)
7. [Analysis Pipeline — Detection Modules](#7-analysis-pipeline--detection-modules)
8. [AI / LLM Integration](#8-ai--llm-integration)
9. [Docker AI Controller — Deploy Flow](#9-docker-ai-controller--deploy-flow)
10. [Environment File (.env) Handling](#10-environment-file-env-handling)
11. [Deploy Blocked / Deploy Warning Logic](#11-deploy-blocked--deploy-warning-logic)
12. [Authentication](#12-authentication)
13. [Database Schema (MongoDB)](#13-database-schema-mongodb)
14. [Frontend Integration](#14-frontend-integration)
15. [AWS Terraform Deployment](#15-aws-terraform-deployment)
16. [Monitoring & Kubernetes Deployment](#16-monitoring--kubernetes-deployment)
17. [Script Editing Guide (Critical Couplings)](#17-script-editing-guide-critical-couplings)

---

## 1. System Overview

DevOps AutoPilot is a full-stack AI-powered DevOps tool that:

1. Accepts a **user's project archive** (ZIP / TAR / TGZ)
2. **Extracts** and **analyses** it automatically (language, framework, ports, DB, services)
3. Feeds structured project metadata into the configured LLM provider (Ollama or Gemini)
4. The LLM generates or validates `Dockerfile`s, `docker-compose.yml`, and Kubernetes manifests
5. Allows the user to build, run, push, and Kubernetes-deploy Docker images from the UI
6. (Optional) Generates Terraform for AWS EC2 Free Tier docker-compose deployment

### Technology Stack

| Layer            | Technology                                       |
| ---------------- | ------------------------------------------------ |
| Backend API      | FastAPI (Python 3.11+)                           |
| Database         | MongoDB 7.x (Motor async)                        |
| LLM Runtime      | Ollama local models or Gemini API                 |
| ML Analyser      | scikit-learn (language/framework classification) |
| Frontend         | React + TypeScript (Vite)                        |
| Docker Operation | Python `subprocess` + Docker CLI                 |
| Kubernetes Ops   | `kubectl` + Docker Desktop compatible manifests  |
| AWS Operation    | Terraform CLI + AWS CLI                          |

---

## 2. Architecture

```
User Browser
    │
    ▼
React Frontend (port 5173)
    │  REST + SSE streaming
    ▼
FastAPI Backend (port 8000)
    ├── upload_controller     ← receives zip/tar
    ├── extract_controller    ← unzips to ./extracted/user_X/
    ├── analyze_controller    ← runs detector.py (orchestrator)
    ├── docker_ai_controller  ← calls LLM + runs docker/k8s
    ├── aws_deploy_controller ← calls Gemini for Terraform
    └── monitor_controller    ← reads k8s/AWS health
         │
    Detection Modules (app/utils/)
    ├── detector.py                ← orchestrator + re-export hub
    ├── detection_constants.py     ← shared constants & helpers
    ├── detection_language.py      ← language/framework detection
    ├── detection_ports.py         ← port detection (env, pkg, Docker)
    ├── detection_database.py      ← database detection & scoring
    ├── detection_services.py      ← service inference
    ├── command_extractor.py       ← Node.js/Python command extraction
    └── ml_analyzer.py             ← ML-based classification
         │
         ▼
    LLM Layer (app/LLM/)
    ├── llm_client.py         ← Ollama/Gemini adapters
    ├── docker_deploy_agent.py← system prompt + user message builder
    └── terraform_deploy_agent.py
         │
         ▼
    LLM provider
    ├── Ollama (http://localhost:11434)
    └── Gemini API (when configured)
         │
         ▼
    MongoDB (localhost:27017)
    └── DB: devops_autopilot → collection: projects
```

---

## 3. Configuration & Settings

All configuration lives in `backend-python/app/config/settings.py` and is loaded from `backend-python/.env`.

```python
# backend-python/.env (example)
MONGODB_URL=mongodb://localhost:27017
DATABASE_NAME=devops_autopilot
HOST=0.0.0.0
PORT=8000
UPLOAD_DIR=./uploads
EXTRACTED_DIR=./extracted
MAX_FILE_SIZE=104857600          # 100 MB
ENVIRONMENT=development

# LLM
OLLAMA_URL=http://localhost:11434/api/generate
LLM_MODEL_NAME=qwen2.5-coder:7b
LLM_TEMPERATURE=0.1
LLM_TOP_P=0.9
LLM_TIMEOUT=600                  # seconds
DOCKER_LLM_PROVIDER=ollama        # ollama | gemini
GEMINI_API_KEY=
GEMINI_API_BASE=https://generativelanguage.googleapis.com/v1beta
GEMINI_MODEL_NAME=gemini-2.5-flash
GEMINI_MAX_OUTPUT_TOKENS=8192
GEMINI_FALLBACK_MODEL_NAME=       # optional; only used when different from primary

# Docker Hub (optional, for push)
DOCKER_HUB_USERNAME=myuser
DOCKER_HUB_PASSWORD=mypassword

# Kubernetes
K8S_NAMESPACE=default
K8S_CLUSTER=docker-desktop
APP_REGISTRY_PREFIX=devops-autopilot
K8S_NODE_PORT_START=30001
K8S_NODE_PORT_END=32767

# AWS (optional, for Terraform)
AWS_PROFILE=my-terraform
AWS_DEFAULT_REGION=us-east-1
TERRAFORM_PATH=terraform
AWS_EC2_INSTANCE_TYPE=t3.micro
AWS_EC2_KEY_NAME=aws-deployment-devops
AWS_SSH_PRIVATE_KEY_PATH=/path/to/your-key.pem
```

> Note: `.env` values override `settings.py` defaults at runtime.

### Key Defaults

| Setting           | Value                                  | Purpose                   |
| ----------------- | -------------------------------------- | ------------------------- |
| `LLM_MODEL_NAME`  | `llama3.1:7b` (settings default)       | Local code-focused LLM    |
| `LLM_TEMPERATURE` | `0.1`                                  | Near-deterministic output |
| `LLM_TOP_P`       | `0.9`                                  | Nucleus sampling          |
| `LLM_TIMEOUT`     | `600s`                                 | 10-minute max wait        |
| `num_ctx`         | `16384` (non-stream) / `8192` (stream) | Context window            |
| `MAX_FILE_SIZE`   | `100 MB`                               | Upload cap                |
| `DOCKER_LLM_PROVIDER` | `ollama`                           | Docker agent provider: `ollama` or `gemini` |
| `GEMINI_MODEL_NAME` | `gemini-2.5-flash`                   | Primary Gemini model      |
| `GEMINI_FALLBACK_MODEL_NAME` | `None`                    | Optional fallback used only for Gemini 429/503 |
| `AWS_EC2_INSTANCE_TYPE` | `t3.micro`                       | EC2 instance type enforced after Terraform generation |
| `AWS_EC2_KEY_NAME` | `aws-deployment-devops`              | EC2 key pair name enforced for SSH |
| `AWS_SSH_PRIVATE_KEY_PATH` | `""` (empty — must be set explicitly; shown here as `/path/to/your-key.pem`) | Local PEM path used in `ssh_command` output |

---

## 4. API Endpoints (Full Reference)

All endpoints are prefixed with `/api`. Authentication via JWT Bearer token (except `/auth`).

### Auth Routes (`/api/auth`)

| Method | Path        | Description         | Inputs                          |
| ------ | ----------- | ------------------- | ------------------------------- |
| POST   | `/register` | Create user account | `username`, `email`, `password` |
| POST   | `/login`    | Get JWT token       | `username`, `password`          |
| GET    | `/me`       | Current user info   | Bearer token                    |

### Project Upload (`/api/projects`)

| Method | Path      | Description            | Inputs                                                      |
| ------ | --------- | ---------------------- | ----------------------------------------------------------- |
| POST   | `/upload` | Upload zip/tar file    | `file` (multipart), `project_name` (optional), Bearer token |
| GET    | `/`       | List all user projects | Bearer token                                                |
| GET    | `/{id}`   | Get project by ID      | Bearer token                                                |
| DELETE | `/{id}`   | Delete project + files | Bearer token                                                |

### Extraction (`/api/projects/{id}`)

| Method | Path                 | Description                    |
| ------ | -------------------- | ------------------------------ |
| POST   | `/extract`           | Unzip to `./extracted/user_X/` |
| GET    | `/files`             | List extracted files/folders   |
| GET    | `/extraction-status` | Check status                   |
| DELETE | `/cleanup`           | Remove extracted files         |

### Analysis (`/api/projects/{id}`)

| Method | Path        | Description         | Inputs                        |
| ------ | ----------- | ------------------- | ----------------------------- |
| POST   | `/analyze`  | Run detector.py     | `use_ml: bool` (default true) |
| GET    | `/analysis` | Get stored analysis | —                             |

### Docker AI (`/api/docker/{id}`)

| Method | Path               | Description |
| ------ | ------------------ | ----------- |
| GET    | `/context`         | Get metadata, Dockerfiles, compose files, k8s manifests, readiness status, and file tree |
| GET    | `/check-readiness` | Scan deployment files and auto-generate missing Docker/compose/k8s files via Gemini |
| POST   | `/chat`            | Free-form Docker deploy chat |
| GET    | `/chat/stream`     | SSE-streamed Docker deploy chat; generated files are parsed and written after the stream |
| GET    | `/file`            | Read a project file by relative path |
| POST   | `/file`            | Write a project file by relative path |
| POST   | `/folder`          | Create a folder under the project root |
| DELETE | `/path`            | Delete a file or folder under the project root |
| GET    | `/logs?action=build` | Stream Docker build logs |
| GET    | `/logs?action=run` | Stream Docker run logs |
| GET    | `/logs?action=push` | Stream Docker push logs |
| GET    | `/logs?action=k8s_deploy` | Stream Kubernetes deploy logs |

### AWS Deployment (`/api/aws/{id}`)

| Method | Path             | Description |
| ------ | ---------------- | ----------- |
| GET    | `/prerequisites` | Check Docker push, AWS credentials, Terraform CLI, compose file, and generated Terraform state |
| POST   | `/generate`      | Generate and validate `infra/main.tf` using Gemini plus backend post-processing |
| POST   | `/apply`         | Run `terraform init -input=false` and `terraform apply -auto-approve` as SSE |
| POST   | `/destroy`       | Run `terraform destroy -auto-approve` as SSE |
| POST   | `/scale-zero`    | Stop the deployed EC2 instance |
| POST   | `/scale-up`      | Start the stopped EC2 instance |
| GET    | `/status`        | Read stored AWS status plus live Terraform outputs when deployed |
| POST   | `/fix`           | Ask Gemini to repair `infra/main.tf` using Terraform error output |

### Monitoring (`/api/monitor`)

| Method | Path                    | Description |
| ------ | ----------------------- | ----------- |
| GET    | `/{project_id}/status`  | Get Kubernetes health, AWS health, recent k8s events, and all pods |
| POST   | `/{project_id}/heal`    | Trigger `kubectl rollout restart` for the project deployment |
| GET    | `/{project_id}/logs`    | Return recent pod logs |
| GET    | `/{project_id}/events`  | Return recent Kubernetes events |
| GET    | `/pods/all`             | Return all pods in the default namespace |
| GET    | `/{project_id}/logs/stream` | Stream live pod logs by SSE using `?token=<jwt>` |

---

## 5. User Upload Flow

```
User selects file → POST /api/projects/upload
    │
    ▼
upload_controller.py
    ├── Validates: allowed extensions = [.zip, .tar, .gz, .tgz]
    ├── Saves to: ./uploads/user_{username}/{timestamp}-{filename}
    ├── Creates MongoDB document with status = "uploaded"
    └── Returns: { project_id, project_name, file_size }
```

**Initial MongoDB document fields created at upload:**

```json
{
  "user_id": "<ObjectId>",
  "username": "alice",
  "project_name": "mern-blog",
  "file_name": "mern-blog.zip",
  "file_path": "./uploads/user_alice/20260220120000-mern-blog.zip",
  "file_size": 1048576,
  "status": "uploaded",
  "extracted_path": null,
  "metadata": {
    "schema_version": "ports_v2",
    "framework": "Unknown",
    "language": "Unknown",
    "runtime": null,
    "dependencies": [],
    "port": null,
    "build_command": null,
    "start_command": null,
    "env_variables": [],
    "dockerfile": false,
    "docker_compose": false,
    "detected_files": []
  }
}
```

---

## 6. Project Extraction Flow

```
POST /api/projects/{id}/extract
    │
    ▼
extract_controller.py
    ├── Sets status = "extracting"
    ├── Calls extractor.extract_file(file_path, project_id, user_workspace)
    │       └── Unzips to: ./extracted/user_{username}/{project_id}/
    ├── Records: files_count, folders_count
    ├── Sets status = "extracted"
    └── Returns: { extracted_path, files_count, folders_count }
```

---

## 7. Analysis Pipeline — Detection Modules

This is the heart of the detection system. Triggered by `POST /api/projects/{id}/analyze`.

### 7.0 Module Architecture

The detection logic is split across 5 focused modules + an orchestrator:

| Module                       | LOC  | Responsibility                                                                                                                        |
| ---------------------------- | ---- | ------------------------------------------------------------------------------------------------------------------------------------- |
| `detection_constants.py`     | ~220 | All shared constants (`LANGUAGE_INDICATORS`, `DB_INDICATORS`, `BACKEND_DEPS`, etc.) and helpers (`norm_path`, `_normalize_dep_name`)  |
| `detection_language.py`      | ~310 | `parse_dependencies_file`, `heuristic_language_detection`, `heuristic_framework_detection`, `get_runtime_info`                        |
| `detection_ports.py`         | ~530 | Port detection: `detect_ports_for_project`, `_detect_port_from_package_json`, `_scan_js_for_port_hint`, Docker compose/EXPOSE parsing |
| `detection_database.py`      | ~230 | `detect_databases`, `_infer_database_port`, `detect_db_and_ports`                                                                     |
| `detection_services.py`      | ~530 | `infer_services`, `_find_all_services_by_deps`, `_find_python_services`, root suppression, empty-shell dropping                       |
| `detector.py` (orchestrator) | ~620 | `detect_framework`, `find_project_root`, Docker/env helpers + **re-exports all symbols** from the modules above                       |

> **Import compatibility:** `detector.py` re-exports every symbol from the 5 modules, so all existing `from app.utils.detector import X` paths continue to work unchanged.

### 7.1 What `detect_framework()` Does

Runs a hybrid heuristic + ML pipeline on the extracted project folder:

```
detect_framework(project_path, use_ml=True)          # detector.py
    │
    ├── 0. _ensure_utf8_stdout()                      # detector.py (best-effort; avoids Unicode print aborts on non-UTF8 consoles)
    ├── 1. find_project_root()                        # detector.py
    ├── 2. heuristic_language_detection()              # detection_language.py
    │       ├── Scores: file extensions (+0.3), config files (+0.7), import patterns (+0.4)
    │       └── Languages: Python, JavaScript, TypeScript, Java, Go, Ruby, PHP
    ├── 3. heuristic_framework_detection()             # detection_language.py
    │       ├── Scores: package.json/requirements.txt deps (+0.8), file markers (+0.5)
    │       └── Frameworks: Express.js, React, Next.js, Flask, Django, FastAPI, Spring Boot...
    ├── 4. (if use_ml=True) ML supplement              # ml_analyzer.py
    ├── 5. Scan dependency files                       # detection_language.py
    │       parse_dependencies_file() for:
    │           package.json → deps + devDeps
    │           requirements.txt → skip comments, flags, extras notation
    │           pom.xml → <artifactId> tags
    │           go.mod → module paths
    ├── 6. detect_docker_files()                       # detector.py
    ├── 7. detect_env_variables()                      # detector.py
    ├── 8. detect_db_and_ports()                       # detection_database.py
    │       ├── detect_databases()                     # detection_database.py
    │       │       Databases: MongoDB, PostgreSQL, MySQL, SQLite, Redis
    │       └── detect_ports_for_project()             # detection_ports.py
    │               ├── _read_env_key_values()         # detector.py
    │               ├── _detect_port_from_package_json()# detection_ports.py
    │               ├── _scan_js_for_port_hint()       # detection_ports.py
    │               └── _parse_docker_compose_ports()  # detection_ports.py
    ├── 9. infer_services()                            # detection_services.py
    └── 10. deploy_blocked check                       # detector.py (see Section 11)
```

### 7.2 Full Output of `detect_framework()`

The result dict stored as `project.metadata` in MongoDB:

```json
{
  "schema_version": "ports_v2",
  "framework": "Express.js",
  "language": "JavaScript",
  "runtime": "node:20-alpine",
  "dependencies": ["express", "mongoose", "react", "..."],
  "port": 5000,
  "build_command": "npm install",
  "start_command": "node server.js",
  "entry_point": "server.js",
  "build_output": null,
  "env_variables": ["PORT", "MONGO_URI", "JWT_SECRET"],
  "dockerfile": false,
  "docker_compose": false,
  "detected_files": ["package.json"],
  "has_package_json": true,
  "has_requirements_txt": false,
  "has_manage_py": false,
  "static_only": false,

  "detection_confidence": {
    "language": 0.95,
    "framework": 0.8,
    "method": "heuristic"
  },

  "database": "MongoDB",
  "databases": ["MongoDB"],
  "database_detection": {
    "MongoDB": {
      "score": 1.8,
      "evidence": ["dependency:mongoose", "env:MONGO_URI"]
    }
  },
  "database_port": 27017,
  "database_is_cloud": false,
  "database_env_var": "MONGO_URI",

  "backend_runtime_port": 5000,
  "frontend_runtime_port": 5173,
  "backend_runtime_port_source": "service_env",
  "frontend_runtime_port_source": "service_vite_default",
  "backend_container_port": 5000,
  "frontend_container_port": 80,
  "backend_container_port_source": "service_service",
  "frontend_container_port_source": "service_nginx_default",
  "backend_port": 5000,
  "frontend_port": 5173,

  "docker_backend_ports": null,
  "docker_frontend_ports": null,
  "docker_database_ports": null,
  "docker_other_ports": null,
  "docker_backend_container_ports": null,
  "docker_frontend_container_ports": null,
  "docker_database_container_ports": null,
  "docker_other_container_ports": null,
  "docker_expose_ports": null,

  "services": [
    {
      "name": "backend",
      "path": "backend",
      "type": "backend",
      "runtime": "node:20-alpine",
      "port": 5000,
      "runtime_port": 5000,
      "container_port": 5000,
      "port_source": "env",
      "entry_point": "server.js",
      "env_file": "./backend/.env",
      "package_manager": { "manager": "npm", "has_lockfile": true }
    },
    {
      "name": "frontend",
      "path": "frontend",
      "type": "frontend",
      "runtime": "nginx:alpine",
      "port": 5173,
      "runtime_port": 5173,
      "container_port": 80,
      "dev_port": 5173,
      "build_output": "dist",
      "package_manager": { "manager": "npm", "has_lockfile": true }
    }
  ],

  "deploy_blocked": false,
  "deploy_blocked_reason": null,
  "backend_env_missing": false,
  "deploy_warning": null
}
```

### 7.3 Service Inference (`infer_services`)

For each detected service, the system builds a service definition dict with:

| Field                   | Source                                     | Description                                                                                          |
| ----------------------- | ------------------------------------------ | ---------------------------------------------------------------------------------------------------- |
| `name`                  | folder name                                | e.g. `"backend"`, `"frontend"`                                                                       |
| `path`                  | folder path relative to root               | e.g. `"backend"`, `"."`                                                                              |
| `type`                  | `_infer_service_type()`                    | `backend / frontend / worker / other`                                                                |
| `framework`             | service-local dependency inference         | Backend/monolith Node services infer framework from local deps (`express`→`Express.js`, `fastify`→`Fastify`, `@nestjs/*`→`NestJS`) with guarded fallback |
| `runtime`               | `infer_service_runtime_image_from_code() -> get_runtime_info(...)` | Per-service Docker base image inferred from service code path (`package.json` engines/volta, language/framework defaults); static frontend mode forces `nginx:alpine` |
| `port`                  | compatibility alias                        | Runtime port alias (`runtime_port`)                                                                  |
| `runtime_port`          | env file → source scan → framework default | App runtime/listen port                                                                              |
| `container_port`        | service strategy                           | Container-side port (`80` for nginx frontend, runtime for backend/monolith/worker)                   |
| `dev_port`              | frontend-only                              | Frontend dev server runtime port                                                                     |
| `port_source`           | detection method                           | `env / source / default / vite_default / cra_default / next_default / vue_default / angular_default` |
| `container_port_source` | frontend container rule                    | `service / compose / nginx_default / next_default / ssr_default / dev_server`                        |
| `entry_point`           | `extract_nodejs_commands()`                | e.g. `"server.js"`                                                                                   |
| `build_output`          | package.json scripts                       | e.g. `"dist"`, `"build"`, `".next"`                                                                  |
| `env_file`              | `.env` file existence check                | e.g. `"./backend/.env"`                                                                              |
| `package_manager`       | lockfile detection                         | `{manager: "npm", has_lockfile: true}`                                                               |

### 7.4 Database Detection Scoring

Each database is scored from multiple signal sources:

| Signal                                           | Weight |
| ------------------------------------------------ | ------ |
| Dependency match (e.g. `mongoose` → MongoDB)     | +1.0   |
| Env var key match (e.g. `MONGO_URI`)             | +0.8   |
| Env var value URL pattern (e.g. `mongodb://`)    | +0.4   |
| docker-compose image match (e.g. `mongo:latest`) | +0.7   |

The **highest scoring** database becomes `primary`.

#### 7.4.1 DB Env Merge + Reconciliation Rules

`detect_databases()` merges `.env` key-values in this precedence:

1. Frontend `.env` values
2. Backend `.env` values (override frontend)
3. Root `.env` values via `setdefault` (fill only missing keys)

Root fallback merge is applied again after the nested-structure `try` block, so root env keys are preserved even if nested detection fails.

Inside `infer_services()`, when backend DB extraction and root DB extraction disagree:

1. Backend DB info is computed first.
2. Root DB info is also computed (when backend path differs from project root).
3. If root DB info came from an explicit env URL (`source == "env"`) and backend did not, root DB info wins for `database_is_cloud`/`database_env_var`.
4. Scorer-authoritative DB type still overrides final `db_type`, and `default_port`/`docker_image` are recomputed to match the normalized type.

### 7.5 Port Detection Priority

For service-level backend/monolith detection (`extract_port_from_project`) priority is:

1. `.env` family keys (source: `"env"`) with precedence:
   - real env files first: `.env.local`, `.env.development`, `.env.production`, `.env`
   - template env files fallback-only: `.env.example`, `.env.sample`
2. Source scan (source: `"source"`) including:
   - `.listen(...)`
   - `process.env.PORT || N`
   - `process.env.PORT ?? N`
   - `parseInt(process.env.PORT || N)` / `parseInt(process.env.PORT ?? N)`
   - `Number(process.env.PORT) || N` / `Number(process.env.PORT) ?? N`
   - `const/let/var PORT = N`
3. Framework/language default (`Express/Fastify/Nest/Next → 3000`, `Vite → 5173`, `Python → 8000`) (source: `"default"`)

For project-level detection (`detect_ports_for_project`), package.json and docker-compose hints are also used to seed `backend_port` / `frontend_port`, then service-level values remain authoritative during consolidation.

For fullstack JS/TS repos, `backend/.env` can also contribute frontend explicit keys (`FRONTEND_PORT`, `CLIENT_PORT`, etc.) when frontend env values are otherwise missing.

Compose service-name classification treats `app` as backend-like (`_classify_docker_service`) to improve backend/other split in scraped repos.

Runtime/container consolidation in `detector.py` is deterministic:

1. Project-level runtime candidates are applied first as baseline.
2. Service-level runtime/container candidates are applied second.
3. Updates use strict source-rank comparison (`>`), so equal-rank ties do not overwrite prior values.
4. Services are ordered by type/depth/path before consolidation to avoid traversal-order drift.
5. If backend and frontend runtime ports collide while both service types exist, detector appends a `consistency_warnings` entry (non-blocking; no forced overwrite).

Backend framework assignment for Node backend/monolith services is intentionally guarded:

1. Prefer service-local dependency signals (`express`, `fastify`, `@nestjs/*`).
2. Only fallback to backend-capable project frameworks (`Express.js`, `Fastify`, `NestJS`).
3. Frontend frameworks (for example `React`) are not inherited by backend services.

### 7.6 Runtime Image Inference Contract (Code-Only)

Runtime image inference is intentionally centralized to `infer_service_runtime_image_from_code()` in `detection_services.py`.

Deterministic rules:

1. If `type=frontend` and `frontend_mode=static_nginx` -> `runtime="nginx:alpine"`.
2. Otherwise call `get_runtime_info(language, framework, service_path)` and use its `runtime` value.
3. If runtime still cannot be resolved -> fallback `runtime="alpine:latest"`.

Important invariants (for script edits):

- Runtime image inference must remain **code-signal-only** (`package.json`, language/framework detection, engines/volta).
- Runtime inference must **not** read Dockerfiles or compose values.
- Frontend static mode is the only branch that intentionally forces nginx runtime.

### 7.7 Compose-Unmatched Service Fallback (Detector Path)

When compose contains a build-context service that did not match an existing inferred service, `infer_services()` creates a new service row from that context path.

For non-frontend compose-unmatched services, language/framework selection is service-local before port/runtime inference:

1. If service context has `package.json` -> `svc_language="JavaScript"` and backend framework inferred from local deps (`_infer_node_backend_framework`).
2. Else if service context has Python markers (`requirements.txt`, `pyproject.toml`, `manage.py`) -> `svc_language="Python"`, `svc_framework="Unknown"`.
3. Else use project-level language + guarded backend framework fallback.

Then:

- Runtime port is inferred with `extract_port_from_project(context_path, svc_framework, svc_language)`.
- Runtime image is inferred with `infer_service_runtime_image_from_code(context_path, svc_type, svc_language, svc_framework)`.
- Compose ports still override when explicitly present.

---

## 8. AI / LLM Integration

### 8.1 LLM Client (`llm_client.py`)

LLM calls are routed through `backend-python/app/LLM/llm_client.py`.

- Docker deploy calls use `call_docker_llm()` / `call_docker_llm_stream()`.
- `DOCKER_LLM_PROVIDER=ollama` sends Docker requests to Ollama.
- `DOCKER_LLM_PROVIDER=gemini` sends Docker requests to Gemini.
- Terraform generation/fix uses Gemini directly through `call_gemini()` / `call_gemini_stream()`.
- `GEMINI_FALLBACK_MODEL_NAME` is optional and is only used when Gemini returns HTTP 429 or 503 and the fallback differs from the primary model.

**Ollama non-streaming call:**

```python
POST http://localhost:11434/api/generate
{
  "model": "qwen2.5-coder:7b",
  "prompt": "System: ...\n\nUser: ...\n\nAssistant:",
  "stream": false,
  "options": {
    "temperature": 0.1,
    "top_p": 0.9,
    "num_ctx": 16384
  }
}
```

**Ollama streaming call** (used when Docker provider is Ollama):

```python
POST http://localhost:11434/api/generate
{  "stream": true, "options": { "num_ctx": 8192 }  }
# SSE: yields {"token": "...", "done": false}
```

**Gemini call shape:**

```python
POST {GEMINI_API_BASE}/models/{GEMINI_MODEL_NAME}:generateContent?key=...
{
  "systemInstruction": { "parts": [{ "text": "..." }] },
  "contents": [{ "role": "user", "parts": [{ "text": "..." }] }],
  "generationConfig": {
    "temperature": 0.1,
    "topP": 0.9,
    "maxOutputTokens": 8192
  }
}
```

Gemini streaming is implemented as a compatibility adapter: the backend calls Gemini once and emits the full response as a token chunk followed by `done=true`.

### 8.2 System Prompt (Full — `docker_deploy_agent.py`)

The system prompt is fixed and instructs the LLM on exactly how to generate Docker configs:

````
You are a Docker configuration generator. Produce CORRECT, WORKING Docker configs with ZERO errors.
Use ONLY values from the input. Never assume or invent values.

STEP 1: EXTRACT VALUES
  - PROJECT_NAME, RUNTIME, BACKEND_RUNTIME_PORT, FRONTEND_RUNTIME_PORT, BACKEND_CONTAINER_PORT, FRONTEND_CONTAINER_PORT, DATABASE, DATABASE_PORT, DATABASE_IS_CLOUD
  - Per-service: name, path, type, runtime, runtime_port, container_port, entry_point, build_output, env_file, package_manager
  - SCHEMA_VERSION is fixed to `ports_v2`; use runtime/container fields only for port semantics.

STEP 2: DETERMINE TYPE PER SERVICE
  - STATIC_ONLY=True or RUNTIME contains "nginx" → Static site
  - type=frontend AND (container_port_source in [next_default, ssr_default] OR build_output in [.next,.nuxt,.svelte-kit,.astro,.output,.output/server,build/server]) → Frontend SSR/hybrid mode (Node runtime, no nginx)
  - type=frontend AND build_output set (non-SSR) → Frontend static build (React/Vue + nginx)
  - type=frontend AND build_output missing/empty → Frontend dev-server mode (no nginx; runtime container port)
  - type in (backend, monolith, worker) → Runtime app service (single-stage unless explicitly Python override in message)

STEP 3: GENERATE DOCKERFILES

  BACKEND (single-stage):
    FROM {service.runtime or RUNTIME}
    WORKDIR /app
    COPY package*.json ./
    RUN {INSTALL_CMD}         ← npm ci / npm install / yarn install --frozen-lockfile
    COPY . .
    ENV PORT={service.container_port}   ← ALWAYS set env fallback
    EXPOSE {service.container_port}
    CMD {CMD_ARRAY}           ← ["node", "{entry_point}"] or ["npm", "start"]

  PYTHON BACKEND (single-stage, pip-optimized):
    FROM {service.runtime or RUNTIME}
    WORKDIR /app
    COPY requirements.txt ./
    RUN pip install --no-cache-dir --timeout 120 --retries 5 -r requirements.txt
    COPY . .
    ENV PORT={service.container_port}
    EXPOSE {service.container_port}
    CMD {CMD_ARRAY}

  FRONTEND (multi-stage required):
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

  FRONTEND DEV-SERVER (no build output):
    FROM {service.runtime or RUNTIME}
    WORKDIR /app
    COPY package*.json ./
    RUN {INSTALL_CMD}
    COPY . .
    ENV PORT={service.container_port}
    EXPOSE {service.container_port}
    CMD {CMD_ARRAY}

  FRONTEND SSR/HYBRID (Next.js/Nuxt/SvelteKit/Remix/Astro — no nginx):
    FROM {service.runtime or RUNTIME} AS builder / ... / FROM {service.runtime or RUNTIME}
    COPY --from=builder /app/{BUILD_OUTPUT} ./{BUILD_OUTPUT}
    COPY --from=builder /app/node_modules ./node_modules
    COPY --from=builder /app/package.json ./
    ENV PORT={service.container_port}
    EXPOSE {service.container_port}
    CMD ["npm", "start"]

STEP 4: GENERATE docker-compose.yml
  - Backend: image + build + ports + env_file (if present) + depends_on database
  - Frontend: image + build + ports (host runtime port mapped to frontend container port) + depends_on backend
  - Database container: ONLY if DATABASE_IS_CLOUD=False
  - No "version:" field in compose
  - All services must have BOTH "image:" and "build:"

STEP 5: VALIDATE (check single-stage, correct ports, compose rules)

STEP 6: RESPOND
  FORMAT:
    STATUS: Generated | Valid | Invalid
    REASON: summary
    GENERATED FILES:
      **{path}/Dockerfile**
      ```dockerfile … ```
      **docker-compose.yml**
      ```yaml … ```
````

### 8.3 User Message Construction (`build_deploy_message`)

Every LLM call constructs a structured user message using a fixed `ports_v2` contract. Core blocks:

> `build_deploy_message()` still normalizes legacy aliases internally for compatibility, but LLM-facing prompt fields are canonical (`*_RUNTIME_PORT` + `*_CONTAINER_PORT`) to avoid contradictory supervision.

```
1. SCHEMA_VERSION: ports_v2
2. MODE: GENERATE_MISSING | VALIDATE_EXISTING
3. PROJECT_NAME: {name} ← USE IN: image: {name}-backend:latest
4. Project: {name}
5. Dockerfile count summary
6. Compose file count summary
7. === CONFIGURATION VALUES ===
   SCHEMA_VERSION: ports_v2
   RUNTIME: node:20-alpine ← USE IN: FROM node:20-alpine
   BACKEND_RUNTIME_PORT: 5000 ← backend host/runtime port
   BACKEND_CONTAINER_PORT: 5000 ← backend EXPOSE/container port
   FRONTEND_RUNTIME_PORT: 5173 ← frontend host/runtime port
    FRONTEND_CONTAINER_PORT: 80 ← frontend container port (80 for static nginx; runtime port for SSR/dev-server)
   DATABASE: MongoDB
   DATABASE_PORT: 27017
   DATABASE_IS_CLOUD: True | False
   DATABASE_ENV_VAR: MONGO_URI
   BUILD_COMMAND: npm install
   START_COMMAND: node server.js
   ENTRY_POINT: server.js
   BUILD_OUTPUT: dist
   Environment variables: PORT, MONGO_URI, JWT_SECRET
   Dependencies: express, mongoose, react ...
8. Existing Dockerfile contents (if any)
9. Existing docker-compose.yml contents (if any)
10. File tree (depth=4, max 200 entries)
11. Source file snippets when provided by controller/service auto-generation paths
12. Build/Run logs (or "none yet")
   13. ⚠️⚠️⚠️ SERVICE DEFINITIONS (per service):
   - name: backend, path: backend, type: backend,
      runtime: node:20-alpine (USE IN Dockerfile FROM),
      runtime_port: 5000, container_port: 5000 (USE: "5000:5000"),
       entry_point: server.js, env_file: ./backend/.env,
       package_manager: npm (USE: npm ci)
   - name: frontend, path: frontend, type: frontend,
      runtime: nginx:alpine (USE IN Dockerfile FROM),
      runtime_port: 5173, container_port: 80 (USE: "5173:80"),
        build_output: dist (USE /app/dist), package_manager: npm (USE: npm ci, npm run build)
14. User message: "Generate all required Docker files..."
    (auto-enhanced when mode=GENERATE_MISSING and user said only "generate")
15. Final instruction line: "Respond with STATUS/REASON/FIXES or GENERATED DOCKERFILES/LOG ANALYSIS."
```

Service-line emission rules in `build_deploy_message()`:

1. Always emit `runtime_port` + `container_port` for each service.
2. Emit `runtime` when present (preferred Dockerfile `FROM` hint).
3. Emit `frontend_mode` for frontend services (`static_nginx`, `ssr`, `dev_server`) to disambiguate Dockerfile branching.
4. Keep `entry_point` service-scoped; do not fallback to project-level entry point for multi-service repos.

#### 8.3.1 Sample Prompt Sent To The LLM

The following is a representative `user` message payload produced by `build_deploy_message()`:

```text
SCHEMA_VERSION: ports_v2

MODE: GENERATE_MISSING

PROJECT_NAME: mern-blog ← USE IN: image: mern-blog-backend:latest, image: mern-blog-frontend:latest

Project: mern-blog

Dockerfiles detected: 0

Compose files detected: 0

=== CONFIGURATION VALUES (USE THESE EXACT VALUES) ===
SCHEMA_VERSION: ports_v2 ← REQUIRED CONTRACT FOR PORT FIELDS
RUNTIME: node:20-alpine ← USE IN: FROM node:20-alpine
BACKEND_RUNTIME_PORT: 5000 ← USE IN: backend host/runtime port
BACKEND_CONTAINER_PORT: 5000 ← USE IN: backend EXPOSE/container side
FRONTEND_RUNTIME_PORT: 5173 ← USE IN: frontend host/runtime port
FRONTEND_CONTAINER_PORT: 80 ← USE IN: frontend container side (80 for static nginx; runtime for SSR/dev-server)
DATABASE: MongoDB
DATABASE_PORT: 27017
FRAMEWORK: Express.js
LANGUAGE: JavaScript
DATABASE_IS_CLOUD: True ← DO NOT add database container! Just pass MONGO_URI to backend
DATABASE_ENV_VAR: MONGO_URI
Environment variables: PORT, MONGO_URI, JWT_SECRET
Dependencies: express, mongoose, react
=== END CONFIGURATION VALUES ===

No Dockerfiles detected.

No docker-compose files detected.

[dir] backend
[file] backend/package.json
[file] backend/server.js
[dir] frontend
[file] frontend/package.json

Build/Run logs: none yet.

⚠️⚠️⚠️ SERVICE DEFINITIONS (OVERRIDES metadata for multi-service!) ⚠️⚠️⚠️
For each service, use ITS entry_point (relative to service dir), NOT metadata.entry_point!
- name: backend, path: backend, type: backend, runtime: node:20-alpine (USE IN Dockerfile FROM: node:20-alpine), runtime_port: 5000, container_port: 5000 (from env - USE: "5000:5000"), entry_point: server.js (USE THIS IN CMD: node server.js), env_file: ./backend/.env (ADD TO COMPOSE: env_file: ['./backend/.env']), package_manager: npm (USE: npm ci)
- name: frontend, path: frontend, type: frontend, runtime: nginx:alpine (USE IN Dockerfile FROM: nginx:alpine), runtime_port: 5173, container_port: 80 (USE: "5173:80"), build_output: dist (USE /app/dist), package_manager: npm (USE: npm ci, npm run build)

User message: Generate all required Docker files:
1. Create a Dockerfile for EACH service directory (use service runtime when provided, otherwise RUNTIME, plus runtime_port/container_port values provided above)
2. Create docker-compose.yml at project root (with image: and build: fields for ALL services)
Use EXACT values from the input. Provide complete file contents.

Respond with STATUS/REASON/FIXES or GENERATED DOCKERFILES/LOG ANALYSIS.
```

### 8.4 Mode Auto-Detection

| Condition                                          | Mode                |
| -------------------------------------------------- | ------------------- |
| Dockerfiles or docker-compose.yml exist in project | `VALIDATE_EXISTING` |
| No Docker files found                              | `GENERATE_MISSING`  |

### 8.5 Logging Policy (LLM Paths)

- Docker prompt payloads are **not** printed to logs in production handlers.
- Streaming/non-streaming Docker chat paths avoid metadata dump logs to reduce noise and accidental prompt leakage.

---

## 9. Docker AI Controller — Deploy Flow

`docker_ai_controller.py` orchestrates the full Docker deploy cycle:

### 9.1 Fetching Context (`GET /api/docker/{id}/context`)

1. Validates project ownership
2. Resolves `extracted_path` → `project_root` (handles nested zip structure)
3. Uses `project_root` consistently for:
   - Dockerfile/compose discovery
   - File tree generation
   - file read/write/create/delete endpoints
4. Applies shared runtime-hint enrichment for all services:
   - Re-check `.env` / `.env.local` / `.env.production`
   - Backfills backend/monolith `entry_point` if missing
   - Backfills per-service `runtime` **only when missing** using `infer_service_runtime_image_from_code()` (does not overwrite existing runtime)
   - For frontend runtime backfill, infers/uses `frontend_mode` (`static_nginx` / `ssr` / `dev_server`) from service metadata fields
5. Recalculates `deploy_blocked` / `deploy_warning` based on current disk state using backend-like types (`backend`, `monolith`)
6. Returns: `metadata`, `dockerfiles`, `compose_files`, `k8s_files`, readiness status, `file_tree`, `k8s_node_port`, and `image_repo`

### 9.2 Docker Chat / Streaming Generation (`GET /chat/stream`)

1. Retrieves project + metadata from MongoDB
2. Resolves `extracted_path` to `project_root`
3. Collects Dockerfiles, compose files, k8s manifests, file tree, and source snippets
4. Runs the same shared runtime-hint enrichment used by `/context`
5. Calls `run_docker_deploy_chat_stream()` through the configured Docker LLM provider
6. Streams tokens to the frontend via Server-Sent Events
7. After the stream finishes, the backend parses generated Docker files from the LLM output
8. Parsed paths are remapped with `remap_generated_docker_paths()`
9. Generated Dockerfiles/compose files are validated before writing
10. Valid generated files are written under the resolved project root and the log prints file name plus absolute location

> Note: Runtime-hint enrichment is response-time normalization only in controller paths; it is not automatically persisted back to MongoDB unless a separate write/update path does so.

### 9.3 Build / Run / Push / Kubernetes Deploy

These use Python `subprocess` calls to the Docker CLI and stream output line-by-line:

```python
# Build
docker compose -f docker-compose.yml build
# or, when no compose exists, sequential docker build for discovered Dockerfiles

# Run
docker compose -f docker-compose.yml up --force-recreate
# or docker run for single-image fallback

# Push
docker tag {image} {hub_username}/{image}
docker push {hub_username}/{image}

# Kubernetes deploy
kubectl apply -f k8s/deployment.yaml
kubectl apply -f k8s/service.yaml
```

Build-specific behavior in `docker_service.py`:

1. Docker login is attempted before build/push when Docker Hub credentials are configured.
2. If `metadata.backend_env_missing` is true and root `.env` is missing, the build flow asks Gemini to generate a root `.env` with placeholder values and logs that it must be reviewed before production.
3. If no Dockerfiles are found, the build flow asks Gemini to generate Dockerfiles plus `docker-compose.yml`, validates the result, writes the files, logs their locations, then builds.
4. If compose exists, `docker compose build` runs in the compose directory.
5. If compose does not exist, discovered Dockerfiles are built one-by-one.

Run-specific behavior:

1. If compose exists, `docker compose up` is used.
2. If multiple Dockerfiles exist and compose is missing, Gemini generates `docker-compose.yml`, the backend validates it with `docker compose config`, writes it to project root, then runs compose.
3. For single-image fallback, container ports are inferred from Docker metadata and host port conflicts are resolved before running.

Push-specific behavior:

1. Docker login is attempted before pushing when Docker Hub credentials are configured.
2. For compose projects, `docker compose config --format json` is used to resolve services.
3. Database services are skipped and are not pushed to Docker Hub.
4. App services are tagged and pushed as `{DOCKER_HUB_USERNAME}/{APP_REGISTRY_PREFIX}-{project}-{service}:latest` using the shared `utils/image_naming.py` helper.
5. The push stream treats internal `docker tag` / `docker push` command exits as `command_complete`, not final stream completion, so the browser does not close before every service image is pushed.
6. The push stream emits one final `complete=true` event only after all selected app images push successfully. That final event includes `pushed_images`.

### 9.4 Deployment Readiness (`GET /check-readiness`)

`deployment_readiness_controller.py` checks and prepares deployment-critical files:

1. Requires project status `analyzed` or `completed`
2. Resolves the real project root with `find_project_root()`
3. Scans for expected Dockerfiles, `docker-compose.yml`, `k8s/deployment.yaml`, `k8s/service.yaml`, and `.env`
4. Generates missing Docker/compose files through `run_docker_deploy_chat()`
5. Generates missing Kubernetes manifests through `run_k8s_manifest_generation()`
6. Does not overwrite a missing real `.env`; it writes `.env.template` instead when needed
7. Returns `present_before`, `missing_before`, `generated`, `still_missing`, `skipped`, `write_errors`, `k8s_node_port`, and `image_repo`

---

## 10. Environment File (.env) Handling

### 10.1 During Analysis (detection modules)

The detection modules read `.env` files at **two layers** — root and nested service directories:

**Files read for key-value detection** (`_read_env_key_values` in `detector.py`):

- `.env`
- `.env.local`
- `.env.development`
- `.env.production`
- `.env.test`
- `.env.example`

**Files read for key-only listing** (`detect_env_variables` in `detector.py`):

- `.env`
- `.env.example`
- `.env.sample`
- `.env.local`

**For DB detection** (`detection_database.py`), the system also reads nested `.env` files and merges key-value pairs before scoring, with explicit precedence:

1. frontend values
2. backend values (override frontend)
3. root values as fallback only (`setdefault`)

The root fallback merge is guaranteed even when nested fullstack detection throws.

Cloud/local classification in `extract_database_info()` uses hostname-aware rules:

- Local: `localhost`, `127.0.0.1`, `host.docker.internal`, known compose hostnames (`mongo`, `postgres`, `redis`, etc.), private IPs, `.local/.internal/.localhost/.test`, and single-label hostnames.
- Cloud: explicit cloud URL patterns (Atlas, RDS, Supabase, Railway, etc.).
- Unknown hostnames default to cloud (conservative policy).

Evaluation order is explicit: local-host detection is checked first; cloud pattern checks run only when host is not local-like.

**Port from `.env`** — these keys are checked (priority order):

| Keys → Backend Port                       | Keys → Frontend Port                                                                               | Keys → Generic (fallback) |
| ----------------------------------------- | -------------------------------------------------------------------------------------------------- | ------------------------- |
| `BACKEND_PORT`, `SERVER_PORT`, `API_PORT` | `FRONTEND_PORT`, `CLIENT_PORT`, `VITE_PORT`, `REACT_APP_PORT`, `NEXT_PUBLIC_PORT`, `VITE_DEV_PORT` | `PORT`                    |

For `extract_port_from_project()` / `extract_frontend_port()` (`command_extractor.py`), env files are read with real-env-first precedence and templates as fallback-only (same ordering documented in Section 7.5).

### 10.2 During Docker Generation (LLM prompt)

When a service has an `.env` file, the LLM is instructed to add it to `docker-compose.yml`:

```yaml
backend:
  env_file:
    - ./backend/.env    ← added only if env_file is set in service definition
```

If the backend service has **no `.env` file**, the LLM is still told to `ENV PORT={service.container_port}` in the Dockerfile as a fallback so the container does not fail if no env is provided at runtime.

The build flow no longer blocks only because `.env` is missing. When `metadata.backend_env_missing` is true and root `.env` is absent, `docker_service.py` asks Gemini to generate a root `.env` with placeholder/default values before Docker build. The log warns the user to review secrets before production.

The readiness flow is more conservative: if a real `.env` is missing, it writes `.env.template` instead of overwriting/creating real secrets.

### 10.3 Dynamic Re-Check on Deploy Page

Every time the user opens the Deploy page (`GET /api/docker/{id}/context`), the system **re-checks disk** for service runtime hints — not relying on the stored MongoDB metadata:

1. `.env` / `.env.local` / `.env.production` re-check per service
2. backend/monolith `entry_point` backfill (when metadata is missing it)
3. per-service `runtime` backfill (only when `runtime` is missing)

The same shared enrichment is also executed in Docker chat and streaming generation handlers, so all LLM paths see the same service hints.

This allows a user to:

1. Upload project (no `.env`)
2. System shows a non-blocking `.env` warning
3. User manually adds `.env` to the extracted folder on disk, or lets the build flow auto-generate root `.env`
4. User refreshes Deploy page → system detects `.env`, clears the warning

**`.env` files checked per service directory:**

- `.env`
- `.env.local`
- `.env.production`

---

## 11. Deploy Blocked / Deploy Warning Logic

This logic runs in **two places** and produces consistent output both times:

### Where it runs:

1. `detector.py → detect_framework()` — at analysis time
2. `docker_ai_controller.py → get_docker_context_handler()` — at deploy page load

### Decision Matrix:

| Condition                                             | `deploy_blocked` | `deploy_warning`        | UI Effect                                      |
| ----------------------------------------------------- | ---------------- | ----------------------- | ---------------------------------------------- |
| Backend/Monolith exists + DB detected + **no .env**   | `False`          | `"No .env file detected. One will be auto-generated before build."` | Amber banner, buttons remain enabled |
| Backend/Monolith exists + **no DB** + **no .env**     | `False`          | `"No .env detected. Auto-generating template..."` | Amber banner, buttons remain enabled |
| Backend/Monolith exists + **.env present**            | `False`          | `null`                  | No banner                                      |
| Frontend-only / no backend-like services              | `False`          | `null`                  | No banner                                      |

### Output fields set on `metadata`:

```json
{
  "deploy_blocked": false,
  "deploy_blocked_reason": null,
  "backend_env_missing": true,
  "deploy_warning": "No .env file detected. One will be auto-generated before build."
}
```

---

## 12. Authentication

- **JWT-based** via `python-jose`
- Token issued on `/api/auth/login`, valid for 30 days
- Passed as `Authorization: Bearer <token>` header
- `decode_access_token()` validates signature + expiry + user existence
- All project endpoints check `user_id` ownership before operating

---

## 13. Database Schema (MongoDB)

**Collection: `projects`**

```json
{
  "_id": "ObjectId",
  "user_id": "string (user ObjectId)",
  "username": "string",
  "project_name": "string",
  "file_name": "string",
  "file_path": "string (absolute path)",
  "file_size": "number (bytes)",
  "upload_date": "datetime",
  "status": "uploaded | extracting | extracted | analyzing | analyzed | completed | failed",
  "extracted_path": "string | null",
  "extraction_date": "datetime | null",
  "files_count": "number",
  "folders_count": "number",
  "extraction_logs": ["string"],
  "metadata": {
    "schema_version": "ports_v2",
    "framework": "Express.js | React | Flask | ...",
    "language": "JavaScript | Python | ...",
    "runtime": "node:20-alpine | python:3.11-slim | ...",
    "dependencies": ["express", "mongoose", "..."],
    "port": 5000,
    "backend_runtime_port": 5000,
    "frontend_runtime_port": 5173,
    "backend_runtime_port_source": "service_env",
    "frontend_runtime_port_source": "service_vite_default",
    "backend_container_port": 5000,
    "frontend_container_port": 80,
    "backend_container_port_source": "service_service",
    "frontend_container_port_source": "service_nginx_default",
    "backend_port": 5000,
    "frontend_port": 5173,
    "database_port": 27017,
    "database": "MongoDB | PostgreSQL | Unknown",
    "database_is_cloud": false,
    "database_env_var": "MONGO_URI",
    "env_variables": ["PORT", "MONGO_URI"],
    "dockerfile": false,
    "docker_compose": false,
    "deploy_blocked": false,
    "deploy_blocked_reason": null,
    "backend_env_missing": false,
    "deploy_warning": null,
    "services": [
      {
        "name": "backend",
        "path": "backend",
        "type": "backend",
        "runtime": "node:20-alpine",
        "port": 5000,
        "runtime_port": 5000,
        "container_port": 5000,
        "port_source": "env",
        "entry_point": "server.js",
        "env_file": "./backend/.env",
        "package_manager": { "manager": "npm", "has_lockfile": true }
      }
    ],
    "detection_confidence": {
      "language": 0.95,
      "framework": 0.8,
      "method": "heuristic"
    }
  },
  "analysis_date": "datetime | null",
  "analysis_logs": ["string"],
  "docker_push_success": false,
  "aws_deployment_status": "not_deployed | terraform_generated | deploying | deployed | failed | destroying | destroy_failed | scaled_to_zero",
  "aws_region": "string | null",
  "aws_frontend_url": "string | null",
  "aws_backend_url": "string | null",
  "aws_instance_id": "string | null",
  "aws_last_deployed": "datetime | null",
  "aws_terraform_path": "string | null",
  "logs": [{ "message": "string", "timestamp": "datetime" }],
  "created_at": "datetime",
  "updated_at": "datetime"
}
```

---

## 14. Frontend Integration

**Files:** `devops-autopilot-frontend/src/pages/DeployPage.tsx`, `src/components/AWSDeployPanel.tsx`, `src/api/client.ts`

### Key UI States

| State          | Condition                                   | Component shown                   |
| -------------- | ------------------------------------------- | --------------------------------- |
| Deploy Blocked | `metadata.deploy_blocked === true`          | Red banner + disabled buttons |
| Deploy Warning | `!deploy_blocked && deploy_warning != null` | Amber banner + enabled buttons |
| Normal         | Both false/null                             | Clean UI, all buttons enabled |

### TypeScript Types (`src/types/api.ts`)

```typescript
// Deployment blocking flags
deploy_blocked?: boolean;
deploy_blocked_reason?: string | null;
backend_env_missing?: boolean;
deploy_warning?: string | null;   // non-blocking warning
```

### SSE Streaming (Deploy Page)

The frontend uses these streaming paths:

1. Docker deploy chat: `GET /api/docker/{id}/chat/stream?message=...&token=...`
2. Docker logs: `GET /api/docker/{id}/logs?action=build|run|push|k8s_deploy&token=...`
3. Monitoring logs: `GET /api/monitor/{id}/logs/stream?token=...`
4. AWS operations: fetch streaming against `/api/aws/{id}/{apply|destroy|scale-zero|scale-up}`

`streamAWSTerraform()` accepts `apply`, `destroy`, `scale-zero`, and `scale-up`. It calls `onComplete()` once, even when the backend sends an explicit `complete` event.

### AWS UI Wiring

On Deploy page load, `DeployPage.tsx` also calls `checkAWSPrerequisites(projectId)` to prefill `awsConfig.docker_repo_prefix` from `docker_hub_username` and to initialize `terraformExists` / `awsStatus` from the backend.

The current `DeployPage.tsx` AWS config state contains `aws_region`, `docker_repo_prefix`, `db_engine`, `mongo_db_url`, and `desired_count`. The visible Cloud panel exposes the region selector, then:

1. `GEN_INFRA` calls `apiClient.generateTerraform(projectId, awsConfig)`, sets local status to `terraform_generated`, and refreshes the file explorer.
2. `DEPLOY_CLOUD` calls `streamAWSTerraform(projectId, "apply", ...)` and sets local status to `deployed` when streaming completes.

`AWSDeployPanel.tsx` provides a fuller generate/apply/destroy/scale-zero component used from project details. Its form sends region, Docker repo prefix, optional DB engine/URL, and desired count.

---

## 15. AWS Terraform Deployment

**Files:** `app/LLM/terraform_deploy_agent.py`, `aws_deploy_controller.py`, `aws_service.py`

The current AWS flow generates Terraform for a single EC2 Free Tier instance running `docker-compose`.

### 15.1 Prerequisites

`GET /api/aws/{project_id}/prerequisites` checks:

1. Project ownership
2. Docker images have been pushed (`docker_push_success`)
3. AWS credentials through `verify_aws_credentials()`
4. Terraform CLI is installed at `settings.TERRAFORM_PATH`
5. Real project root exists after `find_project_root()`
6. `docker-compose.yml` exists under the resolved project root
7. Existing `infra/main.tf` status

Credential behavior in code:

- If process env `AWS_PROFILE` is set, the backend runs `aws sts get-caller-identity --profile <profile>`.
- Otherwise it checks process env `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` are present.
- Region is read from process env `AWS_DEFAULT_REGION` / `AWS_REGION`, falling back to `us-east-1`.

Response fields:

```json
{
  "can_deploy": true,
  "issues": [],
  "project_name": "app",
  "aws_region": "us-east-1",
  "docker_push_success": true,
  "docker_hub_username": "myuser",
  "terraform_exists": false,
  "aws_deployment_status": "not_deployed"
}
```

This endpoint is a readiness check. `generate_terraform_handler()` performs its own project/ownership/path checks, but it does not call the prerequisites handler before generating Terraform.

### 15.2 Terraform Generation

`POST /api/aws/{project_id}/generate` accepts this backend request model:

| Field | Default | Used for |
| ----- | ------- | -------- |
| `aws_region` | `us-east-1` | Terraform provider region and availability zone prefix |
| `docker_repo_prefix` | required | Docker image prefix used in generated service image names |
| `db_engine` | `None` | Optional database context passed to Gemini |
| `mongo_db_url` | `None` | DB URL when Mongo is selected |
| `rds_db_url` | `None` | DB URL for non-Mongo DB engines |
| `desired_count` | `1` | Passed to the prompt for compatibility; EC2/docker-compose apply does not pass it as a Terraform variable |
| `extra_env` | `None` | Merged into every detected service env dict before prompting |
| `key_name` | `aws-deployment-devops` | EC2 key pair name; generated Terraform must include `key_name = var.key_name` |
| `ssh_private_key_path` | `/path/to/your-key.pem` (placeholder — must be supplied explicitly) | Local PEM path used by `ssh_command` output |
| `allowed_ssh_cidr` | `0.0.0.0/0` | Security group SSH CIDR default |
| `app_port` | auto-detected | Primary app port used for `var.app_port` and app URL output |
| `root_volume_size` | `20` | Root EBS volume size in GB |

Generation flow:

1. Resolves `project.extracted_path` to the real project root with `find_project_root()`
2. Builds service inputs from `metadata.services` when available and skips `type=database`
3. Uses service port priority: `container_port -> runtime_port -> port -> 3000`
4. Falls back to legacy fullstack/frontend/backend service inference when `metadata.services` is missing
5. Reads service env vars through `get_service_env_vars_for_terraform()`
6. Merges `extra_env` into every service env dict
7. Reads root `docker-compose.yml` plus service `.env`, `.env.local`, or `.env.production` contents
8. Normalizes app-service compose images for EC2 pulls using the same pushed image naming rule as Docker push: `{docker_hub_user}/{APP_REGISTRY_PREFIX}-{project}-{service}:latest`
9. Sends project name, AWS region, EC2 instance type, Docker repo prefix, resolved image repo, services, env vars, normalized compose, existing env files, SSH config, app port, and root volume size to Gemini
10. Post-processes and validates the returned HCL before writing
11. Writes the final Terraform to `{project_root}/infra/main.tf`
12. Stores `aws_deployment_status="terraform_generated"`, `aws_region`, and `aws_terraform_path`
13. Logs `Terraform configuration generated: main.tf -> <absolute path>`

The Terraform prompt requires:

- AWS provider `~> 5.0`
- Amazon Linux 2023 AMI via `data "aws_ami"`
- Variables for `project_name`, `aws_region`, `docker_repo_prefix`, `key_name`, `ssh_private_key_path`, `allowed_ssh_cidr`, `app_port`, and `root_volume_size`
- VPC, internet gateway, public subnet with `map_public_ip_on_launch = true`, route table, route table association, and security group
- Security group ingress for SSH 22, HTTP 80, HTTPS 443, `var.app_port`, and every host-side port from compose mappings
- Single `aws_instance` using `settings.AWS_EC2_INSTANCE_TYPE` (`t3.micro` by default)
- `vpc_security_group_ids`, `key_name = var.key_name`, and `root_block_device` with `gp3` and `volume_size = var.root_volume_size`
- `user_data` that logs to `/var/log/devops-autopilot-userdata.log`, installs Docker, installs docker-compose, logs into Docker Hub with `DOCKER_USERNAME` / `DOCKER_PASSWORD` placeholders, writes env files and compose YAML, pulls images, and runs `docker-compose up -d`
- Outputs requested by the prompt: `instance_public_ip`, `public_dns`, `instance_id`, `vpc_id`, `app_url`, `frontend_url`, `backend_url`, and `ssh_command`
- IAM permission comments near the top and debug command comments near the bottom

If an existing root `docker-compose.yml` is available, AWS generation reads it into memory and rewrites only non-database service image references before prompting Gemini. App services are pointed at the Docker Hub tags that `push_image_stream()` publishes, and `build:` blocks are removed from the AWS-embedded compose because EC2 receives only the compose file, not the source tree. Database images such as `mongo:latest`, `postgres:latest`, `mysql:latest`, and `redis:alpine` are left unchanged. The prompt then tells Gemini to embed that normalized compose content exactly in `user_data` and create provided env files before the compose file. If no compose file is provided, the prompt tells Gemini to generate compose content from the service/env inputs using the resolved image repo.

Before Terraform is generated, AWS generation collects the expected non-database app images from the normalized compose and runs `docker manifest inspect <image>` for each one. If any expected Docker Hub manifest is missing, generation fails with HTTP 400 before EC2 can later fail with `manifest unknown`.

Post-processing in `aws_deploy_controller.py`:

1. `_inject_docker_credentials()` replaces `DOCKER_USERNAME` / `DOCKER_PASSWORD` placeholders when Docker Hub credentials are configured.
2. If placeholders exist but credentials are missing, generation fails with HTTP 400.
3. `utils/image_naming.py` builds the same Docker image base for Docker push, deployment readiness, and AWS generation from `DOCKER_HUB_USERNAME`, `APP_REGISTRY_PREFIX`, and the sanitized project name.
4. `_normalize_compose_images_for_aws()` rewrites app service images to `{image_repo}-{service}:latest`, removes app `build:` blocks, and leaves database images unchanged.
5. `_expected_aws_app_images()` extracts the exact non-database images EC2 will pull.
6. `_validate_docker_hub_manifests_exist()` blocks Terraform generation when Docker Hub does not have one of those app image manifests.
7. `_enforce_ec2_instance_type()` rewrites the first `instance_type = "..."` to `settings.AWS_EC2_INSTANCE_TYPE`.
8. `_enforce_ssh_key_settings()` sets `key_name` to `aws-deployment-devops`, adds/updates `ssh_private_key_path`, and rewrites `ssh_command` so it uses the configured `AWS_SSH_PRIVATE_KEY_PATH` (e.g. `/path/to/your-key.pem`) instead of `<your-key.pem>`.
9. `_ensure_compose_host_ports_allowed()` extracts quoted compose port mappings from the generated HCL, rejects duplicate host ports, and inserts missing security group ingress blocks for host ports.
10. `_dedupe_ingress_blocks_in_security_groups()` removes duplicate AWS ingress permissions after resolving simple variable defaults such as `var.app_port`.
11. `_run_terraform_validations()` requires `key_name`, SSH ingress for 22, an egress block, `user_data` containing Docker commands, output blocks for `app_url` and `ssh_command`, a non-placeholder SSH key path, and a root block device of at least 20 GB when a literal `volume_size` is present.

> Security note: Docker Hub credentials are intentionally embedded into generated Terraform/user_data only when Gemini includes the Docker login placeholders and the backend replaces them. Do not commit generated `infra/main.tf` if it contains real credentials.

### 15.3 Apply / Destroy / Fix

Apply streams:

```bash
terraform init -input=false
terraform apply -auto-approve
```

Destroy streams:

```bash
terraform destroy -auto-approve
```

Fix flow:

1. Reads current `infra/main.tf`
2. Requires Terraform CLI to be available
3. Sends Terraform code plus error output to Gemini
4. Runs the same Docker credential injection, EC2 instance type enforcement, compose host-port ingress augmentation, and Terraform validations used during generation
5. Writes the fixed file back to `infra/main.tf`
6. Pushes a `"Terraform fixed by LLM"` log entry

Apply status updates:

1. Before streaming, MongoDB status becomes `deploying`.
2. If `terraform init` fails, status becomes `failed`.
3. If `terraform apply` succeeds, the backend reads `terraform output -json`, updates status to `deployed`, stores `aws_frontend_url` / `aws_backend_url` when present, and emits a final SSE `complete` event with deployment outputs.
4. If apply exits non-zero, status becomes `failed`.

Destroy status updates:

1. Before streaming, MongoDB status becomes `destroying`.
2. Successful destroy sets status to `not_deployed` and emits a final `complete` event.
3. Failed destroy sets status to `destroy_failed`.

### 15.4 Scale Controls

AWS scale controls operate on the EC2 instance from Terraform output:

| Endpoint | Service method | Behavior | Status update |
| -------- | -------------- | -------- | ------------- |
| `POST /api/aws/{id}/scale-zero` | `AWSDeploymentService.scale_to_zero()` -> `stop_instance()` | Runs `aws ec2 stop-instances --instance-ids <id>` | `scaled_to_zero` |
| `POST /api/aws/{id}/scale-up` | `AWSDeploymentService.scale_up()` -> `start_instance()` | Runs `aws ec2 start-instances --instance-ids <id>` | `deployed` |

### 15.5 Status

`GET /api/aws/{project_id}/status` returns stored MongoDB AWS fields:

```json
{
  "aws_deployment_status": "deployed",
  "aws_region": "us-east-1",
  "aws_frontend_url": "http://...",
  "aws_backend_url": "http://...",
  "aws_instance_id": null,
  "aws_last_deployed": "datetime",
  "docker_push_success": true
}
```

When status is `deployed`, it also reads live Terraform outputs from `{project_root}/infra` and adds:

```json
{
  "live_public_ip": "x.x.x.x",
  "live_frontend_url": "http://...",
  "live_backend_url": "http://...",
  "live_instance_id": "i-...",
  "live_vpc_id": "vpc-..."
}
```

> Requires AWS CLI credentials/profile and Terraform installed on the backend machine.

---

## 16. Monitoring & Kubernetes Deployment

**Files:** `routes/monitor.py`, `controllers/monitor_controller.py`, `services/monitor_service.py`, `utils/k8s_deployer.py`, `components/MonitoringDashboard.tsx`

Monitoring is exposed through `/api/monitor` and is backed by `kubectl` plus Terraform output checks.

### 16.1 Monitoring Status

`GET /api/monitor/{project_id}/status`:

1. Loads the project for the authenticated user
2. Derives deployment name from `project.metadata.name` or `project.project_name`
3. Reads Kubernetes pod health through `diagnose_pod_health()`
4. Reads AWS health by checking `terraform/<project_id>/terraform.tfstate` and `terraform output -json`
5. Adds recent k8s events and all pods
6. Returns `healthy` when either k8s or AWS health is healthy

### 16.2 Self-Healing

`POST /api/monitor/{project_id}/heal` triggers:

```bash
kubectl rollout restart deployment/{deployment_name}
```

### 16.3 Logs And Events

| Endpoint | Source |
| -------- | ------ |
| `GET /api/monitor/{id}/logs` | `kubectl logs <pod> --tail=<n> --timestamps=true` |
| `GET /api/monitor/{id}/logs/stream?token=<jwt>` | `kubectl logs -f <pod> --timestamps=true` |
| `GET /api/monitor/{id}/events` | `kubectl get events --sort-by=.lastTimestamp` |
| `GET /api/monitor/pods/all` | `kubectl get pods -n default -o json` |

EventSource log streaming uses `?token=<jwt>` because browser `EventSource` cannot send custom Authorization headers.

### 16.4 Kubernetes Deploy

Docker logs support `action=k8s_deploy`. The backend:

1. Ensures Kubernetes connectivity with `kubectl cluster-info`
2. Uses existing `k8s/deployment.yaml` and `k8s/service.yaml`, or generates missing manifests
3. Applies manifests with `kubectl apply -f`
4. Uses NodePort services and reports the selected node port
5. Streams deployment progress to the UI

---

## 17. Script Editing Guide (Critical Couplings)

This section is a maintenance map for editing detector/deploy scripts without introducing hidden regressions.

### 17.1 Port Schema Contract (`ports_v2`)

Canonical meaning:

- `runtime_port` = host/runtime side (compose left side)
- `container_port` = container listen/EXPOSE side (compose right side)
- `port` = compatibility alias of `runtime_port` (legacy consumers)

Do not repurpose these fields. If one script changes this meaning, update all downstream consumers together:

1. `app/utils/detection_services.py` (service detection payload)
2. `app/controllers/docker_ai_controller.py` (`_augment_services_runtime_hints`)
3. `app/LLM/docker_deploy_agent.py` (`_normalize_service_ports_v2`, `_format_metadata`, service-lines formatter)
4. Any external audit/training script that compares detector vs compose outputs

### 17.2 Runtime Inference Flow (Where Runtime Comes From)

Primary source:

1. `infer_services()` computes `service.runtime` via `infer_service_runtime_image_from_code()`.
2. Helper uses service-local code signals (`language/framework`, `package.json` engines/volta through `get_runtime_info`).
3. Static frontend mode forces `nginx:alpine`.

Controller backfill:

1. `_augment_services_runtime_hints()` fills `runtime` only when missing.
2. Existing runtime values are preserved (no overwrite).
3. Frontend runtime backfill derives mode from `frontend_mode` first, then `container_port_source/build_output/runtime_port/container_port`.

Editing rule: if you change runtime inference logic in detector, mirror compatible logic in controller backfill, or older metadata rows may diverge from fresh analysis rows.

### 17.3 Frontend Mode Coupling

Frontend behavior depends on `frontend_mode` and `container_port_source`.

- `static_nginx` -> `container_port=80`, runtime image `nginx:alpine`
- `ssr` -> Node runtime, `container_port` typically equals `runtime_port`
- `dev_server` -> Node runtime, `container_port=runtime_port`

If you add/change modes in `detection_services.py`, also update:

1. Prompt rules in `docker_deploy_agent.py` STEP 2/STEP 3 branches
2. Controller mode derivation in `_augment_services_runtime_hints()`
3. Documentation sample prompt/service-line examples

### 17.4 Compose Unmatched Service Path

When compose build-context services do not match inferred services by path/name, detector creates new service rows.

Non-frontend unmatched services now infer `svc_language/svc_framework` from the service context directory before runtime/port inference.

Editing rule: keep this service-local fallback logic intact; using project-wide framework/language here reintroduces frontend-to-backend bleed (for example React assigned to backend).

### 17.5 Deploy Warning Logic Scope

Missing `.env` handling is backend-like only (`backend`, `monolith`) and is recomputed at:

1. analysis time (`detector.py`)
2. deploy page context time (`docker_ai_controller.py`)

Current behavior does not block deployment only because `.env` is missing. It sets `backend_env_missing=true`, leaves `deploy_blocked=false`, and emits a warning so the build/readiness flows can generate `.env` or `.env.template`.

If you expand service types that need `.env` handling, update both paths together.

### 17.6 Minimum Regression Checks After Script Edits

Run at least:

```bash
python -m py_compile app/config/settings.py app/utils/command_extractor.py app/utils/detection_ports.py app/utils/detection_services.py app/utils/detector.py app/controllers/docker_ai_controller.py app/LLM/docker_deploy_agent.py
python -m py_compile app/controllers/aws_deploy_controller.py app/routes/aws_deploy.py app/services/aws_service.py app/controllers/deployment_readiness_controller.py app/routes/monitor.py app/controllers/monitor_controller.py app/services/monitor_service.py app/utils/k8s_deployer.py
python -m pytest tests/test_docker_pipeline.py -k RuntimeHintAugmentation -v
python -m pytest tests/test_detector_exhaustive.py -k runtime_selection -v
```

For data quality edits, also run your detector audit script on sampled compose repos and compare:

1. service path/type match
2. runtime_port/container_port parity
3. entry_point parity for backend/monolith services

### 17.7 Console Encoding Guard (Windows)

`detect_framework()` now calls `_ensure_utf8_stdout()` before running detection.

Purpose:

1. Avoid `UnicodeEncodeError` from debug `print(...)` calls in nested detection helpers on cp1252/non-UTF8 consoles.
2. Prevent false "detection failed" fallthrough caused by logging-only encoding errors.

Editing rule: if you move/rename detector entry points, preserve this guard at the earliest practical point in the flow.

### 17.8 Startup Log Safety (`settings.py`)

`app/config/settings.py` startup logs are ASCII-only.

Purpose:

1. Keep imports safe on non-UTF8 Windows consoles where emoji logging can fail during module import.
2. Avoid startup failures in controller/LLM paths that import `settings` before request handling.

---

## Appendix A: File Storage Layout

```
devops-autopilot/
├── backend-python/
│   ├── .env                          ← backend config (gitignored)
│   ├── app/
│   │   └── utils/
│   │       ├── detector.py            ← orchestrator + re-export hub (~620 LOC)
│   │       ├── detection_constants.py ← shared constants & helpers
│   │       ├── detection_language.py  ← language/framework detection
│   │       ├── detection_ports.py     ← port detection (env, pkg, Docker)
│   │       ├── detection_database.py  ← database detection & scoring
│   │       ├── detection_services.py  ← service inference
│   │       ├── command_extractor.py   ← Node.js/Python command extraction
│   │       └── ml_analyzer.py         ← ML-based classification
│   ├── uploads/
│   │   └── user_{username}/
│   │       └── {timestamp}-{file}    ← uploaded archives
│   └── extracted/
│       └── user_{username}/
│           └── {project_id}/         ← unzipped project files
│               ├── backend/
│               │   ├── package.json
│               │   ├── .env          ← user's app secrets (NOT gitignored)
│               │   └── server.js
│               ├── frontend/
│               │   └── package.json
│               ├── k8s/
│               │   ├── deployment.yaml ← generated by LLM/readiness flow
│               │   └── service.yaml    ← generated by LLM/readiness flow
│               ├── infra/
│               │   └── main.tf         ← generated AWS Terraform
│               ├── Dockerfile          ← generated by LLM when single-service/root
│               └── docker-compose.yml  ← generated by LLM
└── devops-autopilot-frontend/
    └── src/
        ├── pages/DeployPage.tsx
        ├── components/MonitoringDashboard.tsx
        └── types/api.ts
```

---

## Appendix B: Testing

**Test file locations:**

| File                                                   | Tests               | Coverage                     |
| ------------------------------------------------------ | ------------------- | ---------------------------- |
| `backend-python/tests/test_docker_pipeline.py`         | 53 tests (unittest) | Docker pipeline, Layer 1–4 (includes runtime backfill/no-overwrite checks) |
| `backend-python/tests/test_detector_exhaustive.py`     | 134 tests (pytest)  | Full detector.py coverage    |
| `backend-python/tests/test_detection_comprehensive.py` | 77 tests (pytest)   | Real-world MERN scenarios    |
| `backend-python/tests/test_database_detection.py`      | Database detection  | DB scoring & port inference  |
| `backend-python/tests/test_port_detection.py`          | Port detection      | Multi-source port resolution |
| `backend-python/tests/test_command_extractor.py`       | Command extraction  | Node.js/Python entry points  |

**Total: 426 tests** (all passing after modular refactoring; earlier counts such as the 362-test figure, and the thesis chapter's separate 74-unit-test figure, reflect earlier project stages)

**Run tests:**

```bash
# Full suite (recommended)
python -m pytest tests/ -v

# Individual test files
python -m pytest tests/test_detector_exhaustive.py -v
python -m pytest tests/test_detection_comprehensive.py -v
python -m pytest tests/test_database_detection.py -v
```

> **Note:** All tests import from `app.utils.detector` — the re-export pattern ensures no test imports needed changing after the modular refactoring.

---

_Document updated: 2026-04-18 | DevOps AutoPilot v2.0 - Gemini/Ollama provider routing, Docker/Kubernetes readiness, monitoring dashboard APIs, AWS EC2 Terraform flow, scale-zero/scale-up controls, and non-blocking `.env` auto-generation_
