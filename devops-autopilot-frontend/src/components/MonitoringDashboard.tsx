import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  Activity,
  AlertCircle,
  AlertTriangle,
  Box,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  Clock,
  Container,
  Cpu,
  HeartPulse,
  Info,
  Layers,
  PlayCircle,
  RefreshCw,
  Server,
  Square,
  Terminal,
  Wifi,
  WifiOff,
  XCircle,
} from "lucide-react";
import { apiClient, streamMonitorLogs } from "../api/client";
import { Button } from "./Button";
import { MonitorStatus } from "../types/api";

interface MonitoringDashboardProps {
  projectId: string;
}

// ── helpers ────────────────────────────────────────────────────────────────

function podStatusColor(status: string) {
  const s = status.toLowerCase();
  if (s.includes("running")) return "text-green-400";
  if (s.includes("pending") || s.includes("waiting")) return "text-yellow-400";
  if (s.includes("terminated") || s.includes("error") || s.includes("crash"))
    return "text-red-400";
  return "text-gray-400";
}

function podStatusBg(status: string) {
  const s = status.toLowerCase();
  if (s.includes("running")) return "bg-green-500/15 border-green-500/30";
  if (s.includes("pending") || s.includes("waiting"))
    return "bg-yellow-500/15 border-yellow-500/30";
  if (s.includes("terminated") || s.includes("error") || s.includes("crash"))
    return "bg-red-500/15 border-red-500/30";
  return "bg-gray-500/15 border-gray-500/30";
}

function PodStatusIcon({ status }: { status: string }) {
  const s = status.toLowerCase();
  if (s.includes("running"))
    return <CheckCircle2 size={13} className="text-green-400" />;
  if (s.includes("pending") || s.includes("waiting"))
    return <Clock size={13} className="text-yellow-400" />;
  if (s.includes("terminated") || s.includes("error") || s.includes("crash"))
    return <XCircle size={13} className="text-red-400" />;
  return <AlertCircle size={13} className="text-gray-400" />;
}

function logLineColor(line: string) {
  const l = line.toLowerCase();
  if (l.includes("error") || l.includes("exception") || l.includes("fatal"))
    return "text-red-400";
  if (l.includes("warn") || l.includes("warning")) return "text-yellow-300";
  if (l.includes("info") || l.match(/^\d{4}-\d{2}-\d{2}/))
    return "text-gray-300";
  if (l.includes("debug")) return "text-gray-500";
  return "text-gray-300";
}

function relativeTime(ts: string) {
  if (!ts) return "—";
  const diff = Math.floor((Date.now() - new Date(ts).getTime()) / 1000);
  if (diff < 60) return `${diff}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

// ── component ──────────────────────────────────────────────────────────────

export const MonitoringDashboard: React.FC<MonitoringDashboardProps> = ({
  projectId,
}) => {
  const [status, setStatus] = useState<MonitorStatus | null>(null);
  const [logs, setLogs] = useState<{ ts: string; line: string }[]>([]);
  const [loading, setLoading] = useState(true);
  const [healing, setHealing] = useState(false);
  const [error, setError] = useState("");
  const [isStreaming, setIsStreaming] = useState(false);
  const [eventsOpen, setEventsOpen] = useState(true);
  const [podsOpen, setPodsOpen] = useState(true);
  const [lastRefresh, setLastRefresh] = useState(new Date());
  const esRef = useRef<EventSource | null>(null);
  const logsEndRef = useRef<HTMLDivElement | null>(null);

  // ── fetch status ──────────────────────────────────────────────────────────

  const fetchStatus = useCallback(async () => {
    try {
      const data = await apiClient.getMonitorStatus(projectId);
      if (data.success) {
        setStatus(data);
        setError("");
      } else {
        setError(data.message || "Failed to fetch status");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not reach backend");
    } finally {
      setLoading(false);
      setLastRefresh(new Date());
    }
  }, [projectId]);

  useEffect(() => {
    fetchStatus();
    const interval = setInterval(fetchStatus, 5000);
    return () => clearInterval(interval);
  }, [fetchStatus]);

  useEffect(() => {
    if (logsEndRef.current && isStreaming) {
      logsEndRef.current.scrollIntoView({ behavior: "smooth" });
    }
  }, [logs, isStreaming]);

  useEffect(
    () => () => {
      esRef.current?.close();
    },
    []
  );

  // ── heal ──────────────────────────────────────────────────────────────────

  const handleHeal = async () => {
    setHealing(true);
    try {
      const res = await apiClient.healProject(projectId);
      if (res.success) await fetchStatus();
      else setError(res.message || "Heal request failed");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Heal request failed");
    } finally {
      setHealing(false);
    }
  };

  // ── log stream ────────────────────────────────────────────────────────────

  const toggleStream = () => {
    if (isStreaming) {
      esRef.current?.close();
      esRef.current = null;
      setIsStreaming(false);
    } else {
      setLogs([]);
      const ts = () => new Date().toLocaleTimeString([], { hour12: false });
      const source = streamMonitorLogs(
        projectId,
        (line) => setLogs((prev) => [...prev, { ts: ts(), line }].slice(-200)),
        () => setIsStreaming(false)
      );
      esRef.current = source;
      setIsStreaming(true);
    }
  };

  // ── loading ───────────────────────────────────────────────────────────────

  if (loading && !status) {
    return (
      <div className="flex flex-col items-center justify-center h-full min-h-[400px] gap-4 text-gray-500">
        <RefreshCw size={28} className="animate-spin text-cyan-500" />
        <p className="text-sm font-mono uppercase tracking-widest">
          Connecting to cluster...
        </p>
      </div>
    );
  }

  // ── derived values ────────────────────────────────────────────────────────

  const allPods = status?.pods || [];
  const runningPods = allPods.filter((p) =>
    p.status.toLowerCase().includes("running")
  );
  const events = status?.recent_events || [];
  const warningEvents = events.filter((e) => e.type === "Warning");
  const k8sState = status?.kubernetes?.state || "Unknown";
  const isHealthy = status?.overall_healthy ?? false;
  const deploymentName = status?.deployment_name || "—";

  // ── render ────────────────────────────────────────────────────────────────

  return (
    <div className="flex flex-col gap-4 h-full">
      {/* ── Header strip ── */}
      <div className="rounded-2xl bg-white/[0.03] border border-white/5 p-4 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div
            className={`w-2.5 h-2.5 rounded-full ${isHealthy ? "bg-green-500 shadow-[0_0_8px_rgba(74,222,128,0.8)]" : "bg-red-500 shadow-[0_0_8px_rgba(239,68,68,0.6)]"} animate-pulse`}
          />
          <div>
            <p className="text-xs font-black uppercase tracking-widest text-white">
              {status?.project_name || "Monitoring"}
            </p>
            <p className="text-[10px] text-gray-500 font-mono">
              {deploymentName}
            </p>
          </div>
        </div>

        <div className="flex items-center gap-3">
          <span className="text-[10px] text-gray-600 font-mono flex items-center gap-1">
            <Clock size={10} />
            {lastRefresh.toLocaleTimeString([], { hour12: false })}
          </span>

          {/* Overall status badge */}
          <div
            className={`px-3 py-1 rounded-full text-[10px] font-black uppercase tracking-widest border flex items-center gap-1.5 ${
              isHealthy
                ? "bg-green-500/10 text-green-400 border-green-500/30"
                : "bg-red-500/10 text-red-400 border-red-500/30"
            }`}
          >
            {isHealthy ? (
              <Wifi size={10} />
            ) : (
              <WifiOff size={10} />
            )}
            {isHealthy ? "Healthy" : "Degraded"}
          </div>

          <Button
            variant="secondary"
            size="sm"
            onClick={handleHeal}
            loading={healing}
            className="py-1 px-3 text-[10px] font-black uppercase tracking-widest border-orange-500/30 text-orange-400 hover:bg-orange-500/10"
          >
            <HeartPulse size={11} className="mr-1" /> Auto-Heal
          </Button>

          <button
            onClick={fetchStatus}
            className="w-7 h-7 flex items-center justify-center rounded-xl bg-white/5 border border-white/10 text-gray-400 hover:text-white transition-all"
          >
            <RefreshCw size={12} />
          </button>
        </div>
      </div>

      {error && (
        <div className="flex items-center gap-2 p-3 bg-red-500/10 border border-red-500/20 rounded-xl text-xs text-red-400">
          <AlertCircle size={13} />
          {error}
        </div>
      )}

      {/* ── Stat cards ── */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        {[
          {
            icon: <Box size={16} className="text-cyan-400" />,
            label: "Total Pods",
            value: allPods.length,
            sub: `${runningPods.length} running`,
            color: "cyan",
          },
          {
            icon: <Activity size={16} className="text-green-400" />,
            label: "K8s State",
            value: k8sState,
            sub: status?.kubernetes?.reason || "—",
            color: k8sState === "Running" ? "green" : "red",
          },
          {
            icon: <AlertTriangle size={16} className="text-yellow-400" />,
            label: "Warnings",
            value: warningEvents.length,
            sub: `${events.length} total events`,
            color: warningEvents.length > 0 ? "yellow" : "green",
          },
          {
            icon: <RefreshCw size={16} className="text-purple-400" />,
            label: "Restarts",
            value: status?.kubernetes?.restart_count ?? 0,
            sub: "across all pods",
            color:
              (status?.kubernetes?.restart_count ?? 0) > 0 ? "yellow" : "green",
          },
        ].map((card) => (
          <div
            key={card.label}
            className="bg-white/[0.02] border border-white/5 rounded-2xl p-4"
          >
            <div className="flex items-center justify-between mb-2">
              {card.icon}
              <span className="text-[9px] font-black uppercase tracking-widest text-gray-600">
                {card.label}
              </span>
            </div>
            <p className="text-xl font-black text-white truncate">{card.value}</p>
            <p className="text-[10px] text-gray-600 mt-0.5 truncate">{card.sub}</p>
          </div>
        ))}
      </div>

      {/* ── Pods Table ── */}
      <div className="bg-white/[0.02] border border-white/5 rounded-2xl overflow-hidden">
        <button
          className="w-full flex items-center justify-between px-5 py-3.5 hover:bg-white/[0.02] transition-all"
          onClick={() => setPodsOpen((v) => !v)}
        >
          <div className="flex items-center gap-2 text-xs font-black uppercase tracking-widest text-white">
            <Layers size={14} className="text-cyan-400" />
            Pod Status ({allPods.length})
          </div>
          {podsOpen ? (
            <ChevronUp size={14} className="text-gray-500" />
          ) : (
            <ChevronDown size={14} className="text-gray-500" />
          )}
        </button>

        {podsOpen && (
          <div className="border-t border-white/5">
            {allPods.length === 0 ? (
              <div className="flex items-center gap-2 p-5 text-xs text-gray-600">
                <Info size={14} />
                No pods found in the default namespace. Deploy to Kubernetes
                first.
              </div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="border-b border-white/5">
                      {["Pod Name", "Status", "Ready", "Restarts", "IP", "Age"].map(
                        (h) => (
                          <th
                            key={h}
                            className="text-left px-4 py-2.5 text-[9px] font-black uppercase tracking-widest text-gray-600"
                          >
                            {h}
                          </th>
                        )
                      )}
                    </tr>
                  </thead>
                  <tbody>
                    {allPods.map((pod, i) => (
                      <tr
                        key={i}
                        className="border-b border-white/[0.03] hover:bg-white/[0.02] transition-all"
                      >
                        <td className="px-4 py-2.5 font-mono text-gray-300 max-w-[200px] truncate">
                          {pod.name}
                        </td>
                        <td className="px-4 py-2.5">
                          <span
                            className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full border text-[10px] font-bold ${podStatusBg(pod.status)}`}
                          >
                            <PodStatusIcon status={pod.status} />
                            <span className={podStatusColor(pod.status)}>
                              {pod.status}
                            </span>
                          </span>
                        </td>
                        <td className="px-4 py-2.5">
                          {pod.ready ? (
                            <CheckCircle2 size={13} className="text-green-400" />
                          ) : (
                            <XCircle size={13} className="text-red-400" />
                          )}
                        </td>
                        <td className="px-4 py-2.5 font-mono">
                          <span
                            className={
                              pod.restart_count > 0
                                ? "text-yellow-400"
                                : "text-gray-500"
                            }
                          >
                            {pod.restart_count}
                          </span>
                        </td>
                        <td className="px-4 py-2.5 font-mono text-gray-500 text-[10px]">
                          {pod.pod_ip || "—"}
                        </td>
                        <td className="px-4 py-2.5 text-gray-600">
                          {relativeTime(pod.created_at || "")}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}
      </div>

      {/* ── K8s Events ── */}
      <div className="bg-white/[0.02] border border-white/5 rounded-2xl overflow-hidden">
        <button
          className="w-full flex items-center justify-between px-5 py-3.5 hover:bg-white/[0.02] transition-all"
          onClick={() => setEventsOpen((v) => !v)}
        >
          <div className="flex items-center gap-2 text-xs font-black uppercase tracking-widest text-white">
            <Server size={14} className="text-purple-400" />
            Kubernetes Events ({events.length})
            {warningEvents.length > 0 && (
              <span className="ml-1 px-1.5 py-0.5 bg-yellow-500/20 text-yellow-400 text-[9px] rounded-full border border-yellow-500/30">
                {warningEvents.length} warnings
              </span>
            )}
          </div>
          {eventsOpen ? (
            <ChevronUp size={14} className="text-gray-500" />
          ) : (
            <ChevronDown size={14} className="text-gray-500" />
          )}
        </button>

        {eventsOpen && (
          <div className="border-t border-white/5 max-h-[200px] overflow-y-auto custom-scroll">
            {events.length === 0 ? (
              <div className="flex items-center gap-2 p-5 text-xs text-gray-600">
                <CheckCircle2 size={14} className="text-green-500" />
                No recent Kubernetes events — cluster is quiet.
              </div>
            ) : (
              events
                .slice()
                .reverse()
                .map((ev, i) => (
                  <div
                    key={i}
                    className={`flex items-start gap-3 px-4 py-2.5 border-b border-white/[0.03] last:border-0 ${
                      ev.type === "Warning"
                        ? "bg-yellow-500/[0.04]"
                        : "hover:bg-white/[0.02]"
                    }`}
                  >
                    {ev.type === "Warning" ? (
                      <AlertTriangle
                        size={12}
                        className="text-yellow-400 mt-0.5 shrink-0"
                      />
                    ) : (
                      <Info
                        size={12}
                        className="text-blue-400 mt-0.5 shrink-0"
                      />
                    )}
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 mb-0.5">
                        <span
                          className={`text-[10px] font-bold ${ev.type === "Warning" ? "text-yellow-400" : "text-blue-400"}`}
                        >
                          {ev.reason}
                        </span>
                        {ev.count > 1 && (
                          <span className="text-[9px] bg-white/5 text-gray-500 px-1.5 rounded-full">
                            ×{ev.count}
                          </span>
                        )}
                        <span className="text-[9px] text-gray-600 ml-auto shrink-0">
                          {relativeTime(ev.timestamp)}
                        </span>
                      </div>
                      <p className="text-[10px] text-gray-400 leading-relaxed truncate">
                        {ev.message}
                      </p>
                    </div>
                  </div>
                ))
            )}
          </div>
        )}
      </div>

      {/* ── Live Log Stream ── */}
      <div className="flex-1 flex flex-col bg-white/[0.02] border border-white/5 rounded-2xl overflow-hidden min-h-[260px]">
        <div className="flex items-center justify-between px-5 py-3.5 border-b border-white/5">
          <div className="flex items-center gap-2 text-xs font-black uppercase tracking-widest text-white">
            <Terminal size={14} className="text-cyan-400" />
            Live Pod Logs
            {isStreaming && (
              <span className="flex items-center gap-1 text-[9px] text-green-400 font-black bg-green-500/10 border border-green-500/20 px-2 py-0.5 rounded-full animate-pulse">
                <span className="w-1.5 h-1.5 rounded-full bg-green-500" />
                LIVE
              </span>
            )}
          </div>

          <div className="flex items-center gap-2">
            {isStreaming && (
              <button
                onClick={() => setLogs([])}
                className="text-[10px] text-gray-500 hover:text-white font-mono border border-white/10 px-2 py-1 rounded-lg transition-all"
              >
                Clear
              </button>
            )}
            <button
              onClick={toggleStream}
              className={`flex items-center gap-1.5 text-[10px] font-black uppercase tracking-widest px-3 py-1.5 rounded-xl border transition-all ${
                isStreaming
                  ? "bg-red-500/10 text-red-400 border-red-500/30 hover:bg-red-500/20"
                  : "bg-green-500/10 text-green-400 border-green-500/30 hover:bg-green-500/20"
              }`}
            >
              {isStreaming ? (
                <>
                  <Square size={10} /> Stop
                </>
              ) : (
                <>
                  <PlayCircle size={10} /> Start Stream
                </>
              )}
            </button>
          </div>
        </div>

        <div className="flex-1 bg-black/40 p-4 overflow-y-auto font-mono text-[11px] custom-scroll">
          {logs.length === 0 ? (
            <div className="h-full flex flex-col items-center justify-center gap-2 text-gray-700">
              <Cpu size={28} />
              <p className="text-xs">
                {isStreaming
                  ? "Awaiting log events from pod..."
                  : "Click 'Start Stream' to tail live pod logs via kubectl"}
              </p>
            </div>
          ) : (
            logs.map((entry, idx) => (
              <div
                key={idx}
                className="flex gap-3 mb-0.5 hover:bg-white/[0.02] px-1 rounded"
              >
                <span className="text-gray-600 shrink-0 select-none w-[60px]">
                  {entry.ts}
                </span>
                <span className={`${logLineColor(entry.line)} break-all`}>
                  {entry.line}
                </span>
              </div>
            ))
          )}
          <div ref={logsEndRef} />
        </div>
      </div>

      {/* ── Deployment info ── */}
      {status?.deployment && Object.keys(status.deployment).length > 0 && (
        <div className="bg-white/[0.02] border border-white/5 rounded-2xl p-4">
          <p className="text-[9px] font-black uppercase tracking-widest text-gray-600 mb-3 flex items-center gap-2">
            <Container size={11} />
            Deployment Info
          </p>
          <div className="grid grid-cols-2 md:grid-cols-3 gap-2">
            {Object.entries(status.deployment).map(([k, v]) => (
              <div
                key={k}
                className="bg-black/20 rounded-xl p-2.5 border border-white/5"
              >
                <p className="text-[9px] font-bold text-gray-600 uppercase mb-0.5">
                  {k.replace(/_/g, " ")}
                </p>
                <p className="text-[11px] text-white font-mono truncate">
                  {String(v)}
                </p>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
};
