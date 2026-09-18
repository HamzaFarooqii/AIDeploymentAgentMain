import React, { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  Container,
  Cloud,
  ArrowLeft,
  AlertCircle,
  ChevronDown,
  ChevronRight,
  RefreshCw,
  FilePlus2,
  FolderPlus,
  Rocket,
  Code2,
  Terminal as TerminalIcon,
  Settings,
} from "lucide-react";
import { Navbar } from "../components/Navbar";
import { Card } from "../components/Card";
import { Button } from "../components/Button";
import { Badge } from "../components/Badge";
import { AIChatSidebar } from "../components/AIChatSidebar";
import { apiClient } from "../api/client";
import { LoadingSpinner } from "../components/LoadingSpinner";
import {
  DockerContextResponse,
  FileNode,
} from "../types/api";
import ThreeBackground from "../components/ThreeBackground";
import { MonitoringDashboard } from "../components/MonitoringDashboard";
import AWSDeployPanel from "../components/AWSDeployPanel";

type DeployMode = "docker" | "aws" | "monitor";

interface ChatMessage {
  role: "user" | "ai";
  content: string;
}

interface LogLine {
  line: string;
  stage: "build" | "run" | "push" | string;
  exit_code?: number;
  complete?: boolean;
}

export const DeployPage: React.FC = () => {
  const { projectId } = useParams<{ projectId: string }>();
  const navigate = useNavigate();
  const [context, setContext] = useState<DockerContextResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [logInput, setLogInput] = useState("");
  const [instructions, setInstructions] = useState("");
  const [sending, setSending] = useState(false);
  const [openFiles, setOpenFiles] = useState<string[]>([]);
  const [activeFile, setActiveFile] = useState<string | null>(null);
  const [fileContents, setFileContents] = useState<Record<string, string>>({});
  const [dirtyFlags, setDirtyFlags] = useState<Record<string, boolean>>({});
  const [saving, setSaving] = useState<boolean>(false);
  const [logs, setLogs] = useState<LogLine[]>([]);
  const [eventSource, setEventSource] = useState<EventSource | null>(null);
  const [refreshingTree, setRefreshingTree] = useState<boolean>(false);
  const [expandedDirs, setExpandedDirs] = useState<Record<string, boolean>>({});

  // Deploy mode toggle: docker or aws
  const [deployMode, setDeployMode] = useState<DeployMode>("docker");

  const rawApiBase =
    (import.meta as any).env?.VITE_API_BASE_URL || "http://localhost:8000/api";
  const apiBase = rawApiBase.endsWith("/api")
    ? rawApiBase.replace(/\/+$/, "")
    : `${rawApiBase.replace(/\/+$/, "")}/api`;
  const rootLabel = context?.project.project_name || "project";

  useEffect(() => {
    if (!projectId) return;
    const fetchContext = async () => {
      try {
        setLoading(true);
        const data = await apiClient.getDockerContext(projectId);
        setContext(data);
        setMessages([
          {
            role: "ai",
            content:
              "Llama 3.1 is ready to validate or generate Dockerfiles. Share any extra instructions or build logs to begin.",
          },
        ]);
        setError(null);
      } catch (err) {
        setError(
          err instanceof Error ? err.message : "Failed to load deploy context"
        );
      } finally {
        setLoading(false);
      }
    };
    fetchContext();
  }, [projectId]);

  const refreshExplorer = async () => {
    if (!projectId) return;
    try {
      setRefreshingTree(true);
      const data = await apiClient.getDockerContext(projectId);
      setContext(data);
    } catch (err) {
      setMessages((prev) => [
        ...prev,
        {
          role: "ai",
          content:
            err instanceof Error
              ? `Unable to refresh explorer: ${err.message}`
              : "Unable to refresh explorer",
        },
      ]);
    } finally {
      setRefreshingTree(false);
    }
  };

  const joinPaths = (base: string | null | undefined, name: string) => {
    const cleanBase = base ? base.replace(/\/+$/, "") : "";
    const cleanName = name.replace(/^\/+/, "");
    return cleanBase ? `${cleanBase}/${cleanName}` : cleanName;
  };

  const handleSend = async () => {
    if (!projectId || !input.trim()) return;

    const messageText = input.trim();
    const logsText = logInput.trim();
    const instructionsText = instructions.trim();

    const userMsg: ChatMessage = { role: "user", content: messageText };
    setMessages((prev) => [...prev, userMsg]);
    setSending(true);
    setInput("");

    const { streamDockerChat } = await import("../api/client");
    const params: { message: string; logs?: string[]; instructions?: string } = {
      message: messageText,
    };
    if (logsText) {
      params.logs = logsText.split("\n").slice(-50);
    }
    if (instructionsText) {
      params.instructions = instructionsText;
    }

    let accumulatedContent = "";
    setMessages((prev) => [...prev, { role: "ai", content: "" }]);

    streamDockerChat(
      projectId,
      params,
      (token) => {
        accumulatedContent += token;
        setMessages((prev) => {
          const newMessages = [...prev];
          for (let i = newMessages.length - 1; i >= 0; i--) {
            if (newMessages[i].role === "ai") {
              newMessages[i] = { role: "ai", content: accumulatedContent };
              break;
            }
          }
          return newMessages;
        });
      },
      () => {
        setSending(false);
      },
      (error) => {
        setMessages((prev) => {
          const newMessages = [...prev];
          for (let i = newMessages.length - 1; i >= 0; i--) {
            if (newMessages[i].role === "ai") {
              if (newMessages[i].content === "") {
                newMessages[i] = { role: "ai", content: `Error: ${error.message}` };
              }
              break;
            }
          }
          return newMessages;
        });
        setSending(false);
      }
    );
  };

  const appendLogs = (incoming: LogLine) => {
    setLogs((prev) => [...prev, incoming]);
  };

  const startStream = (action: "build" | "run" | "push") => {
    if (!projectId) return;
    if (eventSource) {
      eventSource.close();
    }
    setLogs([]);
    const token = apiClient.getToken();
    const url = new URL(`${apiBase}/docker/${projectId}/logs`);
    url.searchParams.set("action", action);
    if (token) {
      url.searchParams.set("token", token);
    }
    const source = new EventSource(url.toString());
    source.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        appendLogs({
          line: data.line,
          stage: data.stage,
          exit_code: data.exit_code,
          complete: data.complete,
        });

        if (data.complete && typeof data.exit_code === "number") {
          source.close();
          setEventSource(null);
          if (data.exit_code !== 0) {
            const tail: string[] = data.tail || [];
            const summary = tail.slice(-20).join("\n");
            setMessages((prev) => [
              ...prev,
              {
                role: "ai",
                content: `${action} failed, sending logs to Llama 3.1 for analysis...`,
              },
            ]);
            apiClient
              .sendDockerChat(projectId, {
                message: `${action} failed. Analyze these logs and fix Dockerfile.`,
                logs: summary ? summary.split("\n") : undefined,
              })
              .then((resp) =>
                setMessages((prev) => [...prev, { role: "ai", content: resp.reply }])
              )
              .catch((err) =>
                setMessages((prev) => [
                  ...prev,
                  {
                    role: "ai",
                    content:
                      err instanceof Error
                        ? `Error sending logs to Llama 3.1: ${err.message}`
                        : "Error sending logs to Llama 3.1",
                  },
                ])
              );
          }
        }
      } catch (err) {
        appendLogs({
          line: err instanceof Error ? err.message : "Malformed log event",
          stage: action,
        });
      }
    };
    source.onerror = () => {
      appendLogs({ line: "Log stream error or closed.", stage: action });
      source.close();
      setEventSource(null);
    };
    setEventSource(source);
  };

  useEffect(() => {
    return () => {
      if (eventSource) {
        eventSource.close();
      }
    };
  }, [eventSource]);

  const handleFileSelect = async (node: FileNode) => {
    if (node.is_dir || !projectId) return;
    try {
      const resp = await apiClient.readProjectFile(projectId, node.path);
      setOpenFiles((prev) =>
        prev.includes(node.path) ? prev : [...prev, node.path]
      );
      setActiveFile(node.path);
      setFileContents((prev) => ({ ...prev, [node.path]: resp.content }));
      setDirtyFlags((prev) => ({ ...prev, [node.path]: false }));
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Unable to load file";
      setFileContents((prev) => ({ ...prev, [node.path]: msg }));
      setDirtyFlags((prev) => ({ ...prev, [node.path]: false }));
    }
  };

  const handleSaveFile = async () => {
    if (!projectId || !activeFile || !dirtyFlags[activeFile]) return;
    try {
      setSaving(true);
      await apiClient.writeProjectFile(projectId, {
        path: activeFile,
        content: fileContents[activeFile] || "",
      });
      setDirtyFlags((prev) => ({ ...prev, [activeFile]: false }));
      setMessages((prev) => [
        ...prev,
        { role: "ai", content: `Saved ${activeFile}. Re-run build if needed.` },
      ]);
    } catch (err) {
      setMessages((prev) => [
        ...prev,
        {
          role: "ai",
          content:
            err instanceof Error ? `Save failed: ${err.message}` : "Save failed",
        },
      ]);
    } finally {
      setSaving(false);
    }
  };

  const pruneDeletedState = (targetPath: string) => {
    const normalized = targetPath.replace(/\/+$/, "");
    const prefix = `${normalized}/`;

    setOpenFiles((prev) =>
      prev.filter(
        (p) => p !== normalized && !p.startsWith(prefix)
      )
    );
    setFileContents((prev) => {
      const next = { ...prev };
      Object.keys(next).forEach((key) => {
        if (key === normalized || key.startsWith(prefix)) {
          delete next[key];
        }
      });
      return next;
    });
    setDirtyFlags((prev) => {
      const next = { ...prev };
      Object.keys(next).forEach((key) => {
        if (key === normalized || key.startsWith(prefix)) {
          delete next[key];
        }
      });
      return next;
    });
    if (activeFile && (activeFile === normalized || activeFile.startsWith(prefix))) {
      setActiveFile(null);
    }
  };

  const handleCreateFile = async (basePath?: string | null) => {
    if (!projectId) return;
    const name = window.prompt("New file name (relative to this folder)");
    if (!name || !name.trim()) return;
    const fullPath = joinPaths(basePath, name.trim());
    try {
      await apiClient.createProjectFile(projectId, fullPath, "");
      await refreshExplorer();
      setMessages((prev) => [
        ...prev,
        { role: "ai", content: `Created file ${fullPath}` },
      ]);
    } catch (err) {
      setMessages((prev) => [
        ...prev,
        {
          role: "ai",
          content:
            err instanceof Error ? `Create file failed: ${err.message}` : "Create file failed",
        },
      ]);
    }
  };

  const handleCreateFolder = async (basePath?: string | null) => {
    if (!projectId) return;
    const name = window.prompt("New folder name (relative to this folder)");
    if (!name || !name.trim()) return;
    const fullPath = joinPaths(basePath, name.trim());
    try {
      await apiClient.createProjectFolder(projectId, fullPath);
      await refreshExplorer();
      setMessages((prev) => [
        ...prev,
        { role: "ai", content: `Created folder ${fullPath}` },
      ]);
    } catch (err) {
      setMessages((prev) => [
        ...prev,
        {
          role: "ai",
          content:
            err instanceof Error ? `Create folder failed: ${err.message}` : "Create folder failed",
        },
      ]);
    }
  };

  const handleDeletePath = async (targetPath: string) => {
    if (!projectId) return;
    const confirmed = window.confirm(`Delete ${targetPath}? This cannot be undone.`);
    if (!confirmed) return;
    try {
      await apiClient.deleteProjectPath(projectId, targetPath);
      pruneDeletedState(targetPath);
      await refreshExplorer();
      setMessages((prev) => [
        ...prev,
        { role: "ai", content: `Deleted ${targetPath}` },
      ]);
    } catch (err) {
      setMessages((prev) => [
        ...prev,
        {
          role: "ai",
          content:
            err instanceof Error ? `Delete failed: ${err.message}` : "Delete failed",
        },
      ]);
    }
  };

  const renderFileTree = (nodes: FileNode[], depth = 0) => {
    return nodes.map((node) => (
      <div
        key={node.path}
        className={`mb-1 ${depth > 0 ? "border-l border-gray-800" : ""}`}
        style={{ paddingLeft: `${depth * 12}px` }}
      >
        <div
          className={`flex items-center justify-between group rounded px-2 py-1 hover:bg-gray-700/50 ${!node.is_dir && activeFile === node.path ? "bg-gray-700/60 border border-cyan-600/40" : ""
            }`}
        >
          <div className="flex items-center gap-2">
            {node.is_dir ? (
              <button
                className="text-cyan-300 hover:text-white text-xs"
                onClick={() =>
                  setExpandedDirs((prev) => ({
                    ...prev,
                    [node.path]: !(prev[node.path] ?? true),
                  }))
                }
                aria-label={expandedDirs[node.path] ?? true ? "Collapse folder" : "Expand folder"}
              >
                {(expandedDirs[node.path] ?? true) ? (<ChevronDown size={12} />) : (<ChevronRight size={12} />)}
              </button>
            ) : (
              <span className="text-gray-600 text-xs">-</span>
            )}
            <button
              className={`text-left text-sm ${node.is_dir ? "text-cyan-200" : activeFile === node.path ? "text-white" : "text-gray-200"
                } hover:text-white`}
              onClick={() => (node.is_dir ? setExpandedDirs((prev) => ({ ...prev, [node.path]: !(prev[node.path] ?? true) })) : handleFileSelect(node))}
            >
              {node.name}
            </button>
          </div>
          <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
            {node.is_dir && (
              <>
                <button
                  className="text-[10px] px-2 py-0.5 rounded bg-gray-900 border border-gray-700 text-cyan-300 hover:border-cyan-500"
                  onClick={(e) => {
                    e.stopPropagation();
                    handleCreateFile(node.path);
                  }}
                  disabled={refreshingTree}
                  aria-label={`Add file in ${node.path}`}
                >
                  File
                </button>
                <button
                  className="text-[10px] px-2 py-0.5 rounded bg-gray-900 border border-gray-700 text-cyan-300 hover:border-cyan-500"
                  onClick={(e) => {
                    e.stopPropagation();
                    handleCreateFolder(node.path);
                  }}
                  disabled={refreshingTree}
                  aria-label={`Add folder in ${node.path}`}
                >
                  Dir
                </button>
              </>
            )}
            <button
              className="text-[10px] px-2 py-0.5 rounded bg-gray-900 border border-gray-700 text-red-300 hover:border-red-500"
              onClick={(e) => {
                e.stopPropagation();
                handleDeletePath(node.path);
              }}
              disabled={refreshingTree}
            >
              Delete
            </button>
          </div>
        </div>
        {node.is_dir && (expandedDirs[node.path] ?? true) && node.children && node.children.length > 0 && (
          <div className="mt-1">{renderFileTree(node.children, depth + 1)}</div>
        )}
      </div>
    ));
  };

  const metadataList = useMemo(() => {
    if (!context) return [];
    const m = context.metadata;
    return [
      { label: "Framework", value: m.framework },
      { label: "Language", value: m.language },
      { label: "Runtime", value: m.runtime },
      { label: "Port", value: m.port },
      { label: "Backend Port", value: m.backend_port },
      { label: "Frontend Port", value: m.frontend_port },
      { label: "Database", value: m.database },
      { label: "Database Port", value: m.database_port },
    ].filter((item) => item.value !== undefined && item.value !== null);
  }, [context]);

  if (loading) {
    return <LoadingSpinner message="Loading deploy workspace..." />;
  }

  if (error || !context) {
    return (
      <div className="min-h-screen flex flex-col items-center justify-center gap-4 animate-fade-in" style={{ background: 'var(--bg-base)' }}>
        <div className="flex items-center gap-3 text-rose-400">
          <AlertCircle size={20} />
          <p className="text-sm font-medium">{error || 'Unable to load deploy context'}</p>
        </div>
        <Button variant="secondary" onClick={() => navigate('/dashboard')}>
          <ArrowLeft size={15} /> Back to Dashboard
        </Button>
      </div>
    );
  }

  return (
    <div className="min-h-screen relative overflow-hidden flex flex-col bg-[#050810]">
      <ThreeBackground />
      <div className="absolute inset-0 grid-bg opacity-30 pointer-events-none" style={{ zIndex: 1 }} />

      <div className="relative z-10 flex flex-col h-full bg-[#050810]/40">
        <Navbar />

        <main className="flex-1 max-w-[1600px] w-full mx-auto px-6 py-8 flex flex-col gap-8">

          {/* Hero Header */}
          <div className="animate-fade-in">
            <div className="flex flex-col md:flex-row md:items-center justify-between gap-6 mb-8">
              <div>
                <h1 className="text-4xl md:text-5xl font-black text-white tracking-tighter uppercase flex items-center gap-4">
                  {deployMode === "docker" ? (
                    <><Container size={36} className="text-cyan-400" /> Docker Orchestration</>
                  ) : deployMode === "aws" ? (
                    <><Cloud size={36} className="text-orange-400" /> Cloud Deployment</>
                  ) : (
                    <><Settings size={36} className="text-violet-400" /> Live Monitoring</>
                  )}
                </h1>
                <p className="text-gray-500 mt-2 font-medium flex items-center gap-2">
                  <span className="w-1.5 h-1.5 rounded-full bg-cyan-400 animate-pulse"></span>
                  Active Workspace: <span className="text-white font-bold">{context.project.project_name}</span>
                </p>
              </div>

              <div className="flex items-center gap-4">
                <div className="bg-white/5 border border-white/10 rounded-2xl p-1.5 flex gap-1">
                  <button
                    onClick={() => setDeployMode("docker")}
                    className={`px-5 py-2.5 rounded-xl text-xs font-black tracking-widest uppercase transition-all ${deployMode === "docker" ? "bg-white text-black shadow-lg shadow-white/10" : "text-gray-500 hover:text-white"
                      }`}
                  >
                    Infrastructure
                  </button>
                  <button
                    onClick={() => setDeployMode("aws")}
                    className={`px-5 py-2.5 rounded-xl text-xs font-black tracking-widest uppercase transition-all ${deployMode === "aws" ? "bg-orange-500 text-white shadow-lg shadow-orange-500/20" : "text-gray-500 hover:text-white"
                      }`}
                  >
                    Cloud (AWS)
                  </button>
                  <button
                    onClick={() => setDeployMode("monitor")}
                    className={`px-5 py-2.5 rounded-xl text-xs font-black tracking-widest uppercase transition-all ${deployMode === "monitor" ? "bg-violet-500 text-white shadow-lg shadow-violet-500/20" : "text-gray-500 hover:text-white"
                      }`}
                  >
                    Monitor
                  </button>
                </div>
                <Button variant="secondary" onClick={() => navigate("/dashboard")}>
                  <ArrowLeft size={16} /> Back to Hub
                </Button>
              </div>
            </div>
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 min-h-[72vh]">
            {/* Left sidebar */}
            <div className="lg:col-span-3 flex flex-col gap-6 overflow-y-auto pr-1 custom-scroll">
              <Card className="p-6 bg-white/[0.02] border-white/5 shadow-xl">
                <div className="flex items-center justify-between mb-6">
                  <div className="flex items-center gap-3">
                    <div className="p-2.5 bg-cyan-500/10 rounded-xl text-cyan-400">
                      <Rocket size={18} />
                    </div>
                    <div>
                      <p className="text-[10px] font-black uppercase tracking-widest text-gray-500">Workspace</p>
                      <p className="text-sm font-black text-white">{rootLabel}</p>
                    </div>
                  </div>
                  <button
                    className="w-9 h-9 flex items-center justify-center rounded-xl bg-white/5 border border-white/10 text-gray-400 hover:text-white transition-all shadow-sm"
                    onClick={() => refreshExplorer()}
                    disabled={refreshingTree}
                  >
                    <RefreshCw size={14} className={refreshingTree ? "animate-spin" : ""} />
                  </button>
                </div>

                <div className="flex flex-wrap gap-2 mb-4">
                  <button
                    className="flex items-center gap-2 text-[10px] font-black uppercase tracking-widest px-3 py-2 rounded-xl bg-white/5 border border-white/5 text-gray-400 hover:text-white hover:border-cyan-500 transition-all"
                    onClick={() => handleCreateFile(null)}
                    disabled={refreshingTree}
                  >
                    <FilePlus2 size={12} className="text-cyan-400" />
                    Add File
                  </button>
                  <button
                    className="flex items-center gap-2 text-[10px] font-black uppercase tracking-widest px-3 py-2 rounded-xl bg-white/5 border border-white/5 text-gray-400 hover:text-white hover:border-cyan-500 transition-all"
                    onClick={() => handleCreateFolder(null)}
                    disabled={refreshingTree}
                  >
                    <FolderPlus size={12} className="text-cyan-400" />
                    Add Dir
                  </button>
                </div>

                <div className="max-h-[500px] overflow-y-auto custom-scroll rounded-2xl border border-white/5 bg-black/20 p-4">
                  {renderFileTree(context.file_tree.tree)}
                </div>
              </Card>

              <Card className="p-6 bg-white/[0.02] border-white/5">
                <h3 className="text-[10px] font-black uppercase tracking-widest text-gray-500 mb-6 flex items-center gap-2">
                  <Settings size={14} /> System Profile
                </h3>
                <div className="grid grid-cols-2 gap-3">
                  {metadataList.map((item) => (
                    <div key={item.label} className="bg-white/5 rounded-xl p-3 border border-white/5">
                      <p className="text-[9px] font-black text-gray-600 uppercase mb-1">{item.label}</p>
                      <p className="text-xs font-bold truncate text-white">{item.value as string}</p>
                    </div>
                  ))}
                </div>
              </Card>
            </div>

            {/* Center panel */}
            <div className="lg:col-span-6 flex flex-col gap-6">
              {deployMode === "monitor" && projectId ? (
                <MonitoringDashboard projectId={projectId} />
              ) : deployMode === "aws" && projectId ? (
                <Card className="flex-1 flex flex-col p-8 overflow-y-auto custom-scroll bg-white/[0.02] border-white/5 shadow-2xl min-h-[500px]">
                  <AWSDeployPanel
                    projectId={projectId}
                    onLog={(message) => setMessages((prev) => [...prev, { role: "ai", content: message }])}
                    onTerraformGenerated={refreshExplorer}
                  />
                </Card>
              ) : (
                <Card className="flex-1 flex flex-col p-0 overflow-hidden bg-white/[0.02] border-white/5 relative shadow-2xl min-h-[500px]">
                  <div className="flex items-center justify-between px-6 py-4 border-b border-white/5 bg-white/[0.03]">
                    <div className="flex gap-2.5 overflow-x-auto no-scrollbar py-1">
                      {openFiles.length === 0 ? (
                        <div className="flex items-center gap-2 text-xs text-gray-500 font-bold uppercase tracking-widest opacity-50">
                          <Code2 size={14} /> Source Explorer
                        </div>
                      ) : (
                        openFiles.map((path) => (
                          <button
                            key={path}
                            className={`px-4 py-2 rounded-xl text-xs font-black tracking-widest uppercase transition-all flex items-center gap-2 ${activeFile === path
                              ? "bg-white text-black shadow-lg shadow-white/5"
                              : "bg-white/5 text-gray-500 hover:text-gray-300 border border-white/5"
                              }`}
                            onClick={() => setActiveFile(path)}
                          >
                            {dirtyFlags[path] && <span className="w-1.5 h-1.5 rounded-full bg-cyan-400"></span>}
                            {path.split('/').pop()}
                          </button>
                        ))
                      )}
                    </div>
                  </div>

                  <div className="flex-1 flex flex-col min-h-0 relative">
                    <textarea
                      value={(activeFile && fileContents[activeFile]) || ''}
                      onChange={(e) => {
                        const val = e.target.value;
                        if (!activeFile) return;
                        setFileContents((prev) => ({ ...prev, [activeFile]: val }));
                        setDirtyFlags((prev) => ({ ...prev, [activeFile]: true }));
                      }}
                      className="w-full flex-1 p-8 text-[13px] focus:outline-none resize-none custom-scroll font-mono leading-relaxed"
                      style={{ background: 'transparent', color: '#f0f6fc', border: 'none' }}
                      placeholder="Initialize workspace by selecting a manifest from the explorer..."
                      spellCheck={false}
                    />
                  </div>

                  <div className="px-6 py-4 border-t border-white/5 bg-black/40 flex justify-between items-center">
                    <div className="flex items-center gap-2 text-gray-600 font-mono text-[10px] uppercase">
                      <span className="w-2 h-2 rounded-full bg-gray-700"></span>
                      {activeFile || 'IDLE_MODE'}
                    </div>
                    <Button
                      variant="primary"
                      disabled={!activeFile || !dirtyFlags[activeFile] || saving}
                      loading={saving}
                      onClick={handleSaveFile}
                    >
                      COMMIT_CHANGES
                    </Button>
                  </div>
                </Card>
              )}
            </div>

            {/* Right sidebar */}
            <div className="lg:col-span-3 flex flex-col gap-6">
              <Card className="p-8 bg-white/[0.02] border-white/5 flex flex-col gap-6">
                {deployMode === "monitor" ? (
                  <>
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-3">
                        <Settings size={18} className="text-violet-400" />
                        <h3 className="text-xs font-black uppercase tracking-widest text-white">MONITORING</h3>
                      </div>
                      <Badge variant="info">LIVE</Badge>
                    </div>
                    <p className="text-xs text-gray-500 leading-relaxed">
                      Kubernetes and cloud health checks run in the monitoring panel.
                    </p>
                  </>
                ) : deployMode === "docker" ? (
                  <>
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-3">
                        <TerminalIcon size={18} className="text-cyan-400" />
                        <h3 className="text-xs font-black uppercase tracking-widest text-white">ORCHESTRATION</h3>
                      </div>
                      <Badge variant="info">DOCKER_BUILD</Badge>
                    </div>

                    {context.metadata.deploy_blocked && (
                      <div className="p-4 bg-yellow-500/5 border border-yellow-500/10 rounded-2xl">
                        <p className="text-[10px] text-yellow-500 font-black uppercase tracking-widest mb-1">Blocker Detected</p>
                        <p className="text-xs text-yellow-300 opacity-60 leading-relaxed">
                          {context.metadata.deploy_blocked_reason || "Missing .env configurations."}
                        </p>
                      </div>
                    )}

                    <div className="flex flex-col gap-2">
                      {["build", "run", "push"].map(action => (
                        <Button
                          key={action}
                          variant="secondary"
                          className="w-full h-11 text-xs font-black uppercase tracking-widest"
                          onClick={() => startStream(action as any)}
                          disabled={context.metadata.deploy_blocked}
                        >
                          {action}_IMAGE
                        </Button>
                      ))}
                    </div>

                    <div className="bg-[#050810] rounded-2xl p-6 font-mono text-[10px] min-h-[150px] max-h-[250px] overflow-y-auto custom-scroll border border-white/5">
                      {logs.length === 0 ? (
                        <p className="text-gray-700 italic">Awaiting event stream...</p>
                      ) : (
                        logs.map((l, idx) => (
                          <div key={idx} className="mb-2 text-gray-500 leading-relaxed">
                            <span className="text-cyan-400 mr-3">[{l.stage.toUpperCase()}]</span>
                            {l.line}
                          </div>
                        ))
                      )}
                    </div>
                  </>
                ) : (
                  <>
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-3">
                        <Cloud size={18} className="text-orange-400" />
                        <h3 className="text-xs font-black uppercase tracking-widest text-white">CLOUD_UNIT</h3>
                      </div>
                      <Badge variant="warning">AWS</Badge>
                    </div>
                    <p className="text-xs text-gray-500 leading-relaxed">
                      Configure, deploy, and manage AWS infrastructure in the main panel.
                    </p>
                  </>
                )}
              </Card>

              <div className="flex-1">
                <AIChatSidebar
                  messages={messages}
                  input={input}
                  onInputChange={setInput}
                  onSend={() => { if (!context.metadata.deploy_blocked) handleSend(); }}
                  sending={sending || !!context.metadata.deploy_blocked}
                  logInput={logInput}
                  onLogInputChange={setLogInput}
                  instructions={instructions}
                  onInstructionsChange={setInstructions}
                />
              </div>
            </div>
          </div>
        </main>
      </div>
    </div>
  );
};
