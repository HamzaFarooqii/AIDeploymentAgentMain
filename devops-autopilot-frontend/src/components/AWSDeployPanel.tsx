import React, { useCallback, useEffect, useState } from 'react';
import {
  Cloud,
  Globe,
  MapPin,
  PauseCircle,
  PlayCircle,
  Rocket,
  Settings,
  Terminal,
  Trash2,
  Wrench,
} from 'lucide-react';
import { apiClient, streamAWSTerraform } from '../api/client';
import { Button } from './Button';
import { Alert } from './Alert';
import { Badge } from './Badge';
import { LoadingSpinner } from './LoadingSpinner';

interface AWSDeployPanelProps {
  projectId: string;
  /** Called after any operation that changes deployment state (generate/apply/destroy/scale). */
  onStatusChange?: () => void;
  /**
   * Optional hook for surfacing lifecycle messages to a host-owned log/chat
   * feed (e.g. DeployPage's AI chat sidebar). Not required for standalone use.
   */
  onLog?: (message: string) => void;
  /** Called after Terraform is successfully generated (e.g. to refresh a file explorer). */
  onTerraformGenerated?: () => void;
}

interface AWSConfig {
  aws_region: string;
  docker_repo_prefix: string;
  db_engine: string;
  mongo_db_url: string;
  rds_db_url: string;
  desired_count: number;
}

interface AWSStatus {
  aws_deployment_status: string;
  aws_region?: string;
  aws_frontend_url?: string;
  aws_ecs_cluster_id?: string;
  aws_last_deployed?: string;
  docker_push_success: boolean;
  live_alb_url?: string;
  live_cluster_name?: string;
  live_vpc_id?: string;
}

interface AWSPrerequisites {
  can_deploy: boolean;
  issues: string[];
  project_name: string;
  aws_region: string;
  docker_push_success: boolean;
  docker_hub_username?: string;
  terraform_exists?: boolean;
  aws_deployment_status?: string;
}

interface TerraformEvent {
  type: string;
  message: string;
  stage?: string;
}

type TerraformOperation = 'apply' | 'destroy' | 'scale-zero' | 'scale-up';

const AWS_REGIONS = [
  { value: 'us-east-1', label: 'US East (N. Virginia)' },
  { value: 'us-east-2', label: 'US East (Ohio)' },
  { value: 'us-west-1', label: 'US West (N. California)' },
  { value: 'us-west-2', label: 'US West (Oregon)' },
  { value: 'eu-west-1', label: 'EU (Ireland)' },
  { value: 'eu-central-1', label: 'EU (Frankfurt)' },
  { value: 'ap-south-1', label: 'Asia Pacific (Mumbai)' },
  { value: 'ap-southeast-1', label: 'Asia Pacific (Singapore)' },
];

const DB_ENGINES = [
  { value: 'none', label: 'No Database' },
  { value: 'mongo', label: 'MongoDB (Atlas/Cloud)' },
  { value: 'postgres', label: 'PostgreSQL (RDS)' },
  { value: 'mysql', label: 'MySQL (RDS)' },
];

const STATUS_BADGE_VARIANT: Record<string, 'default' | 'warning' | 'info' | 'success' | 'error' | 'purple'> = {
  not_deployed: 'default',
  terraform_generated: 'warning',
  deploying: 'info',
  deployed: 'success',
  failed: 'error',
  scaled_to_zero: 'purple',
};

const LOG_COLOR: Record<string, string> = {
  error: 'text-rose-400',
  warning: 'text-amber-400',
  success: 'text-emerald-400',
  info: 'text-gray-400',
};

const inputClass =
  'w-full bg-white/5 border border-white/10 rounded-xl px-4 py-3 text-xs text-white placeholder-gray-600 focus:outline-none focus:border-orange-500/50 transition-colors';
const labelClass = 'text-[10px] font-black uppercase tracking-widest text-gray-600 block mb-2';

const AWSDeployPanel: React.FC<AWSDeployPanelProps> = ({
  projectId,
  onStatusChange,
  onLog,
  onTerraformGenerated,
}) => {
  const [status, setStatus] = useState<AWSStatus | null>(null);
  const [prerequisites, setPrerequisites] = useState<AWSPrerequisites | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showConfig, setShowConfig] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [isDeploying, setIsDeploying] = useState(false);
  const [terraformLogs, setTerraformLogs] = useState<TerraformEvent[]>([]);

  const [config, setConfig] = useState<AWSConfig>({
    aws_region: 'us-east-1',
    docker_repo_prefix: '',
    db_engine: 'none',
    mongo_db_url: '',
    rds_db_url: '',
    desired_count: 1,
  });

  const loadStatus = useCallback(async () => {
    if (!projectId) return;
    try {
      const result = await apiClient.getAWSStatus(projectId);
      setStatus(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load AWS status');
    }
  }, [projectId]);

  useEffect(() => {
    if (!projectId) return;
    let cancelled = false;

    const load = async () => {
      setLoading(true);
      setError(null);

      const [statusResult, prereqResult] = await Promise.allSettled([
        apiClient.getAWSStatus(projectId),
        apiClient.checkAWSPrerequisites(projectId),
      ]);

      if (cancelled) return;

      if (statusResult.status === 'fulfilled') {
        setStatus(statusResult.value);
      } else {
        setError(statusResult.reason?.message || 'Failed to load AWS status');
      }

      if (prereqResult.status === 'fulfilled') {
        const prereqs = prereqResult.value;
        setPrerequisites(prereqs);
        setConfig((prev) => ({
          ...prev,
          docker_repo_prefix: prev.docker_repo_prefix || prereqs.docker_hub_username || '',
        }));
      }

      setLoading(false);
    };

    load();
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  const handleGenerateTerraform = async () => {
    if (!projectId) return;
    setError(null);
    setGenerating(true);
    onLog?.('Generating Terraform layer...');

    try {
      const result = await apiClient.generateTerraform(projectId, {
        aws_region: config.aws_region,
        docker_repo_prefix: config.docker_repo_prefix,
        db_engine: config.db_engine !== 'none' ? config.db_engine : undefined,
        mongo_db_url: config.db_engine === 'mongo' ? config.mongo_db_url : undefined,
        rds_db_url:
          config.db_engine !== 'none' && config.db_engine !== 'mongo' ? config.rds_db_url : undefined,
        desired_count: config.desired_count,
      });

      await loadStatus();
      setShowConfig(false);
      onLog?.(`Terraform layer generated at ${result.terraform_path}`);
      onTerraformGenerated?.();
      onStatusChange?.();
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Failed to generate Terraform';
      setError(msg);
      onLog?.(`Terraform generation failed: ${msg}`);
    } finally {
      setGenerating(false);
    }
  };

  const runOperation = (operation: TerraformOperation) => {
    if (!projectId) return;
    if (
      operation === 'destroy' &&
      !window.confirm('This will permanently delete all AWS resources. Are you sure?')
    ) {
      return;
    }

    setIsDeploying(true);
    setTerraformLogs([]);
    setError(null);

    streamAWSTerraform(
      projectId,
      operation,
      (event) => {
        setTerraformLogs((prev) => [...prev, event]);
      },
      () => {
        setIsDeploying(false);
        loadStatus();
        onStatusChange?.();
      },
      (err) => {
        setIsDeploying(false);
        setError(err.message);
        onLog?.(err.message);
      }
    );
  };

  if (loading && !status && !prerequisites) {
    return (
      <div className="flex flex-col items-center justify-center py-12">
        <LoadingSpinner message="Loading AWS status..." fullScreen={false} size="sm" />
      </div>
    );
  }

  const deploymentStatus =
    status?.aws_deployment_status || prerequisites?.aws_deployment_status || 'not_deployed';
  const canDeploy = status?.docker_push_success ?? prerequisites?.docker_push_success ?? false;
  const isDeployed = deploymentStatus === 'deployed';
  const isScaledToZero = deploymentStatus === 'scaled_to_zero';
  const hasTerraform =
    deploymentStatus === 'terraform_generated' ||
    isDeployed ||
    isScaledToZero ||
    !!prerequisites?.terraform_exists;

  const prereqIssuesMessage =
    prerequisites && prerequisites.issues && prerequisites.issues.length > 0
      ? prerequisites.issues.join(' ')
      : 'Push Docker images first to enable AWS deployment.';

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-black uppercase tracking-widest text-white flex items-center gap-2.5">
          <Cloud size={18} className="text-orange-400" />
          AWS Deployment
        </h3>
        <Badge variant={STATUS_BADGE_VARIANT[deploymentStatus] || 'default'}>
          {deploymentStatus.replace(/_/g, ' ').toUpperCase()}
        </Badge>
      </div>

      {error && <Alert type="error" message={error} onClose={() => setError(null)} />}

      {!canDeploy && <Alert type="warning" message={prereqIssuesMessage} />}

      {(isDeployed || isScaledToZero) && (
        <div className="bg-emerald-500/[0.04] border border-emerald-500/20 rounded-2xl p-6 space-y-4">
          {status?.live_alb_url && (
            <div className="flex items-center gap-3 text-sm">
              <Globe size={14} className="text-emerald-400 shrink-0" />
              <span className="text-gray-500 font-bold uppercase tracking-widest text-[10px] shrink-0">
                Frontend URL
              </span>
              <a
                href={`http://${status.live_alb_url}`}
                target="_blank"
                rel="noopener noreferrer"
                className="text-cyan-400 hover:text-cyan-300 hover:underline truncate"
              >
                {status.live_alb_url}
              </a>
            </div>
          )}
          {status?.aws_region && (
            <div className="flex items-center gap-3 text-sm">
              <MapPin size={14} className="text-emerald-400 shrink-0" />
              <span className="text-gray-500 font-bold uppercase tracking-widest text-[10px] shrink-0">
                Region
              </span>
              <span className="text-white font-medium">{status.aws_region}</span>
            </div>
          )}

          <div className="flex flex-wrap gap-3 pt-2">
            {isDeployed && (
              <Button onClick={() => runOperation('scale-zero')} disabled={isDeploying} variant="secondary">
                <PauseCircle size={14} /> Scale to Zero
              </Button>
            )}
            {isScaledToZero && (
              <Button onClick={() => runOperation('scale-up')} disabled={isDeploying} variant="secondary">
                <PlayCircle size={14} /> Scale Up
              </Button>
            )}
            <Button onClick={() => runOperation('destroy')} disabled={isDeploying} variant="danger">
              <Trash2 size={14} /> Destroy
            </Button>
          </div>
        </div>
      )}

      {showConfig && (
        <div className="bg-white/[0.03] border border-white/10 rounded-2xl p-6 space-y-5">
          <div>
            <label className={labelClass}>AWS Region</label>
            <select
              className={inputClass}
              value={config.aws_region}
              onChange={(e) => setConfig((prev) => ({ ...prev, aws_region: e.target.value }))}
            >
              {AWS_REGIONS.map((r) => (
                <option key={r.value} value={r.value}>
                  {r.label}
                </option>
              ))}
            </select>
          </div>

          <div>
            <label className={labelClass}>Docker Hub Username</label>
            <input
              type="text"
              className={inputClass}
              placeholder="e.g., yourusername"
              value={config.docker_repo_prefix}
              onChange={(e) => setConfig((prev) => ({ ...prev, docker_repo_prefix: e.target.value }))}
            />
          </div>

          <div>
            <label className={labelClass}>Database Engine</label>
            <select
              className={inputClass}
              value={config.db_engine}
              onChange={(e) => setConfig((prev) => ({ ...prev, db_engine: e.target.value }))}
            >
              {DB_ENGINES.map((d) => (
                <option key={d.value} value={d.value}>
                  {d.label}
                </option>
              ))}
            </select>
          </div>

          {config.db_engine === 'mongo' && (
            <div>
              <label className={labelClass}>MongoDB Connection URL</label>
              <input
                type="password"
                className={inputClass}
                placeholder="mongodb+srv://..."
                value={config.mongo_db_url}
                onChange={(e) => setConfig((prev) => ({ ...prev, mongo_db_url: e.target.value }))}
              />
            </div>
          )}

          {config.db_engine !== 'none' && config.db_engine !== 'mongo' && (
            <div>
              <label className={labelClass}>RDS Connection URL</label>
              <input
                type="password"
                className={inputClass}
                placeholder="postgresql://..."
                value={config.rds_db_url}
                onChange={(e) => setConfig((prev) => ({ ...prev, rds_db_url: e.target.value }))}
              />
            </div>
          )}

          <div>
            <label className={labelClass}>Desired Task Count</label>
            <input
              type="number"
              min={1}
              max={10}
              className={inputClass}
              value={config.desired_count}
              onChange={(e) =>
                setConfig((prev) => ({ ...prev, desired_count: parseInt(e.target.value, 10) || 1 }))
              }
            />
          </div>

          <div className="flex justify-end gap-3 pt-2">
            <Button onClick={() => setShowConfig(false)} variant="secondary">
              Cancel
            </Button>
            <Button
              onClick={handleGenerateTerraform}
              disabled={!config.docker_repo_prefix || generating}
              loading={generating}
            >
              <Wrench size={14} /> Generate Terraform
            </Button>
          </div>
        </div>
      )}

      {!showConfig && !isDeployed && !isScaledToZero && (
        <div className="flex gap-3">
          {!hasTerraform ? (
            <Button onClick={() => setShowConfig(true)} disabled={!canDeploy}>
              <Settings size={14} /> Configure AWS
            </Button>
          ) : (
            <Button onClick={() => runOperation('apply')} disabled={isDeploying} loading={isDeploying}>
              <Rocket size={14} /> Deploy to AWS
            </Button>
          )}
        </div>
      )}

      {terraformLogs.length > 0 && (
        <div>
          <h4 className="text-[10px] font-black uppercase tracking-widest text-gray-500 mb-3 flex items-center gap-2">
            <Terminal size={12} /> Terraform Output
          </h4>
          <div className="bg-[#050810] rounded-2xl p-5 font-mono text-[11px] border border-white/5 max-h-[300px] overflow-y-auto custom-scroll">
            {terraformLogs.map((log, i) => (
              <div key={i} className={`mb-1 leading-relaxed ${LOG_COLOR[log.type] || 'text-gray-400'}`}>
                <span className="text-gray-600 mr-2">[{(log.stage || 'tf').toUpperCase()}]</span>
                {log.message}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
};

export default AWSDeployPanel;
