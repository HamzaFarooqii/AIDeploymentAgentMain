import React, { useState, useEffect } from 'react';
import { Button } from './Button';
import { Alert } from './Alert';
import { ProgressBar } from './ProgressBar';
import { apiClient } from '../api/client';
import { Project } from '../types/api';
import {
  X, FolderOpen, File, Zap, FileText, CheckCircle
} from 'lucide-react';

interface ExtractProjectModalProps {
  project: Project;
  isOpen: boolean;
  onClose: () => void;
  onSuccess: () => void;
}

interface ExtractedFile {
  name: string;
  path: string;
  type: 'file' | 'folder';
  size?: number;
  extension?: string;
}

const PANEL = {
  bg:     'rgba(5,8,16,0.97)',
  border: 'rgba(255,255,255,0.08)',
  card:   'rgba(13,17,23,0.9)',
};

export const ExtractProjectModal: React.FC<ExtractProjectModalProps> = ({
  project, isOpen, onClose, onSuccess,
}) => {
  const [loading, setLoading] = useState(false);
  const [error, setError]     = useState<string | null>(null);
  const [success, setSuccess] = useState(false);
  const [extractedFiles, setExtractedFiles] = useState<ExtractedFile[]>([]);
  const [showFiles, setShowFiles] = useState(false);
  const [pollingInterval, setPollingInterval] = useState<ReturnType<
    typeof setInterval
  > | null>(null);
  const [progress, setProgress] = useState(0);

  useEffect(() => {
    if (loading && !pollingInterval) {
      let tick = 10;
      const interval = setInterval(async () => {
        tick = Math.min(tick + 8, 85);
        setProgress(tick);
        try {
          const r = await apiClient.getExtractionStatus(project._id);
          if (r.success && r.data.status === 'extracted') {
            clearInterval(interval);
            setProgress(100);
            setSuccess(true);
            setLoading(false);
            setTimeout(onSuccess, 2000);
          }
        } catch { /* keep polling */ }
      }, 2000);
      setPollingInterval(interval);
      return () => clearInterval(interval);
    }
    // Intentionally only re-runs when `loading` toggles. This effect is gated by
    // `pollingInterval` itself (`!pollingInterval`), so adding `pollingInterval`
    // as a dependency would re-fire the effect the instant it sets the interval,
    // clearing it before it ever ticks. `onSuccess`/`project._id` are read from
    // the latest render via closure and don't need to retrigger this setup.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loading]);

  const handleExtract = async () => {
    setError(null);
    setSuccess(false);
    setLoading(true);
    setProgress(10);
    try {
      const resp = await apiClient.extractProject(project._id);
      if (resp.success) {
        const pollInterval = setInterval(async () => {
          try {
            const status = await apiClient.getExtractionStatus(project._id);
            if (status.data.status === 'extracted') {
              clearInterval(pollInterval);
              setProgress(100);
              setSuccess(true);
              setLoading(false);
              const filesResp = await apiClient.getExtractedFiles(project._id);
              if (filesResp.success) setExtractedFiles(filesResp.files);
              onSuccess();
            }
          } catch { /* keep polling */ }
        }, 2000);
        setPollingInterval(pollInterval);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Extraction failed');
      setLoading(false);
    }
  };

  const fetchExtractedFiles = async () => {
    try {
      const resp = await apiClient.getExtractedFiles(project._id);
      if (resp.success) { setExtractedFiles(resp.files); setShowFiles(true); }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to fetch files');
    }
  };

  if (!isOpen) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ background: 'rgba(0,0,0,0.7)', backdropFilter: 'blur(8px)' }}
    >
      <div
        className="w-full max-w-2xl max-h-[90vh] flex flex-col rounded-2xl overflow-hidden animate-scale-in"
        style={{ background: PANEL.bg, border: `1px solid ${PANEL.border}`, boxShadow: '0 32px 80px rgba(0,0,0,0.8)' }}
      >
        {/* Header */}
        <div
          className="flex items-center justify-between px-6 py-4 flex-shrink-0"
          style={{ borderBottom: `1px solid ${PANEL.border}`, background: 'rgba(22,27,34,0.5)' }}
        >
          <div className="flex items-center gap-3">
            <div
              className="w-9 h-9 rounded-xl flex items-center justify-center"
              style={{ background: 'rgba(34,211,238,0.1)', border: '1px solid rgba(34,211,238,0.2)' }}
            >
              <Zap size={17} className="text-cyan-400" />
            </div>
            <div>
              <h2 className="text-base font-bold text-white">Extract & Analyze</h2>
              <p className="text-xs text-gray-500">{project.project_name}</p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="w-8 h-8 rounded-lg flex items-center justify-center text-gray-500 hover:text-white hover:bg-white/10 transition-all"
          >
            <X size={16} />
          </button>
        </div>

        {/* Body */}
        <div className="overflow-y-auto custom-scroll flex-1 p-6 space-y-4">
          {/* Project Info */}
          <div
            className="grid grid-cols-2 gap-3 p-4 rounded-xl"
            style={{ background: 'rgba(255,255,255,0.03)', border: `1px solid ${PANEL.border}` }}
          >
            {[
              { label: 'File name',  value: project.file_name },
              { label: 'File size',  value: `${(project.file_size / 1024 / 1024).toFixed(2)} MB` },
              { label: 'Status',     value: project.status },
              { label: 'Uploaded',   value: new Date(project.upload_date).toLocaleDateString() },
            ].map(item => (
              <div key={item.label}>
                <p className="text-[10px] font-semibold uppercase tracking-widest text-gray-600 mb-0.5">{item.label}</p>
                <p className="text-sm font-medium text-white capitalize">{item.value}</p>
              </div>
            ))}
          </div>

          {/* Error / Success alerts */}
          {error   && <Alert type="error"   title="Extraction failed"     message={error}  onClose={() => setError(null)} />}
          {success && <Alert type="success" title="Extraction complete"   message={`${project.files_count ?? ''} files extracted successfully`} />}

          {/* Loading progress */}
          {loading && (
            <div
              className="p-4 rounded-xl"
              style={{ background: 'rgba(34,211,238,0.06)', border: '1px solid rgba(34,211,238,0.15)' }}
            >
              <div className="flex items-center gap-3 mb-3">
                <div className="w-8 h-8 rounded-lg flex items-center justify-center flex-shrink-0"
                  style={{ background: 'rgba(34,211,238,0.1)' }}>
                  <div className="w-4 h-4 border-2 border-cyan-400 border-t-transparent rounded-full animate-spin" />
                </div>
                <div>
                  <p className="text-sm font-semibold text-white">Extracting project</p>
                  <p className="text-xs text-cyan-400/70">Unpacking archive and scanning structure…</p>
                </div>
              </div>
              <ProgressBar value={progress} showPercentage color="cyan" />
            </div>
          )}

          {/* Extracted file list */}
          {showFiles && extractedFiles.length > 0 && (
            <div>
              <p className="text-xs font-semibold uppercase tracking-widest text-gray-500 mb-3">
                Extracted files ({extractedFiles.length})
              </p>
              <div
                className="rounded-xl max-h-72 overflow-y-auto custom-scroll"
                style={{ background: 'rgba(5,8,16,0.8)', border: `1px solid ${PANEL.border}` }}
              >
                {extractedFiles.slice(0, 60).map((file, i) => (
                  <div
                    key={i}
                    className="flex items-center gap-3 px-4 py-2.5 hover:bg-white/[0.03] transition-colors"
                    style={{ borderBottom: i < extractedFiles.length - 1 ? `1px solid rgba(255,255,255,0.04)` : 'none' }}
                  >
                    {file.type === 'folder'
                      ? <FolderOpen size={14} className="text-amber-400 flex-shrink-0" />
                      : <File       size={14} className="text-blue-400  flex-shrink-0" />
                    }
                    <div className="flex-1 min-w-0">
                      <p className="text-xs text-white font-medium truncate">{file.name}</p>
                      <p className="text-[10px] text-gray-600 truncate font-mono">{file.path}</p>
                    </div>
                  </div>
                ))}
                {extractedFiles.length > 60 && (
                  <div className="px-4 py-3 text-center">
                    <p className="text-xs text-gray-600">+{extractedFiles.length - 60} more files</p>
                  </div>
                )}
              </div>
            </div>
          )}
        </div>

        {/* Footer */}
        <div
          className="flex items-center gap-3 px-6 py-4 flex-shrink-0"
          style={{ borderTop: `1px solid ${PANEL.border}`, background: 'rgba(22,27,34,0.4)' }}
        >
          {!loading && !success && (
            <Button variant="primary" onClick={handleExtract}>
              <Zap size={15} />
              Extract Project
            </Button>
          )}
          {success && (
            <>
              <Button variant="secondary" onClick={fetchExtractedFiles}>
                <FileText size={15} />
                View Files
              </Button>
              <Button variant="secondary" onClick={onClose}>
                <CheckCircle size={15} className="text-emerald-400" />
                Done
              </Button>
            </>
          )}
          <button
            onClick={onClose}
            className="ml-auto text-xs text-gray-600 hover:text-gray-400 transition-colors"
          >
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
};