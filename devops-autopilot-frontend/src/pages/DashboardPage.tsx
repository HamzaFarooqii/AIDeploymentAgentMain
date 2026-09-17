import React, { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Upload,
  Info,
  Play,
  Trash2,
  Container,
  Activity,
  Boxes,
  CheckCircle2,
  Rocket,
  HardDrive,
} from "lucide-react";

import { Navbar } from "../components/Navbar";
import { Card } from "../components/Card";
import { Button } from "../components/Button";
import { Badge } from "../components/Badge";
import { Alert } from "../components/Alert";
import { ExtractProjectModal } from "../components/ExtractProjectModal";
import { AnalyzeProjectModal } from "../components/AnalyzeProjectModal";
import { ProjectDetailsModal } from "../components/ProjectDetailsModal";
import { LoadingSpinner } from "../components/LoadingSpinner";
import { EmptyState } from "../components/EmptyState";
import { Stat } from "../components/Stat";
import { apiClient } from "../api/client";
import { Project } from "../types/api";
import ThreeBackground from "../components/ThreeBackground";

export const DashboardPage: React.FC = () => {
  const navigate = useNavigate();
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const [projects, setProjects] = useState<Project[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [uploading, setUploading] = useState(false);
  const [selectedFilter, setSelectedFilter] = useState<"all" | "analyzed" | "uploaded">("all");

  const [selectedProject, setSelectedProject] = useState<Project | null>(null);
  const [showExtractModal, setShowExtractModal] = useState(false);
  const [showAnalyzeModal, setShowAnalyzeModal] = useState(false);
  const [showDetailsModal, setShowDetailsModal] = useState(false);

  useEffect(() => {
    loadProjects();
    const interval = setInterval(loadProjects, 5000);
    return () => clearInterval(interval);
  }, []);

  const loadProjects = async () => {
    try {
      const response = await apiClient.getProjects();
      setProjects(response.projects);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load projects");
    } finally {
      setLoading(false);
    }
  };

  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    try {
      setUploading(true);
      setUploadProgress(10);
      
      const interval = setInterval(() => {
        setUploadProgress(prev => (prev < 90 ? prev + 5 : prev));
      }, 200);

      await apiClient.uploadProject(file, file.name.split(".")[0]);
      
      clearInterval(interval);
      setUploadProgress(100);
      
      setTimeout(() => {
        loadProjects();
        setUploading(false);
        setUploadProgress(0);
      }, 600);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Upload failed");
      setUploading(false);
      setUploadProgress(0);
    }
  };

  const handleDeleteProject = async (projectId: string) => {
    if (!confirm("Delete this project permanently?")) return;
    try {
      await apiClient.deleteProject(projectId);
      loadProjects();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to delete project");
    }
  };

  const handleExtractSuccess = () => {
    setShowExtractModal(false);
    loadProjects();
  };

  const handleAnalyzeSuccess = () => {
    setShowAnalyzeModal(false);
    loadProjects();
  };

  const getStatusColor = (status: string) => {
    switch (status) {
      case "uploaded": return "info";
      case "extracting":
      case "analyzing": return "warning";
      case "analyzed":
      case "completed": return "success";
      case "failed": return "error";
      default: return "default";
    }
  };

  const filteredProjects = projects.filter((p) => {
    if (selectedFilter === "all") return true;
    if (selectedFilter === "analyzed") return p.status === "analyzed" || p.status === "completed";
    if (selectedFilter === "uploaded") {
      // Include projects in transition
      return p.status === "uploaded" || p.status === "extracting" || p.status === "extracted" || p.status === "analyzing" || p.status === "failed";
    }
    return true;
  });

  const formatBytes = (bytes: number) => {
    if (bytes === 0) return "0 B";
    const k = 1024;
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + " " + ["B", "KB", "MB", "GB"][i];
  };

  if (loading && projects.length === 0) {
    return (
      <div className="flex items-center justify-center min-h-screen bg-[#050810]">
        <LoadingSpinner message="Loading workspace..." />
      </div>
    );
  }

  return (
    <div className="min-h-screen text-white relative flex flex-col">
      <ThreeBackground />
      <Navbar />

      <div className="flex-1 relative z-10 overflow-y-auto">
        <main className="max-w-[1600px] mx-auto px-6 py-12">
          
          {/* Hero Section */}
          <div className="text-center mb-16 animate-fade-in">
            <h1 className="text-5xl md:text-7xl font-black mb-6 tracking-tighter bg-gradient-to-b from-white to-gray-500 bg-clip-text text-transparent">
              DEV OPS CENTER
            </h1>
            <p className="text-gray-400 text-lg md:text-xl max-w-2xl mx-auto mb-10 leading-relaxed">
              Automate your cloud infrastructure with AI-driven analysis. 
              Upload, analyze, and deploy in seconds.
            </p>
            
            <div className="flex flex-col sm:flex-row justify-center gap-4">
              <Button
                size="lg"
                className="px-8 h-14 bg-white text-black hover:bg-gray-200 transition-all shadow-[0_0_30px_rgba(255,255,255,0.1)]"
                onClick={() => fileInputRef.current?.click()}
                disabled={uploading}
              >
                <Upload size={18} />
                Upload New Project
              </Button>
              <input
                type="file"
                ref={fileInputRef}
                onChange={handleFileUpload}
                accept=".zip,.tar,.gz,.tgz"
                className="hidden"
              />
            </div>
          </div>

          {/* Stats Bar */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-12 animate-fade-in" style={{ animationDelay: "0.1s" }}>
             <Stat
               label="Total Assets"
               value={projects.length}
               icon={<Boxes size={16} />}
             />
             <Stat
               label="Analyzed"
               value={projects.filter(p => p.status === 'analyzed').length}
               icon={<CheckCircle2 size={16} />}
               color="#22d3ee"
             />
             <Stat
               label="Deployment Ready"
               value={projects.filter(p => p.status === 'completed').length}
               icon={<Rocket size={16} />}
               color="#10b981"
             />
             <Stat
               label="Storage used"
               value={formatBytes(projects.reduce((a,b) => a + b.file_size, 0))}
               icon={<HardDrive size={16} />}
             />
          </div>

          {/* Progress Bar */}
          {uploading && (
            <div className="max-w-xl mx-auto mb-12 animate-fade-in">
              <div className="flex justify-between mb-2">
                <span className="text-sm font-bold text-cyan-400">UPLOADING CODEBASE...</span>
                <span className="text-sm font-bold">{uploadProgress}%</span>
              </div>
              <div className="h-1 bg-white/10 rounded-full overflow-hidden">
                <div 
                  className="h-full bg-cyan-500 transition-all duration-300" 
                  style={{ width: `${uploadProgress}%` }}
                />
              </div>
            </div>
          )}

          {/* Filter Navigation */}
          <div className="flex justify-center mb-10 border-b border-white/5">
            {[
              { id: "all", label: "All Projects" },
              { id: "analyzed", label: "Analyzed" },
              { id: "uploaded", label: "Uploaded & Processing" },
            ].map((tab) => (
              <button
                key={tab.id}
                onClick={() => setSelectedFilter(tab.id as any)}
                className={`px-8 py-5 text-sm font-black transition-all relative uppercase tracking-widest ${
                  selectedFilter === tab.id ? "text-white" : "text-gray-500 hover:text-gray-300"
                }`}
              >
                {tab.label}
                {selectedFilter === tab.id && (
                  <span className="absolute bottom-[-1px] left-0 w-full h-[2px] bg-cyan-400" />
                )}
              </button>
            ))}
          </div>

          {/* Error Message */}
          {error && <Alert type="error" message={error} onClose={() => setError(null)} />}

          {/* Projects Content */}
          {filteredProjects.length === 0 ? (
            <EmptyState
              icon={<Activity size={40} className="text-gray-600" />}
              title="No projects in this category"
              description="Ready to deploy? Upload your source code and let our AI handle the infrastructure."
              action={{
                label: (
                  <>
                    <Upload size={16} /> Upload Now
                  </>
                ),
                onClick: () => fileInputRef.current?.click(),
              }}
            />
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-3 gap-6">
              {filteredProjects.map((project, idx) => (
                <Card 
                  key={project._id} 
                  className="bg-white/5 border border-white/10 rounded-2xl p-6 h-full flex flex-col hover:border-white/20 transition-all group animate-fade-in"
                  style={{ animationDelay: `${idx * 0.05}s` }}
                >
                  <div className="flex justify-between items-start mb-6">
                    <div className="min-w-0">
                       <h3 className="text-xl font-black text-white truncate group-hover:text-cyan-400 transition-colors mb-2">
                         {project.project_name}
                       </h3>
                       <p className="text-xs text-gray-500 font-mono truncate">{project.file_name}</p>
                    </div>
                    <Badge variant={getStatusColor(project.status) as any}>
                      {project.status.toUpperCase()}
                    </Badge>
                  </div>

                  <div className="grid grid-cols-2 gap-3 mb-6">
                    <div className="bg-black/20 rounded-xl p-3 border border-white/5">
                      <p className="text-[9px] font-black text-gray-600 uppercase mb-1">Language</p>
                      <p className="text-xs font-bold truncate">{project.metadata?.language || 'Identifying'}</p>
                    </div>
                    <div className="bg-black/20 rounded-xl p-3 border border-white/5">
                      <p className="text-[9px] font-black text-gray-600 uppercase mb-1">Framework</p>
                      <p className="text-xs font-bold truncate">{project.metadata?.framework || 'Identifying'}</p>
                    </div>
                  </div>

                  <div className="mt-auto space-y-3">
                    <div className="flex gap-2">
                      <Button variant="primary" className="flex-1 text-[11px] h-9" onClick={() => { setSelectedProject(project); setShowDetailsModal(true); }}>
                        <Info size={14} /> Full Details
                      </Button>
                      <Button variant="secondary" className="px-3 h-9" onClick={() => handleDeleteProject(project._id)}>
                        <Trash2 size={14} />
                      </Button>
                    </div>
                    
                    {project.status === 'uploaded' && (
                      <Button variant="secondary" className="w-full text-xs h-10 border-cyan-500/30 text-cyan-400 hover:bg-cyan-500/10" onClick={() => { setSelectedProject(project); setShowExtractModal(true); }}>
                        <Play size={14} /> Initialize Analysis
                      </Button>
                    )}

                    {project.status === 'extracted' && (
                      <Button variant="secondary" className="w-full text-xs h-10 border-cyan-500/30 text-cyan-400 hover:bg-cyan-500/10" onClick={() => { setSelectedProject(project); setShowAnalyzeModal(true); }}>
                        <Play size={14} /> Analyze Project
                      </Button>
                    )}

                    {(project.status === 'analyzed' || project.status === 'completed') && (
                      <Button variant="secondary" className="w-full text-xs h-10 border-emerald-500/30 text-emerald-400 hover:bg-emerald-500/10" onClick={() => navigate(`/projects/${project._id}/deploy`)}>
                         <Container size={14} /> Deploy to Cloud
                      </Button>
                    )}
                  </div>
                </Card>
              ))}
            </div>
          )}
        </main>
      </div>

      {/* Modals Container */}
      {selectedProject && (
        <>
          <ExtractProjectModal
            project={selectedProject}
            isOpen={showExtractModal}
            onClose={() => setShowExtractModal(false)}
            onSuccess={handleExtractSuccess}
          />
          <AnalyzeProjectModal
            project={selectedProject}
            isOpen={showAnalyzeModal}
            onClose={() => setShowAnalyzeModal(false)}
            onSuccess={handleAnalyzeSuccess}
          />
          <ProjectDetailsModal
            project={selectedProject}
            isOpen={showDetailsModal}
            onClose={() => setShowDetailsModal(false)}
          />
        </>
      )}
    </div>
  );
};
