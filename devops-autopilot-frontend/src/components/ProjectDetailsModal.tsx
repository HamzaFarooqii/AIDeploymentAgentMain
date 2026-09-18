import React, { useState } from "react";
import { Card } from "./Card";
import { Button } from "./Button";
import { Badge, BadgeVariant } from "./Badge";
import { apiClient } from "../api/client";
import { Project } from "../types/api";
import AWSDeployPanel from "./AWSDeployPanel";
import { 
  FileCode, 
  Terminal, 
  Layers, 
  Database, 
  Cpu, 
  Activity, 
  Clock, 
  Folder, 
  ExternalLink,
  ChevronRight,
  ShieldCheck,
  Box,
  Container
} from "lucide-react";

interface ProjectDetailsModalProps {
  project: Project;
  isOpen: boolean;
  onClose: () => void;
}

export const ProjectDetailsModal: React.FC<ProjectDetailsModalProps> = ({
  project,
  isOpen,
  onClose,
}) => {
  const [activeTab, setActiveTab ] = useState<string>("overview");
  const [exporting, setExporting] = useState(false);

  if (!isOpen) return null;

  const handleExport = async (format: "json" | "yaml") => {
    try {
      setExporting(true);
      const resp = await apiClient.exportMetadata(project._id, format);
      if (resp && resp.success) {
        const blob = new Blob([JSON.stringify(resp.data, null, 2)], { type: "application/json" });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = `${project.project_name}-metadata.${format}`;
        a.click();
        URL.revokeObjectURL(url);
      }
    } catch (err) {
      console.error("Export failed:", err);
    } finally {
      setExporting(false);
    }
  };

  const statusVariant: BadgeVariant =
    project.status === "uploaded" ? "info" :
    (project.status === "extracting" || project.status === "analyzing") ? "warning" :
    (project.status === "analyzed" || project.status === "completed") ? "success" :
    project.status === "failed" ? "error" : "default";

  return (
    <div className="fixed inset-0 bg-black/80 backdrop-blur-xl z-[70] flex items-center justify-center p-2 sm:p-6 lg:p-10">
      <Card className="w-full max-w-[98vw] h-[95vh] flex flex-col overflow-hidden border-white/10 shadow-2xl animate-scale-in bg-[#0d1117]/95">
        <div className="p-6 md:p-12 flex flex-col h-full overflow-hidden">
          
          {/* Header */}
          <div className="flex justify-between items-start mb-10 shrink-0">
            <div>
              <div className="flex items-center gap-3 mb-4">
                <Badge variant={statusVariant} size="md">{project.status.toUpperCase()}</Badge>
                <div className="h-4 w-[1px] bg-white/10 mx-1"></div>
                <span className="text-gray-500 font-mono text-xs tracking-widest">{project._id}</span>
              </div>
              <h2 className="text-4xl md:text-6xl font-black text-white mb-2 tracking-tighter uppercase">{project.project_name}</h2>
              <div className="flex items-center gap-4 text-gray-500 text-sm font-medium">
                <div className="flex items-center gap-1.5">
                  <FileCode size={14} className="text-cyan-400" />
                  {project.file_name}
                </div>
                <div className="flex items-center gap-1.5">
                   <Clock size={14} />
                   {new Date(project.upload_date).toLocaleDateString()}
                </div>
              </div>
            </div>
            <button 
              onClick={onClose} 
              className="w-14 h-14 rounded-full bg-white/5 border border-white/10 flex items-center justify-center text-gray-400 hover:text-white hover:bg-white/10 hover:rotate-90 transition-all text-3xl"
            >
              ×
            </button>
          </div>

          {/* Navigation Bar - Modernized */}
          <div className="flex gap-2 sm:gap-10 mb-10 border-b border-white/5 shrink-0 overflow-x-auto custom-scroll no-scrollbar">
            {[
              { id: 'overview', label: 'Vitals', icon: <Activity size={16} /> },
              { id: 'discovery', label: 'Tech Stack', icon: <Layers size={16} /> },
              { id: 'logs', label: 'Deep Logs', icon: <Terminal size={16} /> },
              { id: 'deploy', label: 'Deploy Panel', icon: <ChevronRight size={16} /> }
            ].map((t) => (
              <button
                key={t.id}
                onClick={() => setActiveTab(t.id)}
                className={`pb-5 px-1 font-black text-sm transition-all relative flex items-center gap-2.5 whitespace-nowrap tracking-wider uppercase ${
                  activeTab === t.id ? "text-cyan-400 border-b-2 border-cyan-400" : "text-gray-500 hover:text-gray-300"
                }`}
              >
                {t.icon}
                {t.label}
              </button>
            ))}
          </div>

          {/* Scrollable Content */}
          <div className="flex-1 overflow-y-auto pr-4 custom-scroll">
            {activeTab === 'overview' && (
              <div className="grid grid-cols-1 lg:grid-cols-4 gap-8 pb-12">
                <div className="lg:col-span-3 space-y-8">
                  {/* Metric Tiles */}
                  <div className="grid grid-cols-1 sm:grid-cols-3 gap-6">
                    <div className="bg-white/5 p-8 rounded-[2rem] border border-white/5 relative overflow-hidden">
                       <p className="text-gray-500 text-[10px] font-black uppercase tracking-[0.2em] mb-3">Memory Footprint</p>
                       <p className="text-4xl font-black text-white">{(project.file_size / (1024 * 1024)).toFixed(2)} MB</p>
                       <Box className="absolute top-8 right-8 text-white/5" size={48} />
                    </div>
                    <div className="bg-white/5 p-8 rounded-[2rem] border border-white/5 relative overflow-hidden">
                       <p className="text-gray-500 text-[10px] font-black uppercase tracking-[0.2em] mb-3">Asset Count</p>
                       <p className="text-4xl font-black text-white">{project.files_count.toLocaleString()}</p>
                       <FileCode className="absolute top-8 right-8 text-white/5" size={48} />
                    </div>
                    <div className="bg-white/5 p-8 rounded-[2rem] border border-white/5 relative overflow-hidden">
                       <p className="text-gray-500 text-[10px] font-black uppercase tracking-[0.2em] mb-3">Folder Depth</p>
                       <p className="text-4xl font-black text-white">{project.folders_count}</p>
                       <Folder className="absolute top-8 right-8 text-white/5" size={48} />
                    </div>
                  </div>

                  {/* Sessions Group */}
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-6">
                    {project.extraction_date && (
                      <div className="p-8 bg-cyan-500/[0.03] border border-cyan-500/10 rounded-3xl flex items-center gap-6">
                        <div className="w-14 h-14 rounded-2xl bg-cyan-500/10 flex items-center justify-center text-cyan-400 shadow-[0_0_20px_rgba(34,211,238,0.1)]">
                          <Activity size={24} />
                        </div>
                        <div>
                          <p className="text-cyan-400/60 font-black text-[10px] uppercase tracking-widest mb-1">Source Extraction</p>
                          <p className="text-gray-300 font-bold">{new Date(project.extraction_date).toLocaleString()}</p>
                        </div>
                      </div>
                    )}
                    {project.analysis_date && (
                      <div className="p-8 bg-emerald-500/[0.03] border border-emerald-500/10 rounded-3xl flex items-center gap-6">
                        <div className="w-14 h-14 rounded-2xl bg-emerald-500/10 flex items-center justify-center text-emerald-400 shadow-[0_0_20px_rgba(16,185,129,0.1)]">
                          <ShieldCheck size={24} />
                        </div>
                        <div>
                          <p className="text-emerald-400/60 font-black text-[10px] uppercase tracking-widest mb-1">AI Logic Pass</p>
                          <p className="text-gray-300 font-bold">{new Date(project.analysis_date).toLocaleString()}</p>
                        </div>
                      </div>
                    )}
                  </div>
                </div>

                {/* Sidebar Summary */}
                <div className="lg:col-span-1">
                   <div className="bg-white/[0.02] border border-white/5 rounded-[2.5rem] p-10 h-full">
                      <h4 className="text-white font-black text-xl mb-8 flex items-center gap-3 italic">
                         <Activity size={20} className="text-cyan-400" />
                         VITALS
                      </h4>
                      <div className="space-y-8">
                         {[
                           { label: 'Language', val: project.metadata?.language || 'Identifying', icon: <Cpu size={14} /> },
                           { label: 'Framework', val: project.metadata?.framework || 'Identifying', icon: <Layers size={14} /> },
                           { label: 'Exposed Port', val: project.metadata?.backend_port || project.metadata?.port || 'Automated', icon: <ExternalLink size={14} /> },
                           { label: 'DB Logic', val: project.metadata?.database || 'None', icon: <Database size={14} /> }
                         ].map((item, i) => (
                           <div key={i} className="flex justify-between items-center group">
                              <span className="text-gray-500 text-xs font-bold uppercase tracking-widest flex items-center gap-2 group-hover:text-gray-300 transition-colors">
                                {item.icon} {item.label}
                              </span>
                              <span className="text-white font-black text-sm">{item.val}</span>
                           </div>
                         ))}
                      </div>
                      
                      <div className="mt-14 pt-8 border-t border-white/5">
                         <div className="p-5 bg-cyan-400/5 rounded-2xl border border-cyan-400/10">
                            <p className="text-[10px] font-black text-cyan-400 uppercase tracking-widest mb-2">AUTOPILOT INSIGHT</p>
                            <p className="text-xs text-gray-400 leading-relaxed font-medium">
                               This project is candidate for high-speed deployment. All core components successfully identified and mapped to infrastructure.
                            </p>
                         </div>
                      </div>
                   </div>
                </div>
              </div>
            )}

            {activeTab === 'discovery' && (
              <div className="space-y-10 animate-fade-in pb-20">
                {project.metadata ? (
                  <>
                    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
                       {[
                         { l: 'Environment', v: project.metadata.language, i: <Cpu /> },
                         { l: 'Architecture', v: project.metadata.framework, i: <Layers /> },
                         { l: 'Gateway', v: project.metadata.backend_port || project.metadata.port || '80', i: <ExternalLink /> },
                         { l: 'Persistence', v: project.metadata.database || 'Filesystem', i: <Database /> }
                       ].map((x, i) => (
                         <div key={i} className="bg-white/5 p-8 rounded-3xl border border-white/5 flex flex-col justify-between">
                            <div className="text-gray-500 text-[10px] font-black uppercase tracking-widest mb-4">{x.l}</div>
                            <div className="text-white font-black text-2xl">{x.v}</div>
                         </div>
                       ))}
                    </div>

                    <div className="grid grid-cols-1 lg:grid-cols-2 gap-10">
                       <div className="bg-white/5 p-10 rounded-[3rem] border border-white/5">
                          <h4 className="text-white font-black mb-8 uppercase tracking-widest text-sm flex items-center gap-3">
                             <Box size={18} className="text-cyan-400" />
                             Dependency Map
                          </h4>
                          <div className="flex flex-wrap gap-2 max-h-[400px] overflow-y-auto custom-scroll pr-4">
                             {project.metadata.dependencies?.length > 0 ? project.metadata.dependencies.map((d, i) => (
                               <Badge key={`${d}-${i}`} variant="default" size="sm">{d}</Badge>
                             )) : <span className="text-gray-600 italic">Universal compatibility - no specific packages detected.</span>}
                          </div>
                       </div>
                       
                       <div className="bg-white/5 p-10 rounded-[3rem] border border-white/5">
                          <h4 className="text-white font-black mb-8 uppercase tracking-widest text-sm flex items-center gap-3">
                             <FileCode size={18} className="text-emerald-400" />
                             Core File Analysis
                          </h4>
                          <div className="space-y-3 max-h-[400px] overflow-y-auto custom-scroll pr-4">
                             {project.metadata.detected_files?.length > 0 ? project.metadata.detected_files.map((file, i) => (
                               <div key={i} className="flex items-center gap-4 p-4 bg-white/[0.03] rounded-2xl border border-white/5 group hover:bg-white/[0.05] transition-all">
                                  <span className="text-cyan-400 font-black text-[10px] opacity-40">{String(i+1).padStart(2, '0')}</span>
                                  <span className="text-gray-400 font-mono text-xs truncate group-hover:text-white transition-colors">{file}</span>
                               </div>
                             )) : <span className="text-gray-600 italic">No entry files mapping required.</span>}
                          </div>
                       </div>
                    </div>
                  </>
                ) : <div className="text-center py-32 text-gray-600 border-2 border-dashed border-white/5 rounded-[4rem]">Logic pass scheduled for this project. Discovery will initiate shortly.</div>}
              </div>
            )}

            {activeTab === 'logs' && (
              <div className="bg-[#050810] p-10 rounded-[3rem] font-mono text-xs border border-white/5 animate-fade-in shadow-2xl relative overflow-hidden h-full min-h-[500px]">
                <div className="flex items-center justify-between mb-8 pb-6 border-b border-white/5">
                   <div className="flex items-center gap-3">
                      <Terminal size={18} className="text-cyan-400" />
                      <h4 className="text-white font-black tracking-widest uppercase opacity-70">SYSTEM_STDOUT / EVENT_PIPE</h4>
                   </div>
                   <div className="flex items-center gap-2">
                      <div className="w-2 h-2 rounded-full bg-cyan-400 animate-pulse"></div>
                      <span className="text-[10px] font-black text-cyan-400 uppercase tracking-widest">Live Sync Active</span>
                   </div>
                </div>
                {project.logs?.length > 0 ? (
                  <div className="space-y-4 max-h-[60vh] overflow-y-auto custom-scroll pr-6 pb-10">
                    {project.logs.map((l, i) => (
                      <div key={i} className="flex gap-8 py-2 border-b border-white/[0.02] last:border-0 group">
                        <span className="text-gray-700 shrink-0 font-black tracking-tighter w-24">{new Date(l.timestamp).toLocaleTimeString([], { hour12: false })}</span>
                        <span className="text-gray-500 group-hover:text-cyan-100 transition-colors leading-relaxed selection:bg-cyan-500/30 selection:text-white font-medium">
                          {l.message}
                        </span>
                      </div>
                    ))}
                  </div>
                ) : <div className="py-40 text-center text-gray-600 uppercase tracking-[0.3em] font-black opacity-20 text-xl">Event stream empty</div>}
                
                {/* Decorative Terminal Endings */}
                <div className="absolute bottom-6 right-10 text-white/5 font-black text-6xl pointer-events-none">_EOF</div>
              </div>
            )}

            {activeTab === 'deploy' && (
              <div className="animate-fade-in pb-12">
                 <AWSDeployPanel projectId={project._id} />
              </div>
            )}
          </div>

          {/* Persistent Footer */}
          <div className="mt-10 pt-8 border-t border-white/5 flex justify-between items-center shrink-0">
            <div className="flex gap-5">
              {(project.status === "analyzed" || project.status === "completed") && (
                <>
                  <button 
                    onClick={() => handleExport('json')} 
                    disabled={exporting}
                    className="flex items-center gap-2 text-[10px] font-black text-gray-500 hover:text-cyan-400 uppercase tracking-widest transition-colors px-4 py-2 bg-white/5 rounded-xl border border-white/5"
                  >
                    {exporting ? 'EXPORTING...' : <><Box size={14} /> EXPORT_JSON</>}
                  </button>
                  <button 
                    onClick={() => handleExport('yaml')} 
                    disabled={exporting}
                    className="flex items-center gap-2 text-[10px] font-black text-gray-500 hover:text-emerald-400 uppercase tracking-widest transition-colors px-4 py-2 bg-white/5 rounded-xl border border-white/5"
                  >
                    {exporting ? 'EXPORTING...' : <><Container size={14} /> EXPORT_YAML</>}
                  </button>
                </>
              )}
            </div>
            <Button variant="primary" onClick={onClose} className="px-16 h-14 text-xs font-black tracking-[0.3em] uppercase bg-white text-black hover:bg-gray-200">
               TERMINATE SESSION
            </Button>
          </div>

        </div>
      </Card>
    </div>
  );
};
