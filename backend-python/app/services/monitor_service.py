import json
import os
import subprocess
import time
from datetime import datetime
from typing import Any, AsyncGenerator, Dict, List, Optional

from bson import ObjectId
from pymongo import MongoClient

from app.config.settings import settings
from app.utils.detector import find_project_root
from app.utils.k8s_deployer import diagnose_pod_health


def _resolve_project_infra_dir(project_id: str) -> Optional[str]:
    """
    Resolve the Terraform working directory for a project the same way
    AWSDeploymentService (app/services/aws_service.py) and
    _resolve_project_root() (app/controllers/aws_deploy_controller.py) do:

        {project_root}/infra

    where project_root is derived from the project's `extracted_path`
    (stored on the Mongo project document) via find_project_root() —
    NOT a top-level "terraform/<project_id>" folder relative to the
    process's current working directory.

    Uses a short-lived synchronous pymongo client (pymongo already ships
    as motor's dependency) since this service's methods are synchronous
    and only receive a project_id, not the project document.
    """
    if not project_id or not ObjectId.is_valid(project_id):
        return None

    try:
        client = MongoClient(settings.MONGODB_URL, serverSelectionTimeoutMS=3000)
        try:
            project = client[settings.DATABASE_NAME]["projects"].find_one(
                {"_id": ObjectId(project_id)}
            )
        finally:
            client.close()
    except Exception:
        return None

    if not project:
        return None

    extracted_path = project.get("extracted_path")
    if not extracted_path:
        return None

    real_path = os.path.abspath(extracted_path)
    if not os.path.exists(real_path):
        return None

    project_root = find_project_root(real_path)
    return os.path.join(project_root, "infra")


class MonitorService:

    @staticmethod
    def get_k8s_health(deployment_name: str) -> Dict[str, Any]:
        """Check Kubernetes health with enhanced details."""
        if not deployment_name:
            return {"status": "not_deployed", "healthy": False}
        return diagnose_pod_health(deployment_name)

    @staticmethod
    def get_aws_health(project_id: str) -> Dict[str, Any]:
        """Check AWS Terraform state health."""
        tf_dir = _resolve_project_infra_dir(project_id)
        if not tf_dir or not os.path.exists(os.path.join(tf_dir, "terraform.tfstate")):
            return {"status": "not_deployed", "healthy": False}

        try:
            cmd = ["terraform", "output", "-json"]
            process = subprocess.Popen(
                cmd, cwd=tf_dir, stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
            stdout, _ = process.communicate(timeout=10)

            if process.returncode == 0:
                return {
                    "status": "deployed",
                    "healthy": True,
                    "details": "Terraform state exists",
                }
            return {"status": "unknown", "healthy": False, "details": "Could not read outputs"}
        except Exception as e:
            return {"status": "error", "healthy": False, "details": str(e)}

    @staticmethod
    def trigger_self_healing(deployment_name: str) -> Dict[str, Any]:
        """Triggers a rollout restart of a Kubernetes deployment."""
        try:
            cmd = ["kubectl", "rollout", "restart", f"deployment/{deployment_name}"]
            process = subprocess.run(cmd, capture_output=True, timeout=30)

            if process.returncode == 0:
                return {
                    "success": True,
                    "message": f"Successfully triggered restart for {deployment_name}",
                }
            err = process.stderr.decode("utf-8", errors="replace")
            return {"success": False, "message": f"Failed to restart: {err}"}
        except Exception as e:
            return {"success": False, "message": str(e)}

    @staticmethod
    def get_recent_k8s_events(deployment_name: str, limit: int = 30) -> List[Dict[str, Any]]:
        """Fetch recent Kubernetes events related to a deployment."""
        events: List[Dict[str, Any]] = []
        try:
            cmd = [
                "kubectl", "get", "events",
                "--sort-by=lastTimestamp",
                f"--field-selector=involvedObject.name={deployment_name}",
                "-o", "json",
            ]
            proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15)
            if proc.returncode == 0:
                data = json.loads(proc.stdout.decode("utf-8", errors="replace"))
                items = data.get("items") or []
                for item in items[-limit:]:
                    events.append({
                        "type": item.get("type", "Normal"),
                        "reason": item.get("reason", ""),
                        "message": item.get("message", ""),
                        "timestamp": (
                            item.get("lastTimestamp")
                            or item.get("firstTimestamp")
                            or ""
                        ),
                        "count": item.get("count", 1),
                    })
        except Exception as e:
            events.append({
                "type": "Warning",
                "reason": "MonitorError",
                "message": f"Could not fetch k8s events: {e}",
                "timestamp": datetime.utcnow().isoformat(),
                "count": 1,
            })
        return events

    @staticmethod
    def get_pod_logs(deployment_name: str, tail_lines: int = 100) -> List[str]:
        """Retrieve recent pod logs for a deployment (synchronous snapshot)."""
        lines: List[str] = []
        try:
            # First get the pod name
            pod_cmd = [
                "kubectl", "get", "pods",
                "-l", f"app={deployment_name}",
                "-o", "jsonpath={.items[0].metadata.name}",
            ]
            pod_proc = subprocess.run(
                pod_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10
            )
            if pod_proc.returncode != 0 or not pod_proc.stdout:
                return [f"No running pod found for deployment '{deployment_name}'"]

            pod_name = pod_proc.stdout.decode("utf-8", errors="replace").strip()
            if not pod_name:
                return ["No pod name resolved"]

            log_cmd = ["kubectl", "logs", pod_name, f"--tail={tail_lines}", "--timestamps=true"]
            log_proc = subprocess.run(
                log_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20
            )
            if log_proc.returncode == 0:
                lines = log_proc.stdout.decode("utf-8", errors="replace").splitlines()
            else:
                err = log_proc.stderr.decode("utf-8", errors="replace").strip()
                lines = [f"[kubectl logs error] {err}"]
        except subprocess.TimeoutExpired:
            lines = ["[kubectl logs] Command timed out"]
        except Exception as e:
            lines = [f"[pod log error] {str(e)}"]
        return lines

    @staticmethod
    def get_all_pods_status(namespace: str = "default") -> List[Dict[str, Any]]:
        """Get status of all pods in the namespace."""
        pods: List[Dict[str, Any]] = []
        try:
            cmd = ["kubectl", "get", "pods", "-n", namespace, "-o", "json"]
            proc = subprocess.run(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15
            )
            if proc.returncode == 0:
                data = json.loads(proc.stdout.decode("utf-8", errors="replace"))
                for item in data.get("items") or []:
                    meta = item.get("metadata") or {}
                    status = item.get("status") or {}
                    container_statuses = status.get("containerStatuses") or []

                    ready = False
                    restarts = 0
                    state = status.get("phase", "Unknown")

                    if container_statuses:
                        cs = container_statuses[0]
                        ready = cs.get("ready", False)
                        restarts = cs.get("restartCount", 0)
                        state_dict = cs.get("state") or {}
                        if "running" in state_dict:
                            state = "Running"
                        elif "waiting" in state_dict:
                            state = f"Waiting ({state_dict['waiting'].get('reason', '')})"
                        elif "terminated" in state_dict:
                            state = f"Terminated ({state_dict['terminated'].get('reason', '')})"

                    pods.append({
                        "name": meta.get("name", ""),
                        "namespace": meta.get("namespace", namespace),
                        "status": state,
                        "ready": ready,
                        "restart_count": restarts,
                        "pod_ip": status.get("podIP", ""),
                        "labels": meta.get("labels") or {},
                        "created_at": meta.get("creationTimestamp", ""),
                    })
        except Exception as e:
            pods.append({
                "name": "error",
                "status": f"Error: {e}",
                "ready": False,
                "restart_count": 0,
            })
        return pods


monitor_service = MonitorService()
