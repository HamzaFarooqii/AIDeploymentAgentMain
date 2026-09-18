import React, { useState, useEffect, useCallback, useRef } from "react";
import { Card } from "./Card";
import { Button } from "./Button";
import { Badge } from "./Badge";
import { Alert } from "./Alert";
import { apiClient } from "../api/client";
import { Project, ProjectMetadata } from "../types/api";

interface AnalyzeProjectModalProps {
  project: Project;
  isOpen: boolean;
  onClose: () => void;
  onSuccess: () => void;
}

interface LogEntry {
  message: string;
  timestamp: string;
  type: "info" | "success" | "warning" | "error";
}

export const AnalyzeProjectModal: React.FC<AnalyzeProjectModalProps> = ({
  project,
  isOpen,
  onClose,
  onSuccess,
}) => {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);
  const [useMl, setUseMl] = useState(true);
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [analysisResults, setAnalysisResults] =
    useState<ProjectMetadata | null>(null);
  const [pollingInterval, setPollingInterval] = useState<ReturnType<
    typeof setInterval
  > | null>(null);
  const logsEndRef = useRef<HTMLDivElement>(null);

  const scrollToBottom = () => {
    logsEndRef.current?.scrollIntoView({ behavior: "smooth" });
  };

  useEffect(() => {
    scrollToBottom();
  }, [logs]);

  const addLog = useCallback(
    (
      message: string,
      type: "info" | "success" | "warning" | "error" = "info"
    ) => {
      const timestamp = new Date().toLocaleTimeString();
      setLogs((prev) => [...prev, { message, timestamp, type }]);
    },
    []
  );

  const pollAnalysisStatus = useCallback(async () => {
    try {
      const response = await apiClient.getAnalysisResults(project._id);
      if (response.success) {
        if (response.metadata) {
          setAnalysisResults(response.metadata);
        }
        if (response.data?.status === "analyzed" || response.metadata) {
          addLog("Analysis completed successfully!", "success");
          setSuccess(true);
          setLoading(false);
          if (pollingInterval) clearInterval(pollingInterval);
          setTimeout(onSuccess, 2000);
        }
      }
    } catch {
      // Continue polling
    }
  }, [project._id, pollingInterval, onSuccess, addLog]);

  useEffect(() => {
    if (loading && !pollingInterval) {
      const interval = setInterval(pollAnalysisStatus, 2000);
      setPollingInterval(interval);
      return () => clearInterval(interval);
    }
  }, [loading, pollingInterval, pollAnalysisStatus]);

  const handleAnalyze = async () => {
    setError(null);
    setSuccess(false);
    setLogs([]);
    setAnalysisResults(null);
    setLoading(true);

    addLog(
      `Starting analysis with ML: ${useMl ? "Enabled" : "Disabled"}`,
      "info"
    );

    try {
      addLog("Analyzing project structure...", "info");
      const response = await apiClient.analyzeProject(
        project._id,
        useMl,
        false
      );

      if (response.success) {
        addLog("Framework detection in progress...", "info");

        // Start polling for analysis status
        const pollInterval = setInterval(async () => {
          try {
            const statusResponse = await apiClient.getAnalysisResults(
              project._id
            );
            if (statusResponse.success) {
              if (statusResponse.metadata) {
                setAnalysisResults(statusResponse.metadata);
              }
              if (
                statusResponse.data?.status === "analyzed" ||
                statusResponse.metadata
              ) {
                clearInterval(pollInterval);
                addLog("✅ Analysis completed successfully!", "success");
                setSuccess(true);
                setLoading(false);
                onSuccess();
              }
            }
          } catch {
            // Continue polling
          }
        }, 2000);

        setPollingInterval(pollInterval);
      } else {
        const message =
          response.message ||
          "Analysis could not be started";
        setError(message);
        addLog(message, "warning");
        setLoading(false);
      }
    } catch (err) {
      const errorMsg = err instanceof Error ? err.message : "Analysis failed";
      setError(errorMsg);
      addLog(`❌ ${errorMsg}`, "error");
      setLoading(false);
    }
  };

  const handleExport = async (format: "json" | "yaml") => {
    try {
      const response = await apiClient.exportMetadata(project._id, format);
      if (response.success) {
        const dataStr = JSON.stringify(response.data, null, 2);
        const dataBlob = new Blob([dataStr], { type: "application/json" });
        const url = URL.createObjectURL(dataBlob);
        const link = document.createElement("a");
        link.href = url;
        link.download = `${project.project_name}-metadata.${format}`;
        link.click();
        URL.revokeObjectURL(url);
        addLog(`Exported metadata as ${format.toUpperCase()}`, "success");
      }
    } catch (err) {
      const errorMsg = err instanceof Error ? err.message : "Export failed";
      setError(errorMsg);
      addLog(`Failed to export: ${errorMsg}`, "error");
    }
  };

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 bg-black/50 backdrop-blur-sm z-50 flex items-center justify-center p-4">
      <Card className="w-full max-w-4xl max-h-[90vh] overflow-y-auto">
        <div className="p-6">
          {/* Header */}
          <div className="flex items-center justify-between mb-6">
            <div>
              <h2 className="text-2xl font-bold text-white mb-1">
                Analyze Project
              </h2>
              <p className="text-gray-400 text-sm">{project.project_name}</p>
            </div>
            <button
              onClick={onClose}
              className="text-gray-400 hover:text-white transition text-2xl leading-none"
            >
              ×
            </button>
          </div>

          {/* Project Info */}
          <div className="bg-gray-900/50 rounded-lg p-4 mb-6 border border-gray-700">
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              <div>
                <p className="text-gray-500 text-xs mb-1">Status</p>
                <p className="text-white font-medium capitalize">
                  {project.status}
                </p>
              </div>
              <div>
                <p className="text-gray-500 text-xs mb-1">Files</p>
                <p className="text-white font-medium">{project.files_count}</p>
              </div>
              <div>
                <p className="text-gray-500 text-xs mb-1">Folders</p>
                <p className="text-white font-medium">
                  {project.folders_count}
                </p>
              </div>
              <div>
                <p className="text-gray-500 text-xs mb-1">Extracted Date</p>
                <p className="text-white font-medium">
                  {project.extraction_date
                    ? new Date(project.extraction_date).toLocaleDateString()
                    : "N/A"}
                </p>
              </div>
            </div>
          </div>

          {/* Error Alert */}
          {error && (
            <div className="mb-6">
              <Alert
                type="error"
                title="Error"
                message={error}
                onClose={() => setError(null)}
              />
            </div>
          )}

          {/* Settings */}
          {!loading && !success && (
            <div className="mb-6 bg-gray-900/50 rounded-lg p-4 border border-gray-700">
              <label className="flex items-center gap-3 cursor-pointer">
                <input
                  type="checkbox"
                  checked={useMl}
                  onChange={(e) => setUseMl(e.target.checked)}
                  className="w-5 h-5 rounded border-gray-600 text-cyan-500 focus:ring-cyan-500"
                />
                <div>
                  <p className="text-white font-medium">
                    Use ML-Based Detection
                  </p>
                  <p className="text-gray-400 text-sm">
                    Enable CodeBERT for more accurate framework detection
                  </p>
                </div>
              </label>
            </div>
          )}

          {/* Logs */}
          <div className="mb-6">
            <h3 className="text-lg font-bold text-white mb-3">Analysis Logs</h3>
            <div className="bg-gray-900/80 rounded-lg border border-gray-700 max-h-64 overflow-y-auto font-mono text-sm">
              {logs.length === 0 ? (
                <div className="p-4 text-gray-500 text-center">
                  Logs will appear here during analysis...
                </div>
              ) : (
                <div className="divide-y divide-gray-700">
                  {logs.map((log, idx) => (
                    <div
                      key={idx}
                      className={`p-3 flex items-start gap-3 ${
                        log.type === "success"
                          ? "bg-green-500/5"
                          : log.type === "error"
                            ? "bg-red-500/5"
                            : log.type === "warning"
                              ? "bg-yellow-500/5"
                              : "hover:bg-gray-800/30"
                      }`}
                    >
                      <span className="text-gray-500 flex-shrink-0 min-w-fit">
                        {log.timestamp}
                      </span>
                      <span
                        className={`${
                          log.type === "success"
                            ? "text-green-400"
                            : log.type === "error"
                              ? "text-red-400"
                              : log.type === "warning"
                                ? "text-yellow-400"
                                : "text-cyan-400"
                        }`}
                      >
                        {log.message}
                      </span>
                    </div>
                  ))}
                  <div ref={logsEndRef} />
                </div>
              )}
            </div>
          </div>

          {/* Results */}
          {success && analysisResults && (
            <div className="mb-6">
              <h3 className="text-lg font-bold text-white mb-4">
                Analysis Results
              </h3>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-6">
                <div className="bg-gradient-to-br from-cyan-500/10 to-cyan-600/5 border border-cyan-500/20 rounded-lg p-4">
                  <p className="text-cyan-400 text-sm mb-2">Framework</p>
                  <p className="text-2xl font-bold text-white">
                    {analysisResults.framework}
                  </p>
                </div>

                <div className="bg-gradient-to-br from-green-500/10 to-green-600/5 border border-green-500/20 rounded-lg p-4">
                  <p className="text-green-400 text-sm mb-2">Language</p>
                  <p className="text-2xl font-bold text-white">
                    {analysisResults.language}
                  </p>
                </div>

                <div className="bg-gradient-to-br from-purple-500/10 to-purple-600/5 border border-purple-500/20 rounded-lg p-4">
                  <p className="text-purple-400 text-sm mb-2">Runtime</p>
                  <p className="text-lg font-bold text-white">
                    {analysisResults.runtime || "N/A"}
                  </p>
                </div>

                <div className="bg-gradient-to-br from-blue-500/10 to-blue-600/5 border border-blue-500/20 rounded-lg p-4">
                  <p className="text-blue-400 text-sm mb-2">Dependencies</p>
                  <p className="text-2xl font-bold text-white">
                    {analysisResults.dependencies.length}
                  </p>
                </div>
              </div>

              {analysisResults.databases &&
                analysisResults.databases.length > 0 && (
                  <div className="bg-gray-900/50 rounded-lg p-4 border border-gray-700 mt-4">
                    <p className="text-gray-400 text-sm mb-3">
                      Detected Databases
                    </p>
                    <div className="flex flex-wrap gap-2">
                      {analysisResults.databases.map((db, idx) => (
                        <Badge key={idx} variant="info" size="sm">
                          {db}
                        </Badge>
                      ))}
                    </div>
                  </div>
                )}

              <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-6">
                <div className="bg-gray-900/50 rounded-lg p-4 border border-gray-700">
                  <p className="text-gray-400 text-sm mb-2">Backend Port</p>
                  <p className="text-lg font-bold text-white">
                    {analysisResults.backend_port ??
                      analysisResults.port ??
                      "N/A"}
                  </p>
                </div>

                <div className="bg-gray-900/50 rounded-lg p-4 border border-gray-700">
                  <p className="text-gray-400 text-sm mb-2">Frontend Port</p>
                  <p className="text-lg font-bold text-white">
                    {analysisResults.frontend_port ?? "N/A"}
                  </p>
                </div>

                <div className="bg-gray-900/50 rounded-lg p-4 border border-gray-700">
                  <p className="text-gray-400 text-sm mb-2">Database</p>
                  <p className="text-lg font-bold text-white">
                    {analysisResults.database || "Unknown"}
                  </p>
                </div>
              </div>

              {/* Docker Info */}
              <div className="grid grid-cols-2 gap-4 mb-6">
                <div className="bg-gray-900/50 rounded-lg p-4 border border-gray-700">
                  <p className="text-gray-400 text-sm mb-2">Dockerfile</p>
                  <p className="text-lg font-bold">
                    {analysisResults.dockerfile ? (
                      <span className="text-green-400">✅ Yes</span>
                    ) : (
                      <span className="text-gray-400">❌ No</span>
                    )}
                  </p>
                </div>

                <div className="bg-gray-900/50 rounded-lg p-4 border border-gray-700">
                  <p className="text-gray-400 text-sm mb-2">Docker Compose</p>
                  <p className="text-lg font-bold">
                    {analysisResults.docker_compose ? (
                      <span className="text-green-400">✅ Yes</span>
                    ) : (
                      <span className="text-gray-400">❌ No</span>
                    )}
                  </p>
                </div>
              </div>

              {/* ML Confidence (with fallback to detection_confidence) */}
              {(() => {
                const mlConfidence =
                  analysisResults.ml_confidence ||
                  analysisResults.detection_confidence;

                if (!mlConfidence) return null;

                return (
                  <div className="bg-yellow-500/10 border border-yellow-500/20 rounded-lg p-4 mb-6">
                    <p className="text-yellow-400 font-medium mb-3">
                      ML / Detection Confidence Scores
                    </p>
                    <div className="space-y-2">
                      <div>
                        <div className="flex justify-between mb-1">
                          <span className="text-gray-300 text-sm">
                            Language
                          </span>
                          <span className="text-white font-bold">
                            {Math.round(mlConfidence.language * 100)}%
                          </span>
                        </div>
                        <div className="w-full h-2 bg-gray-700 rounded-full overflow-hidden">
                          <div
                            className="h-full bg-gradient-to-r from-cyan-500 to-blue-600"
                            style={{
                              width: `${mlConfidence.language * 100}%`,
                            }}
                          />
                        </div>
                      </div>

                      <div>
                        <div className="flex justify-between mb-1">
                          <span className="text-gray-300 text-sm">
                            Framework
                          </span>
                          <span className="text-white font-bold">
                            {Math.round(mlConfidence.framework * 100)}%
                          </span>
                        </div>
                        <div className="w-full h-2 bg-gray-700 rounded-full overflow-hidden">
                          <div
                            className="h-full bg-gradient-to-r from-cyan-500 to-blue-600"
                            style={{
                              width: `${mlConfidence.framework * 100}%`,
                            }}
                          />
                        </div>
                      </div>
                    </div>
                  </div>
                );
              })()}

              {/* Commands */}
              {analysisResults.build_command && (
                <div className="bg-gray-900/50 rounded-lg p-4 border border-gray-700 mb-4">
                  <p className="text-gray-400 text-sm mb-2">Build Command</p>
                  <code className="text-cyan-400 text-sm break-all">
                    {analysisResults.build_command}
                  </code>
                </div>
              )}

              {analysisResults.start_command && (
                <div className="bg-gray-900/50 rounded-lg p-4 border border-gray-700 mb-4">
                  <p className="text-gray-400 text-sm mb-2">Start Command</p>
                  <code className="text-cyan-400 text-sm break-all">
                    {analysisResults.start_command}
                  </code>
                </div>
              )}

              {/* Dependencies */}
              {analysisResults.dependencies.length > 0 && (
                <div className="bg-gray-900/50 rounded-lg p-4 border border-gray-700">
                  <p className="text-gray-400 text-sm mb-3">Dependencies</p>
                  <div className="flex flex-wrap gap-2">
                    {analysisResults.dependencies
                      .slice(0, 10)
                      .map((dep, idx) => (
                        <Badge key={idx} variant="info" size="sm">
                          {dep}
                        </Badge>
                      ))}
                    {analysisResults.dependencies.length > 10 && (
                      <Badge variant="default" size="sm">
                        +{analysisResults.dependencies.length - 10} more
                      </Badge>
                    )}
                  </div>
                </div>
              )}
            </div>
          )}

          {/* Actions */}
          <div className="flex items-center gap-3 pt-6 border-t border-gray-700 flex-wrap">
            {!loading && !success && (
              <Button variant="primary" onClick={handleAnalyze}>
                <svg
                  className="w-5 h-5"
                  fill="none"
                  stroke="currentColor"
                  viewBox="0 0 24 24"
                >
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    strokeWidth={2}
                    d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z"
                  />
                </svg>
                Start Analysis
              </Button>
            )}

            {success && (
              <>
                <Button
                  variant="secondary"
                  onClick={() => handleExport("json")}
                  className="flex items-center gap-2"
                >
                  <svg
                    className="w-5 h-5"
                    fill="none"
                    stroke="currentColor"
                    viewBox="0 0 24 24"
                  >
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      strokeWidth={2}
                      d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4"
                    />
                  </svg>
                  Export JSON
                </Button>

                <Button
                  variant="secondary"
                  onClick={() => handleExport("yaml")}
                  className="flex items-center gap-2"
                >
                  <svg
                    className="w-5 h-5"
                    fill="none"
                    stroke="currentColor"
                    viewBox="0 0 24 24"
                  >
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      strokeWidth={2}
                      d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4"
                    />
                  </svg>
                  Export YAML
                </Button>

                <Button
                  variant="secondary"
                  onClick={onClose}
                  className="ml-auto"
                >
                  Close
                </Button>
              </>
            )}
          </div>
        </div>
      </Card>
    </div>
  );
};
