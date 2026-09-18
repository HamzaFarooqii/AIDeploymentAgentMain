// ============ AUTH TYPES ============
export interface UserRegisterRequest {
  username: string;
  email: string;
  password: string;
  full_name?: string;
}

export interface UserLoginRequest {
  username: string;
  password: string;
}

export interface User {
  user_id: string;
  username: string;
  email: string;
  full_name?: string;
  is_active: boolean;
  is_admin?: boolean;
  workspace_path?: string;
  created_at: string;
}

export interface AuthResponse {
  success: boolean;
  message: string;
  user?: User;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
  user: User;
}

// ============ PROJECT TYPES ============
export interface ProjectMetadata {
  schema_version?: string;
  framework: string;
  language: string;
  runtime?: string;
  dependencies: string[];
  port?: number; // still the "main" port (backend_port)
  build_command?: string;
  start_command?: string;
  env_variables: string[];
  dockerfile: boolean;
  docker_compose: boolean;
  detected_files: string[];

  // ML / detection confidence
  ml_confidence?: {
    language: number;
    framework: number;
    method?: string; // mirrors detection_confidence.method when present
  };
  detection_confidence?: {
    language: number;
    framework: number;
    method?: string;
  };
  detection_method?: string; // kept for backwards compatibility if used anywhere

  // NEW FIELDS (from detector.py)
  backend_port?: number;
  frontend_port?: number;
  backend_runtime_port?: number;
  frontend_runtime_port?: number;
  backend_runtime_port_source?: string;
  frontend_runtime_port_source?: string;
  backend_container_port?: number;
  frontend_container_port?: number;
  backend_container_port_source?: string;
  frontend_container_port_source?: string;
  database?: string;
  database_port?: number | null;
  databases?: string[];
  database_detection?: {
    [dbName: string]: {
      score: number;
      evidence: string[];
    };
  };

  // NEW DOCKER PORT FIELDS (HOST)
  docker_backend_ports?: number[] | null;
  docker_frontend_ports?: number[] | null;
  docker_database_ports?: number[] | null;
  docker_other_ports?: { [serviceName: string]: number[] } | null;
  docker_expose_ports?: number[] | null;

  // NEW DOCKER CONTAINER PORT FIELDS
  docker_backend_container_ports?: number[] | null;
  docker_frontend_container_ports?: number[] | null;
  docker_database_container_ports?: number[] | null;
  docker_other_container_ports?: { [serviceName: string]: number[] } | null;

  // DEPLOY BLOCKED FLAGS (backend .env missing)
  deploy_blocked?: boolean;
  deploy_blocked_reason?: string | null;
  backend_env_missing?: boolean;
  deploy_warning?: string | null;

  // ARCHITECTURE (monolith vs multi-service)
  architecture?: "multi-service" | "monolith";
}

export interface LogEntry {
  message: string;
  timestamp: string;
}

export interface Project {
  _id: string;
  project_name: string;
  file_name: string;
  file_size: number;
  upload_date: string;
  status:
    | "uploaded"
    | "extracting"
    | "extracted"
    | "analyzing"
    | "analyzed"
    | "completed"
    | "failed";
  extracted_path?: string;
  extraction_date?: string;
  files_count: number;
  folders_count: number;
  metadata: ProjectMetadata;
  analysis_date?: string;
  analysis_logs?: string[];
  logs: LogEntry[];
}

export interface ProjectUploadResponse {
  success: boolean;
  message: string;
  data: {
    schema_version?: string;
    project_id: string;
    project_name: string;
    file_name: string;
    file_size: string;
    upload_date: string;
  };
}

export interface ProjectListResponse {
  success: boolean;
  count: number;
  projects: Project[];
}

export interface ExtractionResponse {
  success: boolean;
  message: string;
  data: {
    project_id: string;
    project_name: string;
    extracted_path: string;
    files_count: number;
    folders_count: number;
    extraction_date: string;
  };
}

export interface AnalysisResponse {
  success: boolean;
  message: string;
  data: {
    project_id: string;
    project_name: string;
    framework: string;
    language: string;
    runtime: string | null;
    dependencies: string[];
    port?: number;
    build_command?: string;
    start_command?: string;
    ml_confidence?: {
      language: number;
      framework: number;
      method?: string;
    };
    analysis_date: string;
    ml_enabled?: boolean;

    // NEW (optional) – mirrors metadata
    backend_port?: number;
    frontend_port?: number;
    backend_runtime_port?: number;
    frontend_runtime_port?: number;
    backend_runtime_port_source?: string;
    frontend_runtime_port_source?: string;
    backend_container_port?: number;
    frontend_container_port?: number;
    backend_container_port_source?: string;
    frontend_container_port_source?: string;

    database?: string;
    databases?: string[];
    database_port?: number | null;
    database_detection?: {
      [dbName: string]: {
        score: number;
        evidence: string[];
      };
    };

    env_variables?: string[];
    dockerfile?: boolean;
    docker_compose?: boolean;
    detected_files?: string[];

    // detection confidence (if you ever need it on the response)
    detection_confidence?: {
      language: number;
      framework: number;
      method?: string;
    };

    // NEW DOCKER PORT FIELDS (match detector.py)
    docker_backend_ports?: number[] | null;
    docker_frontend_ports?: number[] | null;
    docker_database_ports?: number[] | null;
    docker_other_ports?: { [serviceName: string]: number[] } | null;
    docker_expose_ports?: number[] | null;

    // NEW DOCKER CONTAINER PORT FIELDS
    docker_backend_container_ports?: number[] | null;
    docker_frontend_container_ports?: number[] | null;
    docker_database_container_ports?: number[] | null;
    docker_other_container_ports?: { [serviceName: string]: number[] } | null;
  };
}

// ============ DOCKER DEPLOY TYPES ============
export interface DockerfileInfo {
  path: string;
  content: string;
}

export interface FileNode {
  name: string;
  path: string;
  is_dir: boolean;
  children?: FileNode[];
}

export interface DockerContextResponse {
  success: boolean;
  project: { id: string; project_name: string; status: string };
  metadata: ProjectMetadata;
  dockerfiles: DockerfileInfo[];
  compose_files: DockerfileInfo[];
  file_tree: {
    text: string;
    tree: FileNode[];
  };
  docker_compose_present?: boolean;
}

export interface DockerChatResponse {
  success: boolean;
  reply: string;
  model: string;
  dockerfiles_found: boolean;
  log_stream_base_url?: string;
}

export interface ApiError {
  success: false;
  message: string;
  error?: string;
}

// ============ MONITORING TYPES ============
export interface PodInfo {
  name: string;
  namespace: string;
  status: string;
  ready: boolean;
  restart_count: number;
  pod_ip?: string;
  labels?: Record<string, string>;
  created_at?: string;
}

export interface K8sEvent {
  type: string;
  reason: string;
  message: string;
  timestamp: string;
  count: number;
}

export interface MonitorStatus {
  success: boolean;
  message?: string;
  project_name: string;
  deployment_name: string;
  overall_healthy: boolean;
  kubernetes: {
    healthy: boolean;
    state: string;
    reason: string;
    restart_count: number;
    pod_name?: string;
  };
  aws: { status: string; healthy: boolean; details: string };
  pods: PodInfo[];
  recent_events: K8sEvent[];
  deployment_status: string;
  deployment?: Record<string, unknown>;
}

export interface HealResponse {
  success: boolean;
  message?: string;
}

