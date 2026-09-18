import os
import json
from typing import Dict, List, Optional, Tuple
from pathlib import Path
import numpy as np

# torch/transformers/scikit-learn take 10+ seconds combined to import. They are
# only needed by the optional ML detection fallback (see detector.py's
# detect_framework(), which only reaches this path when heuristic detection has
# low confidence), so importing them at module load time would pay that cost on
# EVERY server startup / every `import app.utils.ml_analyzer` (including
# transitively via app.utils.detector -> app.controllers.analyze_controller ->
# app.main), even for requests that never touch ML detection. Instead, import
# them lazily on first real use via _ensure_ml_imports().
#
# The module-level placeholders below (None) let tests keep using
# `@patch('app.utils.ml_analyzer.AutoTokenizer')` / `AutoModel`: a lazy import
# only overwrites a name that is still None, so a test-installed mock is never
# clobbered by a subsequent real import attempt.
HAS_ML = False
_ml_import_attempted = False
torch = None
AutoTokenizer = None
AutoModel = None
cosine_similarity = None


def _ensure_ml_imports() -> bool:
    """Import torch/transformers/scikit-learn on first use; cached after that."""
    global HAS_ML, _ml_import_attempted, torch, AutoTokenizer, AutoModel, cosine_similarity

    if _ml_import_attempted:
        return HAS_ML
    _ml_import_attempted = True

    try:
        import torch as _torch
        from transformers import AutoTokenizer as _AutoTokenizer, AutoModel as _AutoModel
        from sklearn.metrics.pairwise import cosine_similarity as _cosine_similarity

        if torch is None:
            torch = _torch
        if AutoTokenizer is None:
            AutoTokenizer = _AutoTokenizer
        if AutoModel is None:
            AutoModel = _AutoModel
        if cosine_similarity is None:
            cosine_similarity = _cosine_similarity
        HAS_ML = True
    except ImportError as e:
        print(f"⚠️ ML libraries not available: {e}")
        HAS_ML = False
    return HAS_ML



class MLCodeAnalyzer:
    """Pure ML-powered code analyzer using ONLY CodeBERT"""
    
    def __init__(self):
        self.model = None
        self.tokenizer = None
        self.device = "cpu"  # Force CPU for stability
        self.language_embeddings = {}
        self.framework_embeddings = {}
        self.ml_available = _ensure_ml_imports()
        
        # Language signatures for embedding
        self.language_signatures = {
            "Python": "import def class self print return if elif else for while try except with as pass",
            "JavaScript": "const let var function => async await return if else for while try catch class extends",
            "TypeScript": "interface type const let var function => async await return class extends implements",
            "Java": "public class private static void String int import package extends implements",
            "Go": "package import func type struct interface return if else for range defer",
            "Ruby": "def class module require include attr_accessor attr_reader attr_writer end",
            "PHP": "<?php function class public private protected static namespace use return",
        }
        
        # Framework signatures for embedding
        self.framework_signatures = {
            "Flask": "from flask import Flask app.route render_template request jsonify Blueprint",
            "Django": "from django.conf import settings INSTALLED_APPS django.db.models django.urls path",
            "FastAPI": "from fastapi import FastAPI app.get app.post Depends HTTPException",
            "Express.js": "const express require('express') app.listen app.get app.post router",
            "Next.js": "import next getServerSideProps getStaticProps useEffect useState",
            "React": "import React useState useEffect useRef useContext createContext",
            "Vue.js": "import Vue createApp ref reactive computed watch",
            "Spring Boot": "@SpringBootApplication @RestController @GetMapping @PostMapping @Autowired",
            "Laravel": "use Illuminate namespace Route Model Controller Eloquent",
            "Rails": "ActiveRecord has_many belongs_to validates before_action",
        }
        
        if self.ml_available:
            self._initialize_model()
            if self.model:
                self._initialize_embeddings()
        else:
            print("⚠️ ML libraries not installed. Please install: pip install torch transformers scikit-learn")
    
    def _initialize_model(self):
        """Initialize CodeBERT model"""
        try:
            print("🤖 Loading CodeBERT model...")
            model_name = "microsoft/codebert-base"
            
            cache_dir = os.path.expanduser("~/.cache/huggingface/hub")
            os.makedirs(cache_dir, exist_ok=True)
            
            self.tokenizer = AutoTokenizer.from_pretrained(
                model_name,
                cache_dir=cache_dir,
                trust_remote_code=True
            )
            self.model = AutoModel.from_pretrained(
                model_name,
                cache_dir=cache_dir,
                trust_remote_code=True
            )
            self.model.to(self.device)
            self.model.eval()
            
            print(f"✅ CodeBERT loaded successfully on {self.device}")
            self.ml_available = True
            
        except Exception as e:
            print(f"❌ CodeBERT loading failed: {e}")
            print("   Install required libraries: pip install torch transformers scikit-learn")
            self.model = None
            self.ml_available = False
    
    def _initialize_embeddings(self):
        """Pre-compute embeddings for all languages and frameworks"""
        if not self.model or not HAS_ML:
            print("⚠️ Skipping embedding initialization (model not loaded)")
            return
        
        print("🔄 Computing language & framework embeddings...")
        
        try:
            # Compute language embeddings
            for lang, signature in self.language_signatures.items():
                embedding = self._extract_embedding(signature)
                if embedding is not None:
                    self.language_embeddings[lang] = embedding
            
            # Compute framework embeddings
            for framework, signature in self.framework_signatures.items():
                embedding = self._extract_embedding(signature)
                if embedding is not None:
                    self.framework_embeddings[framework] = embedding
            
            print(f"✅ Computed {len(self.language_embeddings)} language embeddings")
            print(f"✅ Computed {len(self.framework_embeddings)} framework embeddings")
            
        except Exception as e:
            print(f"❌ Embedding initialization failed: {e}")
            import traceback
            traceback.print_exc()
    
    def _extract_embedding(self, text: str) -> Optional[np.ndarray]:
        """Extract embedding from text using CodeBERT"""
        if not self.model or not HAS_ML or not text.strip():
            return None
        
        try:
            inputs = self.tokenizer(
                text,
                return_tensors="pt",
                truncation=True,
                max_length=512,
                padding=True
            )
            inputs = {k: v.to(self.device) for k, v in inputs.items()}
            
            with torch.no_grad():
                outputs = self.model(**inputs)
                embedding = outputs.last_hidden_state[:, 0, :].cpu().numpy()
            
            return embedding
            
        except Exception as e:
            print(f"⚠️ Embedding extraction failed: {e}")
            return None
    
    def analyze_project_structure(self, project_path: str) -> Dict:
        """Analyze project structure and collect code samples"""
        analysis = {
            "total_files": 0,
            "code_files": 0,
            "config_files": [],
            "key_files": [],
            "code_samples": []
        }
        
        important_files = [
            "package.json", "requirements.txt", "pom.xml",
            "composer.json", "go.mod", "Gemfile", "Cargo.toml"
        ]
        
        code_extensions = {".py", ".js", ".ts", ".java", ".go", ".rb", ".php"}
        
        try:
            for root, _, files in os.walk(project_path):
                # Skip ignored directories
                if any(skip in root for skip in ["node_modules", "__pycache__", ".git", "venv", "dist", "build"]):
                    continue
                
                for file in files:
                    analysis["total_files"] += 1
                    file_path = os.path.join(root, file)
                    ext = Path(file).suffix.lower()
                    
                    # Track important config files
                    if file in important_files:
                        analysis["key_files"].append(file)
                        analysis["config_files"].append(file_path)
                    
                    # Collect code samples
                    if ext in code_extensions:
                        analysis["code_files"] += 1
                        if len(analysis["code_samples"]) < 15:  # Collect up to 15 samples
                            try:
                                with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                                    content = f.read(5000)  # Read first 5000 chars
                                    if content.strip():
                                        analysis["code_samples"].append({
                                            "file": file,
                                            "ext": ext,
                                            "content": content
                                        })
                            except:
                                pass
        
        except Exception as e:
            print(f"⚠️ Structure analysis error: {e}")
        
        return analysis
    
    def detect_language_ml(self, structure: Dict) -> Tuple[str, float]:
        """Detect language using ONLY CodeBERT embeddings"""
        
        if not self.model or not HAS_ML or not self.language_embeddings:
            return "Unknown", 0.0
        
        if not structure["code_samples"]:
            return "Unknown", 0.0
        
        try:
            # Combine code samples
            code_texts = [sample["content"][:1000] for sample in structure["code_samples"][:10]]
            combined_code = "\n".join(code_texts)
            
            # Get project code embedding
            project_embedding = self._extract_embedding(combined_code)
            
            if project_embedding is None:
                return "Unknown", 0.0
            
            # Calculate similarity with each language
            best_language = "Unknown"
            best_similarity = 0.0
            
            for lang, lang_embedding in self.language_embeddings.items():
                similarity = cosine_similarity(project_embedding, lang_embedding)[0][0]
                
                if similarity > best_similarity:
                    best_similarity = similarity
                    best_language = lang
            
            confidence = float(best_similarity)
            
            print(f"   🔤 Language Detection: {best_language} (similarity: {confidence:.3f})")
            
            return best_language, confidence
            
        except Exception as e:
            print(f"   ❌ Language detection failed: {e}")
            import traceback
            traceback.print_exc()
            return "Unknown", 0.0
    
    def detect_framework_ml(self, structure: Dict, language: str) -> Tuple[str, float]:
        """Detect framework using ONLY CodeBERT embeddings"""
        
        if not self.model or not HAS_ML or not self.framework_embeddings:
            return "Unknown", 0.0
        
        if not structure["code_samples"]:
            return "Unknown", 0.0
        
        try:
            # Combine code samples
            code_texts = [sample["content"][:1000] for sample in structure["code_samples"][:10]]
            combined_code = "\n".join(code_texts)
            
            # Get project code embedding
            project_embedding = self._extract_embedding(combined_code)
            
            if project_embedding is None:
                return "Unknown", 0.0
            
            # Calculate similarity with each framework
            best_framework = "Unknown"
            best_similarity = 0.0
            
            for framework, fw_embedding in self.framework_embeddings.items():
                similarity = cosine_similarity(project_embedding, fw_embedding)[0][0]
                
                if similarity > best_similarity:
                    best_similarity = similarity
                    best_framework = framework
            
            confidence = float(best_similarity)
            
            print(f"   🎯 Framework Detection: {best_framework} (similarity: {confidence:.3f})")
            
            return best_framework, confidence
            
        except Exception as e:
            print(f"   ❌ Framework detection failed: {e}")
            import traceback
            traceback.print_exc()
            return "Unknown", 0.0
    
    def analyze_project(self, project_path: str) -> Dict:
        """Main ML analysis pipeline - PURE CodeBERT"""
        
        print("🧠 Starting PURE CodeBERT ML Analysis...")
        
        if not self.ml_available or not self.model:
            print("❌ CodeBERT not available! Cannot analyze.")
            return {
                "language": "Unknown",
                "language_confidence": 0.0,
                "framework": "Unknown",
                "framework_confidence": 0.0,
                "detection_method": "Failed - ML not available",
                "ml_available": False,
                "structure_analysis": {
                    "total_files": 0,
                    "code_files": 0,
                    "key_files": [],
                    "file_types": {}
                }
            }
        
        # Step 1: Analyze structure
        structure = self.analyze_project_structure(project_path)
        print(f"📊 Found {structure['total_files']} files, {structure['code_files']} code files")
        
        if structure['code_files'] == 0:
            print("⚠️ No code files found!")
            return {
                "language": "Unknown",
                "language_confidence": 0.0,
                "framework": "Unknown",
                "framework_confidence": 0.0,
                "detection_method": "Failed - No code files",
                "ml_available": self.ml_available,
                "structure_analysis": {
                    "total_files": structure["total_files"],
                    "code_files": 0,
                    "key_files": structure["key_files"],
                    "file_types": {}
                }
            }
        
        # Step 2: Detect Language using CodeBERT
        print("🔍 Detecting language...")
        language, lang_confidence = self.detect_language_ml(structure)
        
        # Step 3: Detect Framework using CodeBERT
        print("🔍 Detecting framework...")
        framework, fw_confidence = self.detect_framework_ml(structure, language)
        
        print(f"\n✅ Analysis Complete!")
        print(f"   Language: {language} (confidence: {lang_confidence:.2f})")
        print(f"   Framework: {framework} (confidence: {fw_confidence:.2f})")
        
        return {
            "language": language,
            "language_confidence": round(lang_confidence, 2),
            "framework": framework,
            "framework_confidence": round(fw_confidence, 2),
            "detection_method": "CodeBERT ML",
            "ml_available": self.ml_available,
            "structure_analysis": {
                "total_files": structure["total_files"],
                "code_files": structure["code_files"],
                "key_files": structure["key_files"],
                "file_types": {}
            }
        }


# Singleton instance
_ml_analyzer_instance = None

def get_ml_analyzer() -> MLCodeAnalyzer:
    """Get or create ML analyzer singleton"""
    global _ml_analyzer_instance
    if _ml_analyzer_instance is None:
        _ml_analyzer_instance = MLCodeAnalyzer()
    return _ml_analyzer_instance