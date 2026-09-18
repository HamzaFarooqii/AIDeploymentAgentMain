import {
  UserRegisterRequest,
  UserLoginRequest,
  User,
  AuthResponse,
  TokenResponse,
  ProjectUploadResponse,
  ProjectListResponse,
  ExtractionResponse,
  AnalysisResponse,
  DockerChatResponse,
  DockerContextResponse,
  MonitorStatus,
  HealResponse,
} from "../types/api";

const rawApiBase =
  import.meta.env.VITE_API_BASE_URL || "http://localhost:8000/api";
const API_BASE_URL = rawApiBase.endsWith("/api")
  ? rawApiBase.replace(/\/+$/, "")
  : `${rawApiBase.replace(/\/+$/, "")}/api`;

class ApiClient {
  private token: string | null = null;

  constructor() {
    this.token = localStorage.getItem("auth_token");
  }

  setToken(token: string) {
    this.token = token;
    localStorage.setItem("auth_token", token);
  }

  getToken(): string | null {
    return this.token || localStorage.getItem("auth_token");
  }

  clearToken() {
    this.token = null;
    localStorage.removeItem("auth_token");
  }

  private getHeaders(): Record<string, string> {
    const headers: Record<string, string> = {
      "Content-Type": "application/json",
    };

    const token = this.getToken();
    if (token) {
      headers["Authorization"] = `Bearer ${token}`;
    }

    return headers;
  }

  private async handleResponse(response: Response) {
    const raw = await response.text();
    const data = raw ? (() => {
      try {
        return JSON.parse(raw);
      } catch {
        return null;
      }
    })() : null;

    if (!response.ok) {
      const message =
        data?.message ||
        data?.detail ||
        data?.error ||
        `API request failed (${response.status})`;
      throw new Error(message);
    }

    return data ?? {};
  }

  // ============ AUTH ENDPOINTS ============
  async register(data: UserRegisterRequest): Promise<AuthResponse> {
    const response = await fetch(`${API_BASE_URL}/auth/register`, {
      method: "POST",
      headers: this.getHeaders(),
      body: JSON.stringify(data),
    });
    return this.handleResponse(response);
  }

  async login(credentials: UserLoginRequest): Promise<TokenResponse> {
    const response = await fetch(`${API_BASE_URL}/auth/login`, {
      method: "POST",
      headers: this.getHeaders(),
      body: JSON.stringify(credentials),
    });
    const data = await this.handleResponse(response);
    this.setToken(data.access_token);
    return data;
  }

  async getCurrentUser(): Promise<{ success: boolean; user: User }> {
    const response = await fetch(`${API_BASE_URL}/auth/me`, {
      method: "GET",
      headers: this.getHeaders(),
    });
    return this.handleResponse(response);
  }

  async verifyToken(): Promise<boolean> {
    try {
      const response = await fetch(`${API_BASE_URL}/auth/verify`, {
        method: "GET",
        headers: this.getHeaders(),
      });
      return response.ok;
    } catch {
      return false;
    }
  }

  // ============ UPLOAD ENDPOINTS ============
  async uploadProject(
    file: File,
    projectName?: string
  ): Promise<ProjectUploadResponse> {
    const formData = new FormData();
    formData.append("file", file);
    if (projectName) {
      formData.append("project_name", projectName);
    }

    const token = this.getToken();
    const headers: Record<string, string> = {};
    if (token) {
      headers["Authorization"] = `Bearer ${token}`;
    }

    const response = await fetch(`${API_BASE_URL}/upload/`, {
      method: "POST",
      headers,
      body: formData,
    });
    return this.handleResponse(response);
  }

  async getProjects(): Promise<ProjectListResponse> {
    const response = await fetch(`${API_BASE_URL}/upload/projects`, {
      method: "GET",
      headers: this.getHeaders(),
    });
    return this.handleResponse(response);
  }

  async getProject(projectId: string) {
    const response = await fetch(
      `${API_BASE_URL}/upload/projects/${projectId}`,
      {
        method: "GET",
        headers: this.getHeaders(),
      }
    );
    return this.handleResponse(response);
  }

  async deleteProject(projectId: string) {
    const response = await fetch(
      `${API_BASE_URL}/upload/projects/${projectId}`,
      {
        method: "DELETE",
        headers: this.getHeaders(),
      }
    );
    return this.handleResponse(response);
  }

  // ============ EXTRACT ENDPOINTS ============
  async extractProject(projectId: string): Promise<ExtractionResponse> {
    const response = await fetch(`${API_BASE_URL}/extract/${projectId}`, {
      method: "POST",
      headers: this.getHeaders(),
    });
    return this.handleResponse(response);
  }

  async getExtractionStatus(projectId: string) {
    const response = await fetch(
      `${API_BASE_URL}/extract/${projectId}/status`,
      {
        method: "GET",
        headers: this.getHeaders(),
      }
    );
    return this.handleResponse(response);
  }

  async getExtractedFiles(projectId: string) {
    const response = await fetch(`${API_BASE_URL}/extract/${projectId}/files`, {
      method: "GET",
      headers: this.getHeaders(),
    });
    return this.handleResponse(response);
  }

  async cleanupExtraction(projectId: string) {
    const response = await fetch(
      `${API_BASE_URL}/extract/${projectId}/cleanup`,
      {
        method: "DELETE",
        headers: this.getHeaders(),
      }
    );
    return this.handleResponse(response);
  }

  // ============ ANALYSIS ENDPOINTS ============
  async analyzeProject(
    projectId: string,
    useML: boolean = true,
    force: boolean = false
  ): Promise<AnalysisResponse> {
    const params = new URLSearchParams();
    params.append("use_ml", useML.toString());
    params.append("force", force.toString());

    const response = await fetch(
      `${API_BASE_URL}/analyze/${projectId}?${params}`,
      {
        method: "POST",
        headers: this.getHeaders(),
      }
    );
    return this.handleResponse(response);
  }

  async getAnalysisResults(projectId: string) {
    const response = await fetch(`${API_BASE_URL}/analyze/${projectId}`, {
      method: "GET",
      headers: this.getHeaders(),
    });
    return this.handleResponse(response);
  }

  async getProjectMetadata(projectId: string) {
    const response = await fetch(
      `${API_BASE_URL}/analyze/${projectId}/metadata`,
      {
        method: "GET",
        headers: this.getHeaders(),
      }
    );
    return this.handleResponse(response);
  }

  async getAllMetadata(framework?: string, language?: string) {
    const params = new URLSearchParams();
    if (framework) params.append("framework", framework);
    if (language) params.append("language", language);

    const response = await fetch(
      `${API_BASE_URL}/analyze/metadata/all?${params}`,
      {
        method: "GET",
        headers: this.getHeaders(),
      }
    );
    return this.handleResponse(response);
  }

  async getMetadataStatistics() {
    const response = await fetch(
      `${API_BASE_URL}/analyze/metadata/statistics`,
      {
        method: "GET",
        headers: this.getHeaders(),
      }
    );
    return this.handleResponse(response);
  }

  async exportMetadata(projectId: string, format: "json" | "yaml" = "json") {
    const response = await fetch(
      `${API_BASE_URL}/analyze/${projectId}/export?format=${format}`,
      {
        method: "GET",
        headers: this.getHeaders(),
      }
    );
    return this.handleResponse(response);
  }

  // ============ DOCKER DEPLOY (Llama 3.1) ============
  async getDockerContext(projectId: string): Promise<DockerContextResponse> {
    const response = await fetch(
      `${API_BASE_URL}/docker/${projectId}/context`,
      {
        method: "GET",
        headers: this.getHeaders(),
      }
    );
    return this.handleResponse(response);
  }

  async sendDockerChat(
    projectId: string,
    payload: { message: string; logs?: string[]; instructions?: string }
  ): Promise<DockerChatResponse> {
    const response = await fetch(`${API_BASE_URL}/docker/${projectId}/chat`, {
      method: "POST",
      headers: this.getHeaders(),
      body: JSON.stringify(payload),
    });
    return this.handleResponse(response);
  }

  async readProjectFile(
    projectId: string,
    path: string
  ): Promise<{ success: boolean; path: string; content: string }> {
    const url = new URL(`${API_BASE_URL}/docker/${projectId}/file`);
    url.searchParams.set("path", path);
    const response = await fetch(url.toString(), {
      method: "GET",
      headers: this.getHeaders(),
    });
    return this.handleResponse(response);
  }

  async writeProjectFile(
    projectId: string,
    payload: { path: string; content: string }
  ): Promise<{ success: boolean; path: string }> {
    const response = await fetch(`${API_BASE_URL}/docker/${projectId}/file`, {
      method: "POST",
      headers: this.getHeaders(),
      body: JSON.stringify(payload),
    });
    return this.handleResponse(response);
  }

  // ============ FILE / FOLDER CRUD (Deploy explorer) ============
  async createProjectFile(
    projectId: string,
    path: string,
    content: string
  ): Promise<{ success: boolean; path: string }> {
    return this.writeProjectFile(projectId, { path, content });
  }

  async createProjectFolder(
    projectId: string,
    path: string
  ): Promise<{ success: boolean; path: string }> {
    const response = await fetch(`${API_BASE_URL}/docker/${projectId}/folder`, {
      method: "POST",
      headers: this.getHeaders(),
      body: JSON.stringify({ path }),
    });
    return this.handleResponse(response);
  }

  async deleteProjectPath(
    projectId: string,
    path: string
  ): Promise<{ success: boolean; path: string }> {
    const url = new URL(`${API_BASE_URL}/docker/${projectId}/path`);
    url.searchParams.set("path", path);
    const response = await fetch(url.toString(), {
      method: "DELETE",
      headers: this.getHeaders(),
    });
    return this.handleResponse(response);
  }

  // ============ AWS DEPLOYMENT ENDPOINTS ============
  
  async checkAWSPrerequisites(projectId: string): Promise<{
    can_deploy: boolean;
    issues: string[];
    project_name: string;
    aws_region: string;
    docker_push_success: boolean;
    docker_hub_username?: string;
    terraform_exists?: boolean;
    aws_deployment_status?: string;
  }> {
    const response = await fetch(
      `${API_BASE_URL}/aws/${projectId}/prerequisites`,
      {
        method: "GET",
        headers: this.getHeaders(),
      }
    );
    return this.handleResponse(response);
  }

  async generateTerraform(
    projectId: string,
    config: {
      aws_region: string;
      docker_repo_prefix: string;
      db_engine?: string;
      mongo_db_url?: string;
      rds_db_url?: string;
      desired_count?: number;
      extra_env?: Record<string, string>;
    }
  ): Promise<{ status: string; terraform_path: string; message: string }> {
    const response = await fetch(`${API_BASE_URL}/aws/${projectId}/generate`, {
      method: "POST",
      headers: this.getHeaders(),
      body: JSON.stringify(config),
    });
    return this.handleResponse(response);
  }

  async getAWSStatus(projectId: string): Promise<{
    aws_deployment_status: string;
    aws_region?: string;
    aws_frontend_url?: string;
    aws_ecs_cluster_id?: string;
    aws_last_deployed?: string;
    docker_push_success: boolean;
    live_alb_url?: string;
    live_cluster_name?: string;
    live_vpc_id?: string;
  }> {
    const response = await fetch(`${API_BASE_URL}/aws/${projectId}/status`, {
      method: "GET",
      headers: this.getHeaders(),
    });
    return this.handleResponse(response);
  }

  async fixTerraform(
    projectId: string,
    errorOutput: string
  ): Promise<{ status: string; message: string }> {
    const response = await fetch(`${API_BASE_URL}/aws/${projectId}/fix`, {
      method: "POST",
      headers: this.getHeaders(),
      body: JSON.stringify({ error_output: errorOutput }),
    });
    return this.handleResponse(response);
  }

  async getMonitorStatus(projectId: string): Promise<MonitorStatus> {
    const response = await fetch(`${API_BASE_URL}/monitor/${projectId}/status`, {
      method: "GET",
      headers: this.getHeaders(),
    });
    return this.handleResponse(response);
  }

  async healProject(projectId: string): Promise<HealResponse> {
    const response = await fetch(`${API_BASE_URL}/monitor/${projectId}/heal`, {
      method: "POST",
      headers: this.getHeaders(),
    });
    return this.handleResponse(response);
  }
}

export const apiClient = new ApiClient();

export function streamMonitorLogs(
  projectId: string,
  onLog: (line: string) => void,
  onError: (error: Error) => void
): EventSource {
  const rawApiBase =
    import.meta.env.VITE_API_BASE_URL || "http://localhost:8000/api";
  const apiBase = rawApiBase.endsWith("/api")
    ? rawApiBase.replace(/\/+$/, "")
    : `${rawApiBase.replace(/\/+$/, "")}/api`;

  const token = apiClient.getToken();
  const url = new URL(`${apiBase}/monitor/${projectId}/logs/stream`);
  if (token) {
    url.searchParams.set("token", token);
  }

  const source = new EventSource(url.toString());

  source.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data);
      if (data.type === "error") {
        onError(new Error(data.message));
        source.close();
      } else {
        onLog(data.message);
      }
    } catch {
      onLog(event.data); // fallback to raw string
    }
  };

  source.onerror = () => {
    onError(new Error("Connection to log stream closed or failed."));
    source.close();
  };

  return source;
}


/**
 * Stream Docker chat response using Server-Sent Events (SSE).
 * Tokens are received incrementally as the LLM generates them.
 * 
 * @param projectId - Project ID
 * @param params - Chat parameters (message, logs, instructions)
 * @param onToken - Callback for each token received
 * @param onDone - Callback when streaming completes
 * @param onError - Callback for errors
 * @returns EventSource instance (can be closed to cancel)
 */
export function streamDockerChat(
  projectId: string,
  params: { message: string; logs?: string[]; instructions?: string },
  onToken: (token: string) => void,
  onDone: () => void,
  onError: (error: Error) => void
): EventSource {
  const rawApiBase =
    import.meta.env.VITE_API_BASE_URL || "http://localhost:8000/api";
  const apiBase = rawApiBase.endsWith("/api")
    ? rawApiBase.replace(/\/+$/, "")
    : `${rawApiBase.replace(/\/+$/, "")}/api`;

  const token = apiClient.getToken();
  const url = new URL(`${apiBase}/docker/${projectId}/chat/stream`);
  
  // Set query parameters
  url.searchParams.set("message", params.message);
  if (params.logs && params.logs.length > 0) {
    url.searchParams.set("logs", params.logs.join("\n"));
  }
  if (params.instructions) {
    url.searchParams.set("instructions", params.instructions);
  }
  if (token) {
    url.searchParams.set("token", token);
  }

  const source = new EventSource(url.toString());

  source.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data);
      
      if (data.error) {
        onError(new Error(data.token || data.error));
        source.close();
        return;
      }
      
      if (data.token) {
        onToken(data.token);
      }
      
      if (data.done) {
        source.close();
        onDone();
      }
    } catch (err) {
      console.error("Error parsing SSE event:", err);
    }
  };

  source.onerror = () => {
    onError(new Error("LLM stream connection error"));
    source.close();
    onDone();
  };

  return source;
}

/**
 * Stream AWS Terraform apply/destroy operations using Server-Sent Events (SSE).
 * 
 * @param projectId - Project ID
 * @param operation - 'apply' | 'destroy' | 'scale-zero'
 * @param onEvent - Callback for each event received
 * @param onComplete - Callback when streaming completes
 * @param onError - Callback for errors
 * @returns EventSource instance (can be closed to cancel)
 */
export function streamAWSTerraform(
  projectId: string,
  operation: 'apply' | 'destroy' | 'scale-zero' | 'scale-up',
  onEvent: (event: { type: string; message: string; stage?: string }) => void,
  onComplete: (outputs?: Record<string, unknown>) => void,
  onError: (error: Error) => void
): EventSource {
  const rawApiBase =
    import.meta.env.VITE_API_BASE_URL || "http://localhost:8000/api";
  const apiBase = rawApiBase.endsWith("/api")
    ? rawApiBase.replace(/\/+$/, "")
    : `${rawApiBase.replace(/\/+$/, "")}/api`;

  const token = apiClient.getToken();
  const url = new URL(`${apiBase}/aws/${projectId}/${operation}`);
  
  if (token) {
    url.searchParams.set("token", token);
  }

  // Use fetch with ReadableStream for POST requests
  const headers: Record<string, string> = {
    "Accept": "text/event-stream",
  };
  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }

  fetch(url.toString(), {
    method: "POST",
    headers,
  }).then(async (response) => {
    if (!response.ok) {
      onError(new Error(`HTTP ${response.status}: ${response.statusText}`));
      return;
    }

    const reader = response.body?.getReader();
    if (!reader) {
      onError(new Error("No response body"));
      return;
    }

    const decoder = new TextDecoder();
    let buffer = "";
    let completed = false;
    let failed = false;

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";

      for (const line of lines) {
        if (line.startsWith("data: ")) {
          try {
            const data = JSON.parse(line.slice(6));
            
            if (data.type === "complete") {
              completed = true;
              onComplete(data.outputs);
            } else if (data.type === "error") {
              failed = true;
              onError(new Error(data.message));
            } else {
              onEvent(data);
            }
          } catch (err) {
            console.error("Error parsing SSE event:", err);
          }
        }
      }
    }

    if (!completed && !failed) {
      onComplete();
    }
  }).catch(onError);

  // Return a dummy EventSource for API compatibility
  // (actual streaming is done via fetch)
  return { close: () => {} } as EventSource;
}
