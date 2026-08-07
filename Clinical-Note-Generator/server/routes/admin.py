# server/routes/admin.py
import copy
import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Dict, Optional, List, Tuple, Any

import socket
from fastapi import APIRouter, HTTPException, Query, Body, Depends

from server.core.service_endpoints import load_service_endpoints, service_endpoints_path
from server.core.service_endpoints_sync import sync_env_from_structure, validate_llama_instances
from server.core.dependencies import get_current_admin

router = APIRouter(
    prefix="/api/admin",
    tags=["admin"],
    dependencies=[Depends(get_current_admin)],
)

logger = logging.getLogger("cng.admin")


BASE_DIR = Path(__file__).resolve().parents[1]
LOGS_DIR = BASE_DIR / ".." / "logs"
CONFIG_PATH = BASE_DIR / ".." / "config" / "config.json"

# GET /config redacts these; POST merge keeps disk values when body still contains __REDACTED__.
_CONFIG_SECRET_KEYS = frozenset({"jwt_secret", "jwt_refresh_secret", "admin_api_key"})
_REDACTED_PLACEHOLDER = "__REDACTED__"


def _redact_config_for_admin(cfg: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(cfg)
    for k in _CONFIG_SECRET_KEYS:
        if k not in out:
            continue
        v = out[k]
        if v is None or str(v).strip() == "":
            continue
        s = str(v).strip()
        if "SET_IN_ENV" in s.upper() or s.startswith("${"):
            continue
        out[k] = _REDACTED_PLACEHOLDER
    return out


def _merge_config_save(incoming: Dict[str, Any], existing: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(incoming)
    for k in _CONFIG_SECRET_KEYS:
        if out.get(k) == _REDACTED_PLACEHOLDER:
            out[k] = existing.get(k)
        elif k not in out and k in existing:
            out[k] = existing[k]
    return out


def _validate_config_save_payload(incoming: Dict[str, Any], existing: Dict[str, Any]) -> None:
    """Post–P2: refuse empty or clearly truncated saves that would wipe most of config.json."""
    if not incoming:
        raise HTTPException(status_code=400, detail="Refusing empty config save. Reload and edit the full JSON.")
    try:
        raw = json.dumps(incoming, ensure_ascii=False)
    except (TypeError, ValueError) as e:
        raise HTTPException(status_code=400, detail=f"Config is not JSON-serializable: {e}") from e
    if len(raw) > 4_000_000:
        raise HTTPException(status_code=400, detail="Config JSON exceeds maximum size (4MB).")
    if existing and len(existing) >= 12 and len(incoming) < max(6, len(existing) // 4):
        raise HTTPException(
            status_code=400,
            detail="Config payload looks truncated vs the file on disk. Reload config, then save again.",
        )


def _pchost_stack_proxy_row(service_id: str, display: str, allowlist_key: str) -> Dict[str, Any]:
    """P3: TCP probe + optional NSSM name from windows_services[allowlist_key]."""
    data = _load_service_endpoints_or_empty()
    osp = data.get("office_stack_processes") if isinstance(data.get("office_stack_processes"), dict) else {}
    block = osp.get(service_id) if isinstance(osp, dict) else {}
    ports_list: List[int] = []
    raw_ports = block.get("ports") if isinstance(block, dict) else None
    if isinstance(raw_ports, list):
        for p in raw_ports:
            try:
                ports_list.append(int(p))
            except (TypeError, ValueError):
                continue
    allow = _windows_services_allowlist()
    nssm = str(allow.get(allowlist_key) or "").strip()
    base = _service_status_win(nssm) if nssm else {"name": "", "status": "—", "raw": ""}
    probe = ports_list[0] if ports_list else None
    reachable = bool(probe) and _port_status("127.0.0.1", int(probe))
    port_str = "/".join(str(p) for p in ports_list[:8]) if ports_list else "—"
    note_parts = [f"office_stack_processes.{service_id}"]
    if not block:
        note_parts.append("no office_stack_processes block (add in service_endpoints.json)")
    if service_id == "notes_proxy":
        note_parts.append("status only in admin UI")
    row: Dict[str, Any] = dict(base)
    row.update(
        {
            "id": service_id,
            "display": display,
            "port": port_str,
            "reachable": reachable,
            "note": "; ".join(note_parts),
        }
    )
    try:
        if row.get("reachable") and row.get("status") != "running":
            row["status"] = "running"
            prev = (row.get("note") or "").strip()
            row["note"] = ("reachable — " + prev) if prev else "reachable"
    except Exception:
        # Best-effort status enrichment: a live port overrides a stale OS
        # status. If the merge fails for any reason, return the row (and status)
        # as already computed rather than error the whole services list.
        pass
    return row


def _service_control_enabled() -> bool:
    # Hard safety gate: disabled unless operator explicitly enables.
    # This prevents accidental stop/start from a UI click or a CSRF-like scenario.
    return str(os.environ.get("ADMIN_SERVICE_CONTROL_ENABLED", "")).strip() in {"1", "true", "yes", "on"}


def _admin_process_control_enabled() -> bool:
    """Master switch for admin-driven process start/stop (AI servers + office stack). Set in startup scripts."""
    return str(os.environ.get("ADMIN_PROCESS_CONTROL_ENABLED", "")).strip().lower() in {"1", "true", "yes", "on"}


def _ai_process_control_enabled() -> bool:
    """llama-server / whisper-server control: master flag or legacy ADMIN_AI_PROCESS_CONTROL_ENABLED."""
    return _admin_process_control_enabled() or str(os.environ.get("ADMIN_AI_PROCESS_CONTROL_ENABLED", "")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _office_stack_process_control_enabled() -> bool:
    """PCHost + FastAPI + RAG processes: master flag or ADMIN_OFFICE_STACK_PROCESS_CONTROL_ENABLED."""
    return _admin_process_control_enabled() or str(os.environ.get("ADMIN_OFFICE_STACK_PROCESS_CONTROL_ENABLED", "")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _write_json_atomic(path: Path, data: Dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _load_service_endpoints_or_empty() -> Dict[str, Any]:
    data = load_service_endpoints()
    return data if isinstance(data, dict) else {}


def _windows_services_allowlist() -> Dict[str, str]:
    """
    Single source of truth for Windows/NSSM service names is service_endpoints.json.

    Example shape:
      { "windows_services": { "officestack": "officestack", "fastapi": "ClinicalFastAPI" } }
    """
    data = _load_service_endpoints_or_empty()
    block = data.get("windows_services") if isinstance(data.get("windows_services"), dict) else {}
    out: Dict[str, str] = {}
    for k, v in block.items():
        if not isinstance(k, str):
            continue
        if not isinstance(v, str):
            continue
        name = v.strip()
        if not name:
            continue
        out[k.strip()] = name
    return out


@router.get("/service_endpoints")
@router.get("/service_endpoints/")
def admin_get_service_endpoints() -> Dict[str, Any]:
    """Read-only view of the single-source-of-truth endpoints file."""
    p = service_endpoints_path()
    data = _load_service_endpoints_or_empty()
    return {"path": str(p), "data": data}


@router.put("/service_endpoints")
@router.put("/service_endpoints/")
def admin_put_service_endpoints(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """
    Update service_endpoints.json.

    When `llama_instances` is non-empty, URL keys under `env` are derived from
    `feature_routing` + `services_urls` + `fastapi_port` before write.

    NOTE: This does not hot-reload all consumers. Most services read env/config at process start.
    """
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Payload must be a JSON object.")
    p = service_endpoints_path()
    data = copy.deepcopy(payload)
    li = data.get("llama_instances")
    wi = data.get("whisper_instances")
    structured = (isinstance(li, dict) and bool(li)) or (isinstance(wi, dict) and bool(wi))
    sync_env_from_structure(data)
    warnings = validate_llama_instances(data)
    try:
        _write_json_atomic(p, data)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to write service_endpoints.json: {e}")
    note = (
        "service_endpoints.json updated. Restart affected services to apply new ports/URLs/models/samplers."
    )
    if warnings:
        note += " Warnings: " + "; ".join(warnings)
    return {
        "ok": True,
        "path": str(p),
        "restart_required": True,
        "structured_sync_applied": structured,
        "warnings": warnings,
        "data": data,
        "note": note,
    }


def _ai_process_block_status(block: Any) -> Dict[str, Any]:
    from server.core.ai_process_launcher import parse_port_from_url, tcp_port_listening

    out: Dict[str, Any] = {}
    if not isinstance(block, dict):
        return out
    for iid, inst in block.items():
        if not isinstance(iid, str) or iid.startswith("_"):
            continue
        if not isinstance(inst, dict):
            continue
        url = str(inst.get("base_url") or "").strip()
        port = parse_port_from_url(url)
        if port is None and inst.get("port") is not None:
            try:
                port = int(inst["port"])
            except (TypeError, ValueError):
                port = None
        listening = bool(port) and tcp_port_listening("127.0.0.1", int(port)) if port is not None else False
        out[iid] = {
            "kind": inst.get("kind"),
            "port": port,
            "listening": listening,
            "base_url": url or None,
        }
    return out


@router.get("/ai/processes/status")
@router.get("/ai/processes/status/")
def ai_processes_status() -> Dict[str, Any]:
    """TCP probe on instance ports for llama_instances + whisper_instances (from service_endpoints.json)."""
    data = _load_service_endpoints_or_empty()
    return {
        "process_control_enabled": _admin_process_control_enabled(),
        "ai_process_control_enabled": _ai_process_control_enabled(),
        "office_stack_process_control_enabled": _office_stack_process_control_enabled(),
        "llama_instances": _ai_process_block_status(data.get("llama_instances")),
        "whisper_instances": _ai_process_block_status(data.get("whisper_instances")),
    }


@router.post("/ai/processes/start")
@router.post("/ai/processes/start/")
def ai_processes_start(body: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    if not _ai_process_control_enabled():
        raise HTTPException(
            status_code=403,
            detail="AI process control disabled. Set ADMIN_PROCESS_CONTROL_ENABLED=1 (or legacy ADMIN_AI_PROCESS_CONTROL_ENABLED=1) on the FastAPI process.",
        )
    group = str(body.get("group") or "").strip()
    iid = str(body.get("id") or "").strip()
    if group not in {"llama_instances", "whisper_instances"} or not iid:
        raise HTTPException(status_code=400, detail="Body must include group (llama_instances|whisper_instances) and id.")

    data = _load_service_endpoints_or_empty()
    from server.core.ai_process_launcher import start_instance

    try:
        ok, msg, pid = start_instance(data, group, iid)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"ok": True, "message": msg, "pid": pid, "group": group, "id": iid}


@router.post("/ai/processes/stop")
@router.post("/ai/processes/stop/")
def ai_processes_stop(body: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    if not _ai_process_control_enabled():
        raise HTTPException(
            status_code=403,
            detail="AI process control disabled. Set ADMIN_PROCESS_CONTROL_ENABLED=1 (or legacy ADMIN_AI_PROCESS_CONTROL_ENABLED=1) on the FastAPI process.",
        )
    group = str(body.get("group") or "").strip()
    iid = str(body.get("id") or "").strip()
    if group not in {"llama_instances", "whisper_instances"} or not iid:
        raise HTTPException(status_code=400, detail="Body must include group (llama_instances|whisper_instances) and id.")

    data = _load_service_endpoints_or_empty()
    from server.core.ai_process_launcher import parse_port_from_url, stop_listeners_on_port

    blk = data.get(group)
    if not isinstance(blk, dict):
        raise HTTPException(status_code=400, detail=f"Unknown group {group}")
    inst = blk.get(iid)
    if not isinstance(inst, dict):
        raise HTTPException(status_code=400, detail=f"Unknown instance {iid!r}")

    url = str(inst.get("base_url") or "").strip()
    port = parse_port_from_url(url)
    if port is None and inst.get("port") is not None:
        try:
            port = int(inst["port"])
        except (TypeError, ValueError):
            port = None
    if port is None:
        raise HTTPException(status_code=400, detail="Cannot resolve port for this instance.")

    ok, msg = stop_listeners_on_port(int(port))
    return {"ok": ok, "message": msg, "port": port, "group": group, "id": iid}


@router.get("/office/processes/status")
@router.get("/office/processes/status/")
def office_processes_status() -> Dict[str, Any]:
    """TCP probe for office_stack_processes (same logical units as startup/start-office-stack.ps1)."""
    from server.core.office_stack_launcher import office_process_status

    data = _load_service_endpoints_or_empty()
    return {
        "process_control_enabled": _admin_process_control_enabled(),
        "office_stack_process_control_enabled": _office_stack_process_control_enabled(),
        "processes": office_process_status(data),
    }


@router.post("/office/processes/start")
@router.post("/office/processes/start/")
def office_processes_start(body: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    if not _office_stack_process_control_enabled():
        raise HTTPException(
            status_code=403,
            detail="Office stack process control disabled. Set ADMIN_PROCESS_CONTROL_ENABLED=1 (or ADMIN_OFFICE_STACK_PROCESS_CONTROL_ENABLED=1) on the FastAPI process.",
        )
    pid = str(body.get("id") or "").strip()
    if not pid:
        raise HTTPException(status_code=400, detail="Body must include id (office_stack_processes key).")
    data = _load_service_endpoints_or_empty()
    from server.core.office_stack_launcher import start_office_process

    ok, msg, proc_id = start_office_process(data, pid)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"ok": True, "message": msg, "pid": proc_id, "id": pid}


@router.post("/office/processes/stop")
@router.post("/office/processes/stop/")
def office_processes_stop(body: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    if not _office_stack_process_control_enabled():
        raise HTTPException(
            status_code=403,
            detail="Office stack process control disabled. Set ADMIN_PROCESS_CONTROL_ENABLED=1 (or ADMIN_OFFICE_STACK_PROCESS_CONTROL_ENABLED=1) on the FastAPI process.",
        )
    pid = str(body.get("id") or "").strip()
    if not pid:
        raise HTTPException(status_code=400, detail="Body must include id.")
    data = _load_service_endpoints_or_empty()
    from server.core.office_stack_launcher import stop_office_process

    ok, msg = stop_office_process(data, pid)
    return {"ok": ok, "message": msg, "id": pid}


@router.post("/office/processes/start_all")
@router.post("/office/processes/start_all/")
def office_processes_start_all() -> Dict[str, Any]:
    if not _office_stack_process_control_enabled():
        raise HTTPException(
            status_code=403,
            detail="Office stack process control disabled. Set ADMIN_PROCESS_CONTROL_ENABLED=1 on the FastAPI process.",
        )
    data = _load_service_endpoints_or_empty()
    from server.core.office_stack_launcher import start_office_all

    return {"ok": True, "results": start_office_all(data)}


@router.post("/office/processes/stop_all")
@router.post("/office/processes/stop_all/")
def office_processes_stop_all() -> Dict[str, Any]:
    if not _office_stack_process_control_enabled():
        raise HTTPException(
            status_code=403,
            detail="Office stack process control disabled. Set ADMIN_PROCESS_CONTROL_ENABLED=1 on the FastAPI process.",
        )
    data = _load_service_endpoints_or_empty()
    from server.core.office_stack_launcher import stop_office_all

    return {"ok": True, "results": stop_office_all(data)}


def _reload_llm_clients_from_disk() -> None:
    """Reload note_gen + qa_text clients after config.json changes (P2 routing)."""
    try:
        from routes.notes import note_gen

        if hasattr(note_gen, "reload_config"):
            note_gen.reload_config()
    except Exception:
        # Best-effort hot-reload after a config.json save. If the running note
        # generator can't reload, the saved config still applies on next service
        # restart -- but the operator's action did NOT take effect live, so say
        # so instead of silently claiming success.
        logger.warning("note_gen.reload_config() failed after config save", exc_info=True)
    try:
        from services.note_generator_clean import get_simple_note_generator

        get_simple_note_generator("qa_text").reload_config()
    except Exception:
        # Same rationale as above: the QA-text client failed to hot-reload the
        # saved config; surface it so the operator knows to restart to apply.
        logger.warning("qa_text.reload_config() failed after config save", exc_info=True)


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


def _normalize_path(p: str) -> str:
    p = str(p or "").strip().strip('"')
    return os.path.normpath(os.path.expandvars(p))


def _fs_allowed_roots() -> List[Path]:
    """
    Server-side paths the admin file browser may traverse. Restricted to the app checkout
    and its parent project folder plus usual model directories (no arbitrary drive letters).
    """
    app = BASE_DIR.parent.resolve()
    roots: List[Path] = [app]
    proj = app.parent
    if app.name == "Clinical-Note-Generator" and proj.exists():
        roots.append(proj.resolve())
    extras = [
        app / "models",
        app / "models" / "llama",
        app / "models" / "whisper",
    ]
    if proj.exists():
        extras.extend(
            [
                proj / "models",
                proj / "Clinical-Note-Generator" / "models" / "llama",
                proj / "Clinical-Note-Generator" / "models" / "whisper",
            ]
        )
    for e in extras:
        try:
            if e.exists() and e.is_dir():
                roots.append(e.resolve())
        except Exception:
            # Best-effort root discovery: skip any candidate dir that can't be
            # resolved/inspected (gone, perms); the filesystem browser stays
            # usable with whatever roots it did collect.
            pass
    seen: set[str] = set()
    out: List[Path] = []
    for r in roots:
        k = str(r)
        if k in seen:
            continue
        seen.add(k)
        out.append(r)
    return out


def _fs_root_label(r: Path) -> str:
    try:
        return r.name or str(r)
    except Exception:
        return str(r)


def _fs_path_allowed(target: Path) -> bool:
    try:
        t = target.resolve()
    except Exception:
        return False
    for root in _fs_allowed_roots():
        try:
            t.relative_to(root.resolve())
            return True
        except ValueError:
            continue
    return False


def _fs_parent_for(current: Path) -> Optional[str]:
    if not _fs_path_allowed(current):
        return None
    par = current.parent
    if par == current:
        return None
    if _fs_path_allowed(par):
        try:
            return str(par.resolve())
        except Exception:
            return None
    return None


def _fs_browse_resolve_preset(preset: Optional[str]) -> Optional[str]:
    """Map admin UI preset to first existing models/llama or models/whisper folder under allowlisted roots."""
    p = (preset or "").strip().lower()
    if p not in {"llama_models", "whisper_models"}:
        return None
    seg = "llama" if p == "llama_models" else "whisper"
    candidates: List[Path] = []
    seen_paths: set[str] = set()
    for root in _fs_allowed_roots():
        try:
            r = root.resolve()
        except Exception:
            continue
        if not r.is_dir():
            continue
        try:
            if r.name.lower() == seg and "model" in r.as_posix().lower():
                sp = str(r)
                if sp not in seen_paths:
                    seen_paths.add(sp)
                    candidates.append(r)
        except Exception:
            # Best-effort preset foldering: skip this candidate on any failure
            # and keep scanning the other roots.
            pass
        for rel in (
            ("models", seg),
            ("Clinical-Note-Generator", "models", seg),
        ):
            try:
                c = r.joinpath(*rel).resolve()
                if c.is_dir():
                    sp = str(c)
                    if sp not in seen_paths:
                        seen_paths.add(sp)
                        candidates.append(c)
            except Exception:
                continue
    return str(candidates[0]) if candidates else None


@router.get("/fs/browse")
@router.get("/fs/browse/")
def admin_fs_browse(
    path: Optional[str] = Query(None),
    preset: Optional[str] = Query(None),
) -> Dict[str, Any]:
    """
    List directories and files under allowlisted roots for choosing model paths in admin UI.
    Not a download API — path must stay within allowed trees (path traversal blocked).
    Optional preset=llama_models|whisper_models opens the first matching models/<seg> directory.
    """
    roots = _fs_allowed_roots()
    roots_out = [{"label": _fs_root_label(r), "path": str(r)} for r in roots]
    path_effective = path
    if (path_effective is None or not str(path_effective).strip()) and preset:
        resolved = _fs_browse_resolve_preset(preset)
        if resolved:
            path_effective = resolved
    if path_effective is None or not str(path_effective).strip():
        return {
            "path": None,
            "parent": None,
            "preset": preset,
            "roots": roots_out,
            "directories": [],
            "files": [],
            "is_file": False,
        }
    raw = str(path_effective).strip()
    try:
        p = Path(os.path.expandvars(_normalize_path(raw))).resolve()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid path.")
    if not p.exists():
        raise HTTPException(status_code=404, detail="Path not found.")
    if not _fs_path_allowed(p):
        raise HTTPException(status_code=403, detail="Path is outside allowed directories.")
    if p.is_file():
        try:
            sz = p.stat().st_size
        except Exception:
            sz = 0
        return {
            "path": str(p),
            "parent": _fs_parent_for(p),
            "roots": roots_out,
            "directories": [],
            "files": [{"name": p.name, "path": str(p), "size": sz}],
            "is_file": True,
        }
    dirs: List[Dict[str, str]] = []
    files: List[Dict[str, Any]] = []
    try:
        for child in sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
            if child.name.startswith("."):
                continue
            try:
                if child.is_dir():
                    dirs.append({"name": child.name, "path": str(child.resolve())})
                elif child.is_file():
                    st = child.stat()
                    files.append({"name": child.name, "path": str(child.resolve()), "size": st.st_size})
            except Exception:
                continue
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    return {
        "path": str(p),
        "parent": _fs_parent_for(p),
        "roots": roots_out,
        "directories": dirs,
        "files": files,
        "is_file": False,
    }


def _port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


@router.get("/ocr/status")
@router.get("/ocr/status/")
def ocr_status(url: Optional[str] = None) -> Dict:
    # Accept host:port in url or infer from env; Qwen Vision defaults to 8001.
    probe = (url or os.environ.get("OCR_URL_PRIMARY") or "http://127.0.0.1:8001").strip()
    host = "127.0.0.1"
    port = 8001
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
        # Best-effort host:port parse of the probe URL for the reachability
        # check; on any malformed URL fall through to the pre-set host/port.
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
                            # Best-effort model-name extraction for the status
                            # hint; if it trips, still keep the http_models
                            # block with the model_count already computed.
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


@router.post("/asr/upstream_probe")
@router.post("/asr/upstream_probe/")
async def admin_asr_upstream_probe() -> Dict[str, Any]:
    """POST a minimal WAV to each whisper-server `/inference` — verifies pool reachability and response shape."""
    from server.routes.asr import probe_asr_upstream_inference_compat

    return await probe_asr_upstream_inference_compat()


@router.get("/llama/status")
@router.get("/llama/status/")
def llama_status() -> Dict:
    # Compatibility endpoint: reports the configured DreamCision text backend.
    url = os.environ.get("NOTEGEN_URL_PRIMARY", "http://127.0.0.1:8004")
    host = "127.0.0.1"
    port = 8004
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
        # Best-effort host:port parse of the configured text URL for the status
        # view; fall through to the default 127.0.0.1:8004 on malformed input.
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
    cfg = _load_cfg()
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
        # Best-effort normalization of the upstream model-info response. If the
        # fields don't match either schema, leave active_* unset (UI degrades to
        # 'unknown') rather than fail the whole llama status endpoint.
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



@router.get("/config")
@router.get("/config/")
def get_config() -> Dict[str, Any]:
    """Return config path and data with secrets redacted for safe UI display."""
    raw = _load_cfg()
    return {"path": str(CONFIG_PATH.resolve()), "data": _redact_config_for_admin(raw)}


@router.post("/config/save")
@router.post("/config/save/")
def save_config(body: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """
    Write config.json. Accepts either `{ \"data\": { ... } }` (admin UI) or a legacy raw object.
    Values still equal to __REDACTED__ for secret keys are not overwritten (disk wins).
    """
    incoming = body["data"] if isinstance(body.get("data"), dict) else body
    if not isinstance(incoming, dict):
        raise HTTPException(status_code=400, detail="Payload must be a JSON object.")
    existing = _load_cfg()
    _validate_config_save_payload(incoming, existing)
    merged = _merge_config_save(incoming, existing)
    _save_cfg(merged)
    _reload_llm_clients_from_disk()
    return {"ok": True, "path": str(CONFIG_PATH.resolve())}


@router.post("/models/parameters")
@router.post("/models/parameters/")
def update_model_parameters(params: Dict = Body(...)) -> Dict:
    """Update model parameters in configuration"""
    cfg = _load_cfg()

    # Update note generation parameters
    if "temperature" in params:
        cfg["default_note_temperature"] = float(params["temperature"])

    if "max_tokens" in params:
        from server.core.token_limits import clamp_completion_tokens

        mt = int(params["max_tokens"])
        if mt > 0:
            cfg["default_note_max_tokens"] = clamp_completion_tokens(mt)

    if "context_length" in params:
        cfg["context_length"] = int(params["context_length"])

    # Update Q&A parameters
    if "qa_temperature" in params:
        cfg["default_qa_temperature"] = float(params["qa_temperature"])

    if "qa_max_tokens" in params:
        cfg["default_qa_max_tokens"] = int(params["qa_max_tokens"])

    if "qa_context_length" in params:
        from server.core.token_limits import MAX_COMPLETION_TOKENS_CAP

        qc = int(params["qa_context_length"])
        cfg["qa_context_length"] = max(512, min(MAX_COMPLETION_TOKENS_CAP, qc))

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
    _reload_llm_clients_from_disk()

    return {"ok": True, "updated_parameters": params, "config": cfg}


def _load_cfg() -> Dict:
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            # Corrupt/unreadable *present* admin config: fall back to plan
            # defaults rather than break the admin UI, but surface the
            # misconfiguration instead of swallowing it silently. (Missing
            # config stays silent - defaults are the normal baseline.)
            logger.warning("Failed to load admin config/config.json; using defaults", exc_info=True)
    # Defaults per plan
    return {
        "save_audio": True,
        "audio_retention_days": 7,
        "asr_segment_seconds": 12,
        "vad_default": False,
        "default_note_temperature": 0.3,
        "default_note_max_tokens": 12288,
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
        "llama_secondary": services.get("llama_secondary") or "",
        "ocr": services.get("ocr") or "",
        "rag": services.get("rag", "RAGApi"),
    }


def _service_names_sot(cfg: Dict) -> Dict[str, str]:
    """Prefer service_endpoints.json windows_services; fall back to config.json mapping."""
    allow = _windows_services_allowlist()
    base = _service_names(cfg)
    # allowlist keys: fastapi, llama, llama_secondary, ocr, rag, officestack, etc.
    for k, v in allow.items():
        if k in {"fastapi", "llama", "llama_secondary", "ocr", "rag"}:
            base[k] = v
    return base


def _parse_port_from_url(url: Optional[str], default: int) -> int:
    if not url or not str(url).strip():
        return default
    try:
        u = str(url).strip()
        if "://" in u:
            host_port = u.split("://", 1)[1].split("/", 1)[0]
        else:
            host_port = u.split("/", 1)[0]
        if ":" in host_port:
            return int(host_port.rsplit(":", 1)[1])
    except Exception:
        # Malformed URL -> fall through and report the default port; safe parse
        # guard (a bad enum can't abort the services status computation).
        pass
    return default


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
            "FastAPIServer": "start_fastapi_server_external.bat",
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
                            # Best-effort PID parse from the tasklist scan; on
                            # failure fall through to the next fallback strategy.
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
    names = _service_names_sot(cfg)

    fastapi_port = int(os.environ.get("FASTAPI_PORT", "7860"))
    text_url = str(
        os.environ.get("LLM_QA_TEXT_URL")
        or os.environ.get("NOTEGEN_URL_PRIMARY")
        or "http://127.0.0.1:8004"
    )
    text_port = _parse_port_from_url(text_url, 8004)
    vision_url = str(
        os.environ.get("LLM_QA_VISION_URL")
        or os.environ.get("OCR_URL_PRIMARY")
        or "http://127.0.0.1:8001"
    )
    vision_port = _parse_port_from_url(vision_url, 8001)

    asr_primary_port = _parse_port_from_url(os.environ.get("ASR_URL"), 8095)
    asr_fb_url = os.environ.get("ASR_URL_FALLBACK", "") or ""
    asr_fb_port = _parse_port_from_url(asr_fb_url, 8096)

    rag_port = _parse_port_from_url(os.environ.get("RAG_URL"), 8007)
    searxng_port = _parse_port_from_url(os.environ.get("SEARXNG_URL"), 8083)

    def _finalize(st: Dict) -> None:
        # Match prior behavior: open port implies "up" even if Windows reports stopped.
        try:
            if st.get("reachable") and st.get("status") != "running":
                st["status"] = "running"
                prev = (st.get("note") or "").strip()
                st["note"] = ("reachable — " + prev) if prev else "reachable"
        except Exception:
            # Best-effort status enrichment (same as services row builder): the
            # finalize merge must not abort the whole services status response.
            pass

    services: Dict[str, Dict] = {}

    # FastAPI
    s = _service_status_win(names.get("fastapi") or "")
    s.update(
        {
            "id": "fastapi",
            "display": "FastAPI (API)",
            "port": fastapi_port,
            "reachable": _port_status("127.0.0.1", fastapi_port),
        }
    )
    _finalize(s)
    services["fastapi"] = s
    fastapi_reachable = bool(s.get("reachable"))

    # Shared DeepSeek text backend. DreamCision disables thinking per request.
    s = _service_status_win(names.get("llama") or "")
    s.update(
        {
            "id": "text_llm",
            "display": "DeepSeek text backend",
            "port": text_port,
            "reachable": _port_status("127.0.0.1", text_port),
            "note": "DreamCision requests force thinking off",
        }
    )
    _finalize(s)
    services["text_llm"] = s

    # Shared Qwen visual evidence and OCR backend.
    s = _service_status_win(names.get("ocr") or "")
    s.update(
        {
            "id": "vision_llm",
            "display": "Qwen vision/OCR backend",
            "port": vision_port,
            "reachable": _port_status("127.0.0.1", vision_port),
            "note": "Visual evidence extraction and OCR",
        }
    )
    _finalize(s)
    services["vision_llm"] = s

    # ASR primary
    s = _service_status_win("")
    s.update(
        {
            "id": "asr_primary",
            "display": "ASR (primary)",
            "port": asr_primary_port,
            "reachable": _port_status("127.0.0.1", asr_primary_port),
        }
    )
    _finalize(s)
    services["asr_primary"] = s

    # ASR fallback
    s = _service_status_win("")
    s.update(
        {
            "id": "asr_fallback",
            "display": "ASR (fallback)",
            "port": asr_fb_port,
            "reachable": _port_status("127.0.0.1", asr_fb_port),
        }
    )
    if not asr_fb_url.strip():
        s["note"] = (s.get("note") or "") + ("; " if s.get("note") else "") + "ASR_URL_FALLBACK unset (probing default port)"
    _finalize(s)
    services["asr_fallback"] = s

    # In-app OCR proxy (routes through FastAPI; no separate listener)
    s = _service_status_win("")
    s.update(
        {
            "id": "ocr_in_app",
            "display": "OCR (in-app proxy)",
            "port": None,
            "port_hint": str(fastapi_port),
            "reachable": fastapi_reachable,
            "status": "running" if fastapi_reachable else "unreachable",
            "note": "Internal route via FastAPI on port " + str(fastapi_port),
        }
    )
    services["ocr_in_app"] = s

    # RAG
    s = _service_status_win(names.get("rag") or "")
    s.update(
        {
            "id": "rag",
            "display": "RAG API",
            "port": rag_port,
            "reachable": _port_status("127.0.0.1", rag_port),
        }
    )
    _finalize(s)
    services["rag"] = s

    s = _service_status_win("")
    s.update(
        {
            "id": "searxng",
            "display": "SearXNG web evidence",
            "port": searxng_port,
            "reachable": _port_status("127.0.0.1", searxng_port),
        }
    )
    _finalize(s)
    services["searxng"] = s

    # PCHost proxy from service_endpoints office_stack_processes.
    services["notes_proxy"] = _pchost_stack_proxy_row("notes_proxy", "PCHost (notes proxy)", "notes_proxy")

    return {"services": services}


@router.post("/services/{service_id}/{action}")
@router.post("/services/{service_id}/{action}/")
def service_control(service_id: str, action: str) -> Dict[str, Any]:
    """
    Start/stop/restart a Windows/NSSM service mapped in service_endpoints.json.
    Hard-gated by ADMIN_SERVICE_CONTROL_ENABLED=1.
    """
    if not _service_control_enabled():
        raise HTTPException(
            status_code=403,
            detail="Service control disabled. Set ADMIN_SERVICE_CONTROL_ENABLED=1 in the FastAPI environment to enable start/stop.",
        )
    sid = (service_id or "").strip()
    act = (action or "").strip().lower()
    if act not in {"start", "stop", "restart"}:
        raise HTTPException(status_code=400, detail="Unsupported action. Use start|stop|restart.")

    if sid == "notes_proxy":
        raise HTTPException(
            status_code=400,
            detail="notes_proxy is status-only from this API; manage PCHost or NSSM outside these actions.",
        )

    allow = _windows_services_allowlist()
    svc_name = allow.get(sid)
    if not svc_name:
        # Back-compat: allow the legacy config.json mapping for core services only.
        cfg = _load_cfg()
        legacy = _service_names(cfg)
        if sid in legacy and legacy.get(sid):
            svc_name = legacy.get(sid) or ""
        else:
            raise HTTPException(
                status_code=400,
                detail=f"Service '{sid}' is not allowed. Add it under windows_services in service_endpoints.json.",
            )

    ok, out = _service_action_win(str(svc_name), act)
    return {"ok": ok, "service_id": sid, "service_name": svc_name, "action": act, "output": out}


@router.get("/services/allowlist")
@router.get("/services/allowlist/")
def service_control_allowlist() -> Dict[str, Any]:
    return {
        "control_enabled": _service_control_enabled(),
        "windows_services": _windows_services_allowlist(),
        "note": "To control services, add windows_services mappings to service_endpoints.json and set ADMIN_SERVICE_CONTROL_ENABLED=1 for FastAPI.",
    }


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
# Use start_llama_server.bat, start_ocr_server.bat, start_fastapi_server_external.bat


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
