# DevOps AutoPilot Run Commands

Run commands from PowerShell unless noted otherwise. All commands below assume your terminal's current directory is the root of your local clone of this repository (the folder that directly contains `backend-python/` and `devops-autopilot-frontend/`) — `cd` there once, then run the commands as shown.

## 1. Start MongoDB

Create the MongoDB container the first time:

```powershell
docker run -d --name devops-autopilot-mongo -p 27017:27017 -v devops-autopilot-mongo-data:/data/db mongo:7
```

Start it later if it already exists:

```powershell
docker start devops-autopilot-mongo
```

Check it:

```powershell
docker ps --filter "name=devops-autopilot-mongo"
```

Required backend `.env` values:

```env
MONGODB_URL=mongodb://localhost:27017
DATABASE_NAME=devops_autopilot
```

## 2. Run Backend

```powershell
cd backend-python
.\venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Run with auto-reload only during development:

```powershell
cd backend-python
.\venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
```

Backend URLs:

```text
API: http://localhost:8000
Swagger: http://localhost:8000/docs
Health: http://localhost:8000/health
```

## 3. Run Frontend

Install dependencies once:

```powershell
cd devops-autopilot-frontend
npm.cmd install
```

Start the Vite dev server:

```powershell
cd devops-autopilot-frontend
npm.cmd run dev
```

Frontend URL:

```text
http://localhost:5173
```

## 4. Use Gemini For Docker Generation

Set these in `backend-python/.env`, then restart the backend:

```env
DOCKER_LLM_PROVIDER=gemini
GEMINI_API_KEY=your_google_ai_studio_key
GEMINI_MODEL_NAME=gemini-2.5-flash
```

Confirm provider:

```powershell
# from the repository root
backend-python\venv\Scripts\python.exe -c "import sys; sys.path.insert(0, 'backend-python'); from app.config.settings import settings; print(settings.DOCKER_LLM_PROVIDER)"
```

Switch back to Ollama:

```env
DOCKER_LLM_PROVIDER=ollama
```

## 5. Optional Ollama Commands

Use these only if Docker generation is configured for Ollama:

```powershell
ollama serve
ollama pull llama3.1:7b
```

## 6. Useful Verification

Backend import check:

```powershell
# from the repository root
backend-python\venv\Scripts\python.exe -c "import sys; sys.path.insert(0, 'backend-python'); from app.main import app; print('backend import ok')"
```

Focused backend tests:

```powershell
# from the repository root
backend-python\venv\Scripts\python.exe -m pytest backend-python/tests/test_docker_agent.py -q
backend-python\venv\Scripts\python.exe -m pytest backend-python/tests/test_docker_pipeline.py -k RuntimeHintAugmentation -q
```

Frontend build check:

```powershell
cd devops-autopilot-frontend
npm.cmd run build
```

Note: use `npm.cmd` on Windows PowerShell to avoid `npm.ps1` execution-policy errors.

