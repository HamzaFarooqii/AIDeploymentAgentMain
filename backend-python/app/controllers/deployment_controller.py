from fastapi import HTTPException
from datetime import datetime
from bson import ObjectId
import os
from ..config.database import get_projects_collection
from ..config.settings import settings
from ..utils.docker_builder import build_docker_image
from ..utils.docker_pusher import push_docker_image
from ..utils.k8s_deployer import deploy_to_kubernetes, get_deployment_status, cleanup_deployment
from ..utils.k8s_manifest_generator import generate_k8s_manifests
from ..utils.image_naming import build_project_image_repo


async def deploy_project_handler(project_id: str, current_user: dict):
    """Deploy extracted & analyzed project to local Kubernetes"""
    try:
        if not ObjectId.is_valid(project_id):
            raise HTTPException(status_code=400, detail="Invalid project ID format")
        
        collection = get_projects_collection()
        project = await collection.find_one({"_id": ObjectId(project_id)})
        
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        
        if project.get("user_id") != str(current_user["_id"]):
            raise HTTPException(status_code=403, detail="Access denied: Not project owner")
        
        if project.get("deployment_status") == "deploying":
            raise HTTPException(status_code=400, detail="Deployment already in progress")
        
        if project["status"] not in ["analyzed", "completed"]:
            return {
                "success": False,
                "message": "Project must be analyzed first",
                "current_status": project["status"]
            }
        
        await collection.update_one(
            {"_id": ObjectId(project_id)},
            {
                "$set": {"deployment_status": "deploying", "updated_at": datetime.now()},
                "$push": {"logs": {"message": "Deployment started", "timestamp": datetime.now()}}
            }
        )
        
        print(f"\n🚀 Deployment started: {project_id}")
        
        metadata = project.get("metadata", {})
        language = metadata.get("language", "Unknown")
        framework = metadata.get("framework", "Unknown")
        port = metadata.get("port", 8000)
        env_variables = metadata.get("env_variables", [])
        project_name = project.get("project_name", "app").replace(" ", "-").lower()
        extracted_path = project.get("extracted_path")
        
        if not extracted_path or not os.path.exists(extracted_path):
            raise HTTPException(status_code=400, detail="Extracted project files not found")
        
        print(f"🛠️ Detected language: {language}")
        
        # Step 1: Build Docker image
        print(f"🐳 Building Docker image...")
        image_name = build_project_image_repo(
            project.get("project_name", "app"),
            settings.DOCKER_HUB_USERNAME,
            settings.APP_REGISTRY_PREFIX,
        )
        image_tag = f"{image_name}:latest"
        
        build_result = build_docker_image(
            project_path=extracted_path,
            image_tag=image_tag,
            language=language,
            port=port,
            start_command=metadata.get("start_command"),
            build_command=metadata.get("build_command")
        )
        # Around line 50, after build_result:


        if build_result.get("detected_port"):
            port = build_result["detected_port"]
            print(f"🔌 Using detected port: {port}")
        
        if not build_result["success"]:
            raise Exception(f"Docker build failed: {build_result['message']}")
        
        print(f"✅ Docker image built")
        
        # Step 2: Push to Docker Hub
        print(f"📤 Pushing to Docker Hub...")
        push_result = push_docker_image(image_tag)
        
        if not push_result["success"]:
            raise Exception(f"Docker push failed: {push_result['message']}")
        
        print(f"✅ Docker image pushed")
        
        # Step 3: Generate K8s manifests
        print(f"📋 Generating K8s manifests...")
        deployment_name = f"devops-autopilot-{project_name}".replace("_", "-").lower()
        node_port = 30000 + (hash(project_id) % 2767)
        
        manifests = generate_k8s_manifests(
            deployment_name=deployment_name,
            image=image_tag,
            port=port,
            node_port=node_port,
            env_variables=env_variables,
            mongodb_url=f"mongodb://host.docker.internal:27017/{project_name}",  # Use project-specific DB
            labels={"project_id": project_id, "created_by": current_user.get("username", "user")}
        )
        
        # IMPORTANT: patch imagePullPolicy to IfNotPresent for local k8s
        # (Always would require image on DockerHub which may not be pushed yet)
        if "deployment" in manifests:
            manifests["deployment"] = manifests["deployment"].replace(
                "imagePullPolicy: Always", "imagePullPolicy: IfNotPresent"
            )
        
        print(f"✅ K8s manifests generated")
        
        # Step 4: Deploy to Kubernetes
        print(f"⚙️ Deploying to K8s...")
        k8s_result = deploy_to_kubernetes(manifests)
        
        if not k8s_result["success"]:
            raise Exception(f"K8s deployment failed: {k8s_result['message']}")
        
        print(f"✅ Deployed to K8s")
        
        # Step 5: Update database
        deployment_info = {
            "deployment_name": deployment_name,
            "pod_name": k8s_result.get("pod_name"),
            "service_url": f"http://localhost:{node_port}",
            "node_port": node_port,
            "image": image_tag,
            "status": "running",
            "deployed_at": datetime.now(),
            "k8s_namespace": "default",
            "language": language,
            "framework": framework
        }
        
        await collection.update_one(
            {"_id": ObjectId(project_id)},
            {
                "$set": {
                    "deployment_status": "deployed",
                    "deployment": deployment_info,
                    "status": "completed",
                    "updated_at": datetime.now()
                },
                "$push": {"logs": {"message": "Deployment completed successfully", "timestamp": datetime.now()}}
            }
        )
        
        print(f"✅ Deployment complete")
        
        return {
            "success": True,
            "message": "Project deployed successfully!",
            "data": {
                "project_id": project_id,
                "project_name": project_name,
                "deployment": deployment_info,
                "access_url": deployment_info["service_url"]
            }
        }
    
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Deployment error: {str(e)}")
        
        if ObjectId.is_valid(project_id):
            collection = get_projects_collection()
            await collection.update_one(
                {"_id": ObjectId(project_id)},
                {
                    "$set": {"deployment_status": "failed", "updated_at": datetime.now()},
                    "$push": {"logs": {"message": f"Deployment failed: {str(e)}", "timestamp": datetime.now()}}
                }
            )
        
        raise HTTPException(status_code=500, detail=f"Deployment failed: {str(e)}")


async def get_deployment_status_handler(project_id: str, current_user: dict):
    """Get deployment status"""
    try:
        if not ObjectId.is_valid(project_id):
            raise HTTPException(status_code=400, detail="Invalid project ID format")
        
        collection = get_projects_collection()
        project = await collection.find_one({"_id": ObjectId(project_id)})
        
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        
        if project.get("user_id") != str(current_user["_id"]):
            raise HTTPException(status_code=403, detail="Access denied")
        
        deployment = project.get("deployment", {})
        deployment_status = project.get("deployment_status", "not_deployed")
        
        return {
            "success": True,
            "project_id": project_id,
            "deployment_status": deployment_status,
            "deployment": deployment
        }
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get status: {str(e)}")


async def undeploy_project_handler(project_id: str, current_user: dict):
    """Remove deployment from K8s"""
    try:
        if not ObjectId.is_valid(project_id):
            raise HTTPException(status_code=400, detail="Invalid project ID format")
        
        collection = get_projects_collection()
        project = await collection.find_one({"_id": ObjectId(project_id)})
        
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        
        if project.get("user_id") != str(current_user["_id"]):
            raise HTTPException(status_code=403, detail="Access denied")
        
        deployment = project.get("deployment", {})
        deployment_name = deployment.get("deployment_name")
        
        if not deployment_name:
            return {
                "success": False,
                "message": "No deployment found"
            }
        
        print(f"🗑️ Undeploying: {deployment_name}")
        cleanup_deployment(deployment_name)
        
        await collection.update_one(
            {"_id": ObjectId(project_id)},
            {
                "$set": {
                    "deployment_status": "undeployed",
                    "deployment": None,
                    "updated_at": datetime.now()
                },
                "$push": {"logs": {"message": "Deployment removed", "timestamp": datetime.now()}}
            }
        )
        
        return {
            "success": True,
            "message": "Deployment removed"
        }
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Undeploy failed: {str(e)}")