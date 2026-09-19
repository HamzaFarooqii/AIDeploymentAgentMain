from pydantic_settings import BaseSettings
from typing import List, Optional
import os


class Settings(BaseSettings):
    MONGODB_URL: str = "mongodb://localhost:27017"
    DATABASE_NAME: str = "devops_autopilot"
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    UPLOAD_DIR: str = "./uploads"
    EXTRACTED_DIR: str = "./extracted"
    MAX_FILE_SIZE: int = 104857600
    ALLOWED_ORIGINS: List[str] = [
        "http://localhost:3000",
        "http://localhost:4321",
        "http://localhost:5173",
        "http://localhost:8080",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:4321",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:8080"
    ]
    ENVIRONMENT: str = "development"

    # JWT signing secret. Must be set via SECRET_KEY in the environment/.env
    # for any real deployment - the placeholder default below is only safe
    # for local development and is never a value you should rely on in
    # production (anyone with it can forge valid auth tokens).
    SECRET_KEY: str = "your-secret-key-here-change-in-production"

    # Docker Hub Credentials - loaded from environment or .env
    # Set `DOCKER_HUB_USERNAME` and `DOCKER_HUB_PASSWORD` in your environment
    DOCKER_HUB_USERNAME: Optional[str] = None
    DOCKER_HUB_PASSWORD: Optional[str] = None
    
    # Kubernetes Config
    K8S_NAMESPACE: str = "default"
    K8S_CLUSTER: str = "docker-desktop"
    
    # Deployment Config
    APP_REGISTRY_PREFIX: str = "devops-autopilot"
    K8S_NODE_PORT_START: int = 30001
    K8S_NODE_PORT_END: int = 32767
    
    # LLM Configuration
    OLLAMA_URL: str = "http://localhost:11434/api/generate"
    LLM_MODEL_NAME: str = "llama3.1:7b"
    LLM_TEMPERATURE: float = 0.1
    LLM_TOP_P: float = 0.9
    LLM_TIMEOUT: int = 600


    DOCKER_LLM_PROVIDER: str = "ollama"  # "ollama", "gemini", or "groq"
    GEMINI_API_KEY: Optional[str] = None
    GEMINI_API_BASE: str = "https://generativelanguage.googleapis.com/v1beta"
    GEMINI_MODEL_NAME: str = "gemini-3.6-flash"
    GEMINI_MAX_OUTPUT_TOKENS: int = 8192
    GEMINI_FALLBACK_MODEL_NAME: Optional[str] = None

    # Groq (OpenAI-compatible chat completions) - free tier, hardware-accelerated
    # inference. Model name is configurable here rather than hardcoded so it can
    # be swapped later without a code change.
    GROQ_API_KEY: Optional[str] = None
    GROQ_API_BASE: str = "https://api.groq.com/openai/v1"
    GROQ_MODEL_NAME: str = "openai/gpt-oss-120b"

    # Supabase - not wired into the app yet (Mongo remains the live database),
    # declared here so .env can hold these without pydantic-settings rejecting
    # them as unrecognized fields (it validates every .env var against a
    # declared field by default).
    SUPABASE_URL: Optional[str] = None
    SUPABASE_PUBLISHABLE_KEY: Optional[str] = None
    SUPABASE_DB_URL: Optional[str] = None
    SUPABASE_DB_HOST: Optional[str] = None
    SUPABASE_DB_PORT: int = 5432
    SUPABASE_DB_NAME: str = "postgres"
    SUPABASE_DB_USER: str = "postgres"
    SUPABASE_DB_PASSWORD: Optional[str] = None

    # AWS Deployment Configuration
    AWS_PROFILE: Optional[str] = None  # AWS CLI profile name (e.g., "my-terraform")
    AWS_DEFAULT_REGION: str = "us-east-1"
    TERRAFORM_PATH: str = "terraform"  # Path to terraform CLI binary
    AWS_EC2_INSTANCE_TYPE: str = "t3.micro"
    AWS_EC2_KEY_NAME: str = "aws-deployment-devops"
    # Local path to the EC2 SSH private key (.pem) file. Must be set via
    # AWS_SSH_PRIVATE_KEY_PATH in the environment/.env — there is no safe
    # machine-specific default. Left empty ("") so unset is a genuinely
    # falsy value that callers can detect (`if not settings.AWS_SSH_PRIVATE_KEY_PATH`)
    # rather than silently deploying with a path that doesn't exist.
    AWS_SSH_PRIVATE_KEY_PATH: str = ""
    
    class Config:
        env_file = ".env"
        case_sensitive = True


settings = Settings()

os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
os.makedirs(settings.EXTRACTED_DIR, exist_ok=True)

print(f"Settings loaded: {settings.ENVIRONMENT} mode")
print(f"Upload directory: {settings.UPLOAD_DIR}")
print(f"Extracted directory: {settings.EXTRACTED_DIR}")

# Inform about Docker Hub credential presence without printing sensitive values
if settings.DOCKER_HUB_USERNAME and settings.DOCKER_HUB_PASSWORD:
    print("Docker Hub credentials loaded from environment")
else:
    print("Warning: Docker Hub credentials not set in environment (.env or OS vars)")

if settings.SECRET_KEY == "your-secret-key-here-change-in-production":
    print("Warning: SECRET_KEY is using the default placeholder - set SECRET_KEY in .env before deploying anywhere real")
