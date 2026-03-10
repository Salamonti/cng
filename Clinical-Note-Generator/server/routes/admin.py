# server/routes/admin.py
import json
import os
import subprocess
from pathlib import Path
from typing import Dict, Optional, List, Tuple, Any

import socket
from fastapi import APIRouter, HTTPException, Query, Body, Depends

from server.core.dependencies import get_current_admin
from server.models.user import User

router = APIRouter(
    prefix="/api/admin",
    tags=["admin"],
    dependencies=[Depends(get_current_admin)],
)

BASE_DIR = Path(__file__).resolve().parents[1]
LOGS_DIR = BASE_DIR / ".." / "logs"
CONFIG_PATH = BASE_DIR / ".." / "config" / "config.json"


@router.get("/logs/tail")
@router.get("/logs/tail/")
def logs_tail(lines: int = Query(200)) -> Dict:
    logs_dir = BASE_DIR / "logs"

    # Look for log files in priority order
    log_candidates = [
        logs_dir / "server.log",
        logs_dir / "http_requests.csv",
        logs_dir / "access.log",
        logs_dir / "app.log"
    ]

    # Also check for any .log or .csv files
    if logs_dir.exists():
        log_files = list(logs_dir.glob("*.log")) + list(logs_dir.glob("*.csv"))
        log_candidates.extend(log_files)

    # Find the first existing log file
    path = None
    for candidate in log_candidates:
        if candidate.exists():
            path = candidate
            break

    if not path:
        return {
            "lines": ["No log files found in logs directory"],
            "log_file": "None",
            "available_files": [str(f.name) for f in logs_dir.glob("*") if f.is_file()] if logs_dir.exists() else []
        }

    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            data = f.readlines()[-lines:]
        return {
            "lines": [line.rstrip("\n") for line in data],
            "log_file": str(path.name),
            "total_lines": len(data)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/models")
@router.get("/models/")
def list_models() -> Dict:
    whisper_dir = (BASE_DIR / ".." / "models" / "whisper").resolve()
    llama_dir = (BASE_DIR / ".." / "models" / "llama").resolve()
    whisper_entries: set[str] = set()
    if whisper_dir.exists():
        for p in whisper_dir.iterdir():
            if p.is_file():
                whisper_entries.add(p.name)
            elif p.is_dir():
                whisper_entries.add(p.name)
    whisper = sorted(whisper_entries)
    llm = []
    if llama_dir.exists():
        for p in llama_dir.iterdir():
            if p.is_file() and p.suffix.lower() == ".gguf":
                llm.append(p.name)
    # Match expected keys from client: llm_models, whisper_models
    return {"llm_models": llm, "whisper_models": whisper}


def _normalize_path(p: str) -> str:
    p = str(p or "").strip().strip('"')
    return os.path.normpath(os.path.expandvars(p))


def _repo_root() -> Path:
    return BASE_DIR.parent


def _resolve_llm_model_path(value: str, cfg: Dict) -> Optional[str]:
    """Resolve a filename or relative path to an absolute file path if found."""
    if not value:
        return None
    v = _normalize_path(value)
    # Absolute
    if os.path.isabs(v) and os.path.exists(v):
        return os.path.abspath(v)
    candidates: list[Path] = []
    repo = _repo_root()
    # relative to repo
    candidates.append(repo / v)
    # models_dir
    models_dir = cfg.get("models_dir")
    if models_dir:
        md = Path(_normalize_path(models_dir))
        candidates.append(md / v)
        candidates.append(md / "llama" / v)
    # repo models folders
    candidates.append(repo / "models" / v)
    candidates.append(repo / "models" / "llama" / v)
    for c in candidates:
        try:
            if c.exists():
                return os.path.abspath(str(c))
        except Exception:
            continue
    return None


def _configure_llama_service(cfg: Dict) -> Tuple[bool, str]:
    """No-op: internal manager mode; skip Windows service configuration."""
    return True, "skipped"


@router.post("/models/select")
@router.post("/models/select/")
async def select_models(payload: Dict = Body(...)) -> Dict:
    cfg = _load_cfg()
    updated: Dict[str, str] = {}
    if "whisper_model" in payload:
        cfg["whisper_model"] = str(payload["whisper_model"]).strip()
        updated["whisper_model"] = cfg["whisper_model"]
    if "llm_model" in payload:
        raw = str(payload["llm_model"]).strip()
        resolved = _resolve_llm_model_path(raw, cfg)
        if resolved:
            cfg["llm_model"] = resolved
            updated["llm_model"] = resolved
        else:
            # Keep previous behavior: accept raw value to let manager resolve later
            cfg["llm_model"] = raw
            updated["llm_model"] = raw
    _save_cfg(cfg)

    # Signal services to reload config (no FastAPI restart needed)
    try:
        from routes.notes import note_gen
        if hasattr(note_gen, 'reload_config'):
            note_gen.reload_config()
    except Exception:
        pass

    # Llama-server is externally managed; no in-app restart
    llama_apply: Dict[str, Any] = {"ok": False, "note": "externalized"}

    return {"ok": True, "config": cfg, "updated": updated, "llama_apply": llama_apply}


def _port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


@router.get("/ocr/status")
@router.get("/ocr/status/")
def ocr_status(url: Optional[str] = None) -> Dict:
    # Accept host:port in url or infer from env; default 127.0.0.1:8090
    probe = (url or os.environ.get("OCR_URL_PRIMARY") or "http://127.0.0.1:8090").strip()
    host = "127.0.0.1"
    port = 8090
    try:
        # crude parse for host:port
        if "://" in probe:
            rest = probe.split("://", 1)[1]
        else:
            rest = probe
        parts = rest.split("/")[0]
        if ":" in parts:
            host, ps = parts.split(":", 1)
            port = int(ps)
        else:
            host = parts
    except Exception:
        pass
    ok = _port_open(host, port)

    # Try lightweight HTTP probes if port is open
    http_health: Optional[Dict[str, Any]] = None
    http_models: Optional[Dict[str, Any]] = None
    model_count = None
    if ok:
        try:
            import requests as _rq
            r = _rq.get(f"http://{host}:{port}/health", timeout=2)
            http_health = {"status": r.status_code, "ok": 200 <= r.status_code < 500}
        except Exception as e:
            http_health = {"error": str(e)}
        try:
            import requests as _rq
            r2 = _rq.get(f"http://{host}:{port}/v1/models", timeout=2)
            if r2.headers.get("content-type", "").startswith("application/json"):
                try:
                    js = r2.json()
                    # avoid dumping huge payloads; just count models
                    if isinstance(js, dict) and "data" in js and isinstance(js["data"], list):
                        model_count = len(js["data"])
                        # include up to first 3 model ids for quick sanity check
                        names = []
                        try:
                            for item in js["data"][:3]:
                                if isinstance(item, dict):
                                    name = item.get("id") or item.get("name")
                                    if name:
                                        names.append(str(name))
                        except Exception:
                            pass
                        http_models = {"status": r2.status_code, "ok": 200 <= r2.status_code < 500, "model_count": model_count, "models": names}
                    else:
                        http_models = {"status": r2.status_code, "ok": 200 <= r2.status_code < 500}
                except Exception:
                    http_models = {"status": r2.status_code, "ok": 200 <= r2.status_code < 500}
            else:
                http_models = {"status": r2.status_code, "ok": 200 <= r2.status_code < 500}
        except Exception as e:
            http_models = {"error": str(e)}

    return {"host": host, "port": port, "reachable": ok, "http_health": http_health, "http_models": http_models}


@router.get("/llama/status")
@router.get("/llama/status/")
def llama_status() -> Dict:
    # Get llama server port from env or default to 8081
    url = os.environ.get("NOTEGEN_URL_PRIMARY", "http://127.0.0.1:8081")
    host = "127.0.0.1"
    port = 8081
    try:
        if "://" in url:
            host_port = url.split("://", 1)[1].split("/", 1)[0]
        else:
            host_port = url.split("/", 1)[0]
        if ":" in host_port:
            host, ps = host_port.split(":", 1)
            port = int(ps)
        else:
            host = host_port
    except Exception:
        pass

    # Check if port is open
    port_open = _port_open(host, port)

    # Try to get health from llama server if it's running
    health_status = None
    model_info = None

    if port_open:
        try:
            import requests
            response = requests.get(f"http://{host}:{port}/health", timeout=5)
            if response.status_code == 200:
                health_status = response.json()
        except Exception:
            health_status = {"status": "unreachable"}

        # Try to get model info
        try:
            import requests
            response = requests.get(f"http://{host}:{port}/v1/models", timeout=5)
            if response.status_code == 200:
                model_info = response.json()
        except Exception:
            model_info = {"error": "Could not retrieve model info"}

    # Check configured model existence and resolved path (without starting server)
    configured_model = cfg.get("llm_model", "Unknown")
    import os as _os
    model_exists = _os.path.isabs(configured_model) and _os.path.exists(configured_model)

    # Externalized llama-server: no internal resolution
    resolved_path = configured_model if model_exists else None
    last_error = None

    # Normalize active model from llama-server response (unify view)
    active_model_id: Optional[str] = None
    active_meta: Optional[Dict[str, Any]] = None
    active_caps: Optional[List[str]] = None
    try:
        if isinstance(model_info, dict):
            # Prefer OpenAI-style 'data' list
            data = model_info.get("data")
            if isinstance(data, list) and data:
                item = data[0] if isinstance(data[0], dict) else None
                if item:
                    active_model_id = str(item.get("id") or item.get("name") or item.get("model") or "").strip() or None
                    active_meta = item.get("meta") if isinstance(item.get("meta"), dict) else None
            # Fallback to llama.cpp 'models' list
            if not active_model_id:
                ml = model_info.get("models")
                if isinstance(ml, list) and ml:
                    m0 = ml[0] if isinstance(ml[0], dict) else None
                    if m0:
                        active_model_id = str(m0.get("model") or m0.get("name") or m0.get("id") or "").strip() or None
                        caps = m0.get("capabilities")
                        if isinstance(caps, list):
                            active_caps = [str(x) for x in caps]
    except Exception:
        pass

    # Compute in_sync between configured/resolved and active model
    def _normpath(p: Optional[str]) -> Optional[str]:
        try:
            if not p:
                return None
            import os as _os
            return _os.path.normcase(_os.path.normpath(str(p)))
        except Exception:
            return p

    in_sync = None
    in_sync_by: Optional[str] = None
    try:
        rp = _normpath(resolved_path)
        am = _normpath(active_model_id)
        if rp and am:
            # If active model id is not an absolute path, compare basenames
            import os as _os
            if not _os.path.isabs(am):
                am = _normpath(_os.path.basename(am))
                rp = _normpath(_os.path.basename(rp))
                in_sync = (rp == am)
                in_sync_by = "basename" if in_sync else None
            else:
                # Cross-OS robustness: consider matching basenames equal even if absolute roots differ
                rp_base = _normpath(_os.path.basename(rp))
                am_base = _normpath(_os.path.basename(am))
                if rp == am:
                    in_sync = True
                    in_sync_by = "fullpath"
                elif rp_base and am_base and rp_base == am_base:
                    in_sync = True
                    in_sync_by = "basename"
                else:
                    in_sync = False
    except Exception:
        in_sync = None
        in_sync_by = None

    return {
        "host": host,
        "port": port,
        "reachable": port_open,
        "health": health_status,
        # Normalized active model summary
        "active_model": active_model_id,
        "active_model_meta": active_meta,
        "active_model_capabilities": active_caps,
        # Raw model info from llama-server retained for compatibility
        "model_info": model_info,
        "configured_model": configured_model,
        "configured_model_exists": model_exists,
        "resolved_model_path": resolved_path,
        "in_sync": in_sync,
        "in_sync_by": in_sync_by,
        "last_error": last_error,
    }


@router.get("/llama/health")
@router.get("/llama/health/")
async def llama_health() -> Dict[str, Any]:
    """Externalized llama-server (no internal manager)."""
    return {"running": False, "note": "externalized"}


@router.post("/llama/start")
@router.post("/llama/start/")
async def llama_start() -> Dict[str, Any]:
    """Start llama-server via internal manager (disabled)."""
    return {"ok": False, "note": "externalized"}


@router.post("/llama/stop")
@router.post("/llama/stop/")
async def llama_stop() -> Dict[str, Any]:
    """Stop llama-server via internal manager (disabled)."""
    return {"ok": False, "note": "externalized"}


@router.post("/llama/restart")
@router.post("/llama/restart/")
async def llama_restart() -> Dict[str, Any]:
    """Restart llama-server via internal manager (disabled)."""
    return {"ok": False, "note": "externalized"}


@router.get("/config")
@router.get("/config/")
def get_config() -> Dict:
    return _load_cfg()


@router.post("/config/save")
@router.post("/config/save/")
def save_config(cfg: Dict = Body(...)) -> Dict:
    _save_cfg(cfg)
    # Signal any services that need to reload config
    try:
        from routes.notes import note_gen
        note_gen.reload_config()
    except Exception:
        pass  # Note generator may not be imported yet
    return {"ok": True}


@router.post("/models/parameters")
@router.post("/models/parameters/")
def update_model_parameters(params: Dict = Body(...)) -> Dict:
    """Update model parameters in configuration"""
    cfg = _load_cfg()

    # Update note generation parameters
    if "temperature" in params:
        cfg["default_note_temperature"] = float(params["temperature"])

    if "max_tokens" in params:
        cfg["default_note_max_tokens"] = int(params["max_tokens"])

    if "context_length" in params:
        cfg["context_length"] = int(params["context_length"])

    # Update Q&A parameters
    if "qa_temperature" in params:
        cfg["default_qa_temperature"] = float(params["qa_temperature"])

    if "qa_max_tokens" in params:
        cfg["default_qa_max_tokens"] = int(params["qa_max_tokens"])

    if "qa_context_length" in params:
        cfg["qa_context_length"] = int(params["qa_context_length"])

    if "qa_max_user_chars" in params:
        cfg["qa_max_user_chars"] = int(params["qa_max_user_chars"])

    # Update server parameters
    if "batch_size" in params:
        # Keep legacy key for compatibility and also map to llama-server specific key
        cfg["batch_size"] = int(params["batch_size"])
        cfg["llama_server_batch_size"] = int(params["batch_size"])

    if "llama_server_batch_size" in params:
        cfg["llama_server_batch_size"] = int(params["llama_server_batch_size"])

    if "gpu_layers" in params:
        cfg["llama_server_gpu_layers"] = int(params["gpu_layers"])

    if "llama_server_threads" in params:
        cfg["llama_server_threads"] = int(params["llama_server_threads"])

    if "llama_server_log_disable" in params:
        cfg["llama_server_log_disable"] = bool(params["llama_server_log_disable"])
    if "llama_no_mmap" in params:
        cfg["llama_no_mmap"] = bool(params["llama_no_mmap"])

    # CUDA backend controls
    if "llama_force_cublas" in params:
        cfg["llama_force_cublas"] = bool(params["llama_force_cublas"])
    if "llama_mmq_enable" in params:
        cfg["llama_mmq_enable"] = bool(params["llama_mmq_enable"])
    if isinstance(params.get("llama_env"), dict):
        # Merge/update environment overrides for llama-server child process
        env_cfg = cfg.get("llama_env") if isinstance(cfg.get("llama_env"), dict) else {}
        env_cfg.update({str(k): str(v) for k, v in params["llama_env"].items()})
        cfg["llama_env"] = env_cfg

    # OCR chat template (specific to OCR server)
    if "ocr_chat_template" in params:
        cfg["ocr_chat_template"] = str(params["ocr_chat_template"]).strip()

    _save_cfg(cfg)

    # Signal any services that need to reload config
    try:
        from routes.notes import note_gen
        if hasattr(note_gen, 'reload_config'):
            note_gen.reload_config()
    except Exception:
        pass  # Note generator may not be imported yet

    return {"ok": True, "updated_parameters": params, "config": cfg}


def _load_cfg() -> Dict:
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    # Defaults per plan
    return {
        "save_audio": True,
        "audio_retention_days": 60,
        "asr_chunk_seconds": 3,
        "asr_segment_seconds": 12,
        "vad_default": False,
        "default_note_temperature": 0.3,
        "default_note_max_tokens": 3000,
    }


# -------------------------------------------------------------
# Service management via NSSM/SC (Windows)
# -------------------------------------------------------------

def _service_names(cfg: Dict) -> Dict[str, str]:
    # Allow override in config.json (optional)
    services = cfg.get("services", {}) if isinstance(cfg.get("services"), dict) else {}
    return {
        "fastapi": services.get("fastapi", "ClinicalFastAPI"),
        "llama": services.get("llama", "LlamaServer"),
        "ocr": services.get("ocr") or "",
        "rag": services.get("rag", "RAGApi"),
    }


def _run_cmd(args: List[str], timeout: int = 10) -> Tuple[int, str]:
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout, shell=False)
        out = (p.stdout or "") + (p.stderr or "")
        return p.returncode, out.strip()
    except Exception as e:
        return 1, str(e)


def _service_status_win(name: str) -> Dict:
    if not name:
        return {
            "name": "internal",
            "status": "managed",
            "raw": "Managed internally by application",
        }
    # Try NSSM first
    code, out = _run_cmd([_nssm_bin(), "status", name])
    status = None
    if code == 0:
        # NSSM prints e.g. "SERVICE_RUNNING" or "Stopped"
        lower = out.lower()
        if "running" in lower:
            status = "running"
        elif "stopped" in lower:
            status = "stopped"

    if status is None:
        # Fallback to sc query
        code, out = _run_cmd(["sc", "query", name])
        lower = out.lower()
        if "state" in lower and "running" in lower:
            status = "running"
        elif "state" in lower and ("stopped" in lower or "stop" in lower):
            status = "stopped"

    return {
        "name": name,
        "status": status or "unknown",
        "raw": out,
    }


def _service_action_win(name: str, action: str) -> Tuple[bool, str]:
    """Enhanced service action with smart dependency handling"""
    if not name:
        return True, "Service managed internally; no external controller required"
    if action == "start":
        # Try NSSM then sc
        code, out = _run_cmd([_nssm_bin(), "start", name])
        if code != 0:
            code, out = _run_cmd(["sc", "start", name])
        if code == 0:
            return True, out
        # Fallback: try batch files for known services
        batch_map = {
            "LlamaServer": "start_llama_server.bat",
            "FastAPIServer": "start_fastapi_server.bat",
        }
        bf = batch_map.get(name)
        if bf:
            batch_path = BASE_DIR.parent / bf
            if batch_path.exists():
                try:
                    subprocess.Popen(
                        [str(batch_path)],
                        cwd=str(BASE_DIR.parent),
                        creationflags=subprocess.CREATE_NO_WINDOW,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    return True, f"Started via batch: {bf}"
                except Exception as e:
                    return False, f"Batch start failed: {e}"
        return False, out
    elif action == "stop":
        # Smart stopping: try multiple approaches

        # 1. Try normal stop first
        code, out = _run_cmd(["sc", "stop", name])
        if code == 0:
            return True, out

        # 2. If sc fails, try NSSM
        code, out = _run_cmd([_nssm_bin(), "stop", name])
        if code == 0:
            return True, out

        # 3. If dependency issue, try process termination
        if "depends" in out.lower() or "dependency" in out.lower():
            # Get the service PID and kill the process directly
            query_code, query_out = _run_cmd(["sc", "queryex", name])
            if query_code == 0:
                # Extract PID from output
                lines = query_out.split('\n')
                for line in lines:
                    if 'PID' in line and ':' in line:
                        try:
                            pid = line.split(':')[1].strip()
                            if pid.isdigit():
                                kill_code, kill_out = _run_cmd(["taskkill", "/F", "/PID", pid])
                                if kill_code == 0:
                                    return True, f"Process killed directly (PID: {pid})"
                        except Exception:
                            pass

            # 4. Last resort: try to kill by process name
            process_names = {
                "FastAPIServer": "python.exe",
                "LlamaServer": "llama-server.exe",
                "OCRServer": "llama-server.exe"
            }
            if name in process_names:
                kill_code, kill_out = _run_cmd(["taskkill", "/F", "/IM", process_names[name]])
                if kill_code == 0:
                    return True, f"Process killed by name: {process_names[name]}"

        return False, out
    elif action == "restart":
        ok1, out1 = _service_action_win(name, "stop")
        if not ok1:
            return False, out1
        # Wait a moment for process to fully stop
        import time
        time.sleep(2)
        ok2, out2 = _service_action_win(name, "start")
        return ok2, out2
    else:
        return False, f"unsupported action: {action}"


def _port_status(host: str, port: int) -> bool:
    return _port_open(host, port)


@router.get("/services/status")
@router.get("/services/status/")
def services_status() -> Dict:
    cfg = _load_cfg()
    names = _service_names(cfg)
    services: Dict[str, Dict] = {}
    # Known ports from env
    ocr_port = 8090
    try:
        ocr_url = str(os.environ.get("OCR_URL_PRIMARY", "http://127.0.0.1:8090"))
        if "://" in ocr_url:
            host_port = ocr_url.split("://", 1)[1].split("/", 1)[0]
        else:
            host_port = ocr_url.split("/", 1)[0]
        if ":" in host_port:
            ocr_port = int(host_port.split(":", 1)[1])
    except Exception:
        pass
    rag_port = None
    try:
        rag_url = str(os.environ.get("RAG_URL", "http://127.0.0.1:8007"))
        host_port = rag_url.split("://", 1)[1].split("/", 1)[0]
        if ":" in host_port:
            rag_port = int(host_port.split(":", 1)[1])
        else:
            rag_port = 80
    except Exception:
        rag_port = 8007

    ports = {
        "fastapi": 7860,
        "llama": int(os.environ.get("NOTEGEN_URL_PRIMARY", "http://127.0.0.1:8081").split(":")[-1].split("/")[0]) if os.environ.get("NOTEGEN_URL_PRIMARY") else 8081,
        "ocr": ocr_port,
        "rag": rag_port,
    }

    for sid, name in names.items():
        st = _service_status_win(name)
        st.update({
            "id": sid,
            "display": {
                "fastapi": "FastAPI Server",
                "llama": "LLaMA Server",
                "ocr": "OCR Server",
            }.get(sid, name),
            "port": ports.get(sid),
            "reachable": _port_status("127.0.0.1", ports.get(sid, 0)) if ports.get(sid) else None,
        })
        # Externalized services: no internal manager process info

        # Prioritize port reachability: if reachable, treat as running even if service control says stopped
        try:
            if st.get("reachable") and st.get("status") != "running":
                st["status"] = "running"
                st["note"] = "reachable"
        except Exception:
            pass
        services[sid] = st
    return {"services": services}


@router.get("/rag/status")
@router.get("/rag/status/")
def rag_status() -> Dict[str, Any]:
    url = str(os.environ.get("RAG_URL", "http://127.0.0.1:8007")).rstrip("/")
    # Parse port
    try:
        host = url.split("://", 1)[1].split("/", 1)[0].split(":")[0]
        port = int(url.split("://", 1)[1].split("/", 1)[0].split(":")[1]) if ":" in url.split("://", 1)[1].split("/", 1)[0] else 80
    except Exception:
        host, port = "127.0.0.1", 8007
    reachable = _port_status("127.0.0.1", port)
    health_status = None
    try:
        import requests
        r = requests.get(f"{url}/health", timeout=2)
        if r.ok:
            health_status = r.json()
        else:
            health_status = {"status": f"HTTP {r.status_code}"}
    except Exception as e:
        health_status = {"status": "error", "detail": str(e)[:160]}
    return {"url": url, "host": host, "port": port, "reachable": reachable, "health": health_status}


# Server management removed - use batch files instead
# Services can be managed via Windows Services or NSSM
# Use start_llama_server.bat, start_ocr_server.bat, start_fastapi_server.bat


def _save_cfg(cfg: Dict) -> None:
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _nssm_bin() -> str:
    # Prefer explicit path if available
    path = r"C:\\nssm.exe"
    return path if os.path.exists(path) else "nssm"
