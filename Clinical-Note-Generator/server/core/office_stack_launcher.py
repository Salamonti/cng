"""Start/stop Windows processes for the office stack (PCHost node, FastAPI, RAG) from service_endpoints.json.

office_stack_processes + office_stack_order are the single source of truth. Prefer running
``python -m server.core.office_stack_supervisor`` from NSSM so all ``kind`` logic lives here only.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from server.core.ai_process_launcher import tcp_port_listening
from server.core.service_endpoints import service_endpoints_path


def _repo_root() -> Path:
    return service_endpoints_path().parent


def _clinical_root() -> Path:
    return _repo_root() / "Clinical-Note-Generator"


def _resolve_clinical_python() -> str:
    repo = _clinical_root()
    for rel in (".venv/Scripts/python.exe", "venv/Scripts/python.exe", "cenv/Scripts/python.exe"):
        p = repo / rel
        if p.is_file():
            return str(p)
    return "python"


def _ports_from_spec(spec: Dict[str, Any]) -> List[int]:
    raw = spec.get("ports") or []
    if not isinstance(raw, list):
        return []
    out: List[int] = []
    for p in raw:
        try:
            out.append(int(p))
        except (TypeError, ValueError):
            pass
    return out


def build_office_argv(
    process_id: str,
    spec: Dict[str, Any],
    data: Dict[str, Any],
) -> Tuple[str, List[str], str, List[int]]:
    """Returns executable (for cwd default), full argv, cwd, ports to probe."""
    kind = str(spec.get("kind") or "node").strip().lower()
    ports = _ports_from_spec(spec)

    if kind == "node":
        exe = str(spec.get("executable") or "").strip()
        if not exe:
            raise ValueError(f"office_stack_processes.{process_id}: missing executable")
        args = spec.get("arguments") or []
        if isinstance(args, str):
            args = [args]
        if not isinstance(args, list):
            args = []
        cwd = str(spec.get("working_dir") or "").strip() or str(_repo_root() / "PCHost")
        argv = [exe] + [str(x) for x in args]
        return exe, argv, cwd, ports

    if kind == "uvicorn_clinical":
        py = str(spec.get("python") or "").strip() or _resolve_clinical_python()
        cwd = str(spec.get("working_dir") or "").strip() or str(_clinical_root())
        fp = data.get("fastapi_port", 7860)
        try:
            fp = int(fp)
        except (TypeError, ValueError):
            fp = 7860
        if str(os.environ.get("FASTAPI_PORT", "")).strip():
            try:
                fp = int(str(os.environ["FASTAPI_PORT"]).strip())
            except ValueError:
                pass
        argv = [
            py,
            "-m",
            "uvicorn",
            "server.app:app",
            "--host",
            "0.0.0.0",
            "--port",
            str(fp),
            "--workers",
            "1",
            "--proxy-headers",
            "--forwarded-allow-ips",
            "127.0.0.1,::1",
            "--log-level",
            "info",
        ]
        return py, argv, cwd, ports

    if kind == "uvicorn_rag":
        py = str(spec.get("python") or "").strip()
        if not py:
            py = str(_repo_root() / "RAG" / "venv" / "Scripts" / "python.exe")
        if not Path(py).is_file():
            raise ValueError(f"office_stack_processes.{process_id}: python not found: {py}")
        cwd = str(spec.get("working_dir") or "").strip() or str(_repo_root() / "RAG")
        port = spec.get("port", 8007)
        try:
            port = int(port)
        except (TypeError, ValueError):
            port = 8007
        argv = [
            py,
            "-m",
            "uvicorn",
            "query_api:app",
            "--host",
            "0.0.0.0",
            "--port",
            str(port),
            "--workers",
            "2",
            "--loop",
            "asyncio",
            "--http",
            "h11",
            "--timeout-keep-alive",
            "30",
        ]
        return py, argv, cwd, ports

    raise ValueError(f"office_stack_processes.{process_id}: unknown kind {kind!r}")


def apply_office_stack_env_defaults(data: Dict[str, Any]) -> None:
    """Set os.environ for child processes (mirrors startup/start-office-stack.ps1 Apply-FastApiEnvDefaults)."""
    if not str(os.environ.get("NOTEGEN_URL_PRIMARY", "")).strip():
        os.environ["NOTEGEN_URL_PRIMARY"] = "http://127.0.0.1:8004"
    if not str(os.environ.get("OCR_URL_PRIMARY", "")).strip():
        os.environ["OCR_URL_PRIMARY"] = "http://127.0.0.1:8001"
    if not str(os.environ.get("RAG_URL", "")).strip():
        os.environ["RAG_URL"] = "http://127.0.0.1:8007"
    if not str(os.environ.get("ASR_URL", "")).strip():
        os.environ["ASR_URL"] = "http://127.0.0.1:8095"
    if not str(os.environ.get("ASR_URL_FALLBACK", "")).strip():
        os.environ["ASR_URL_FALLBACK"] = "http://127.0.0.1:8096"
    # No hardcoded ASR_API_KEY fallback here (was "notegenadmin") -- a
    # well-known default credential defeats the point of authenticating to
    # the ASR box. Leave unset if the environment doesn't provide one; the
    # request path (server/routes/asr.py) already fails closed (no
    # Authorization header) rather than falling back to a guessable value.
    if not str(os.environ.get("ASR_ENABLE_DIARIZATION", "")).strip():
        os.environ["ASR_ENABLE_DIARIZATION"] = "0"
    fp = 7860
    try:
        fp = int(data.get("fastapi_port", 7860))
    except (TypeError, ValueError):
        fp = 7860
    if not str(os.environ.get("FASTAPI_PORT", "")).strip():
        os.environ["FASTAPI_PORT"] = str(fp)
    if not str(os.environ.get("EMBEDDER_DIM", "")).strip():
        os.environ["EMBEDDER_DIM"] = "384"


def stack_listen_ports(data: Dict[str, Any]) -> List[int]:
    """All TCP ports used for office stack shutdown sweeps and pre-start cleanup."""
    ports: List[int] = []
    block = data.get("office_stack_processes")
    if isinstance(block, dict):
        for _, spec in block.items():
            if not isinstance(spec, dict):
                continue
            ports.extend(_ports_from_spec(spec))
            p = spec.get("port")
            if p is not None:
                try:
                    ports.append(int(p))
                except (TypeError, ValueError):
                    pass
    extra = data.get("stack_cleanup_ports")
    if isinstance(extra, list):
        for p in extra:
            try:
                ports.append(int(p))
            except (TypeError, ValueError):
                pass
    return sorted(set(ports))


def wait_for_tcp_listen(port: int, timeout: float = 90.0, interval: float = 2.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if tcp_port_listening("127.0.0.1", int(port)):
            return True
        time.sleep(interval)
    return False


def office_stack_order(data: Dict[str, Any]) -> List[str]:
    raw = data.get("office_stack_order")
    if isinstance(raw, list) and raw:
        return [str(x).strip() for x in raw if str(x).strip()]
    return ["notes_proxy", "fastapi", "rag"]


def office_process_status(data: Dict[str, Any]) -> Dict[str, Any]:
    block = data.get("office_stack_processes")
    if not isinstance(block, dict):
        return {}
    out: Dict[str, Any] = {}
    for pid, spec in block.items():
        if not isinstance(pid, str) or pid.startswith("_") or not isinstance(spec, dict):
            continue
        ports = _ports_from_spec(spec)
        primary = ports[0] if ports else None
        listening = bool(primary) and tcp_port_listening("127.0.0.1", int(primary))
        out[pid] = {
            "display": spec.get("display") or pid,
            "kind": spec.get("kind"),
            "ports": ports,
            "listening": listening,
        }
    return out


def start_office_process(data: Dict[str, Any], process_id: str) -> Tuple[bool, str, Optional[int]]:
    block = data.get("office_stack_processes")
    if not isinstance(block, dict):
        return False, "office_stack_processes missing in service_endpoints.json", None
    spec = block.get(process_id)
    if not isinstance(spec, dict):
        return False, f"Unknown office process {process_id!r}", None

    try:
        exe, argv, cwd, ports = build_office_argv(process_id, spec, data)
    except ValueError as e:
        return False, str(e), None

    if ports:
        for p in ports:
            if tcp_port_listening("127.0.0.1", int(p)):
                return False, f"Port {p} already has a listener; stop it first.", None

    log_file = str(spec.get("log_file") or "").strip()
    creation = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0  # type: ignore[attr-defined]
    env = os.environ.copy()
    extra = spec.get("env")
    if isinstance(extra, dict):
        for k, v in extra.items():
            if isinstance(k, str) and v is not None:
                env[k] = str(v)

    log_fp = None
    try:
        if log_file:
            lp = Path(log_file)
            lp.parent.mkdir(parents=True, exist_ok=True)
            log_fp = open(lp, "a", encoding="utf-8")
        kw: Dict[str, Any] = {
            "cwd": cwd or None,
            "env": env,
            "creationflags": creation,
        }
        if log_fp:
            kw["stdout"] = log_fp
            kw["stderr"] = subprocess.STDOUT
        else:
            kw["stdout"] = subprocess.DEVNULL
            kw["stderr"] = subprocess.DEVNULL
        proc = subprocess.Popen(argv, **kw)
        return True, f"Started {process_id} (pid={proc.pid}). Log={log_file or 'n/a'}", proc.pid
    except Exception as e:
        return False, str(e), None


def stop_office_process(data: Dict[str, Any], process_id: str) -> Tuple[bool, str]:
    from server.core.ai_process_launcher import stop_listeners_on_port

    block = data.get("office_stack_processes")
    if not isinstance(block, dict):
        return False, "office_stack_processes missing"
    spec = block.get(process_id)
    if not isinstance(spec, dict):
        return False, f"Unknown office process {process_id!r}"
    ports = _ports_from_spec(spec)
    if not ports:
        return False, f"No ports configured for {process_id}"
    msgs: List[str] = []
    for p in ports:
        ok, msg = stop_listeners_on_port(int(p))
        msgs.append(f"{p}: {msg}")
    return True, "; ".join(msgs)


def start_office_all(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    for pid in office_stack_order(data):
        ok, msg, p = start_office_process(data, pid)
        results.append({"id": pid, "ok": ok, "message": msg, "pid": p})
        if not ok:
            break
    return results


def start_office_stack_sequential(
    data: Dict[str, Any],
    *,
    apply_env: bool = True,
    wait_for_ports: bool = True,
    port_wait_timeout: float = 90.0,
) -> List[Dict[str, Any]]:
    """
    Start each office process in order; apply FastAPI env defaults before ``fastapi`` id.
    Used by the NSSM supervisor (single code path with the admin API).
    """
    results: List[Dict[str, Any]] = []
    for pid in office_stack_order(data):
        if apply_env and pid == "fastapi":
            apply_office_stack_env_defaults(data)
        ok, msg, child_pid = start_office_process(data, pid)
        results.append({"id": pid, "ok": ok, "message": msg, "pid": child_pid})
        if not ok:
            break
        if wait_for_ports and ok:
            block = data.get("office_stack_processes") or {}
            spec = block.get(pid) if isinstance(block, dict) else None
            ports = _ports_from_spec(spec) if isinstance(spec, dict) else []
            for prt in ports:
                wt = port_wait_timeout if pid == "fastapi" else 60.0
                if not wait_for_tcp_listen(int(prt), timeout=wt):
                    results.append(
                        {"id": pid, "ok": False, "message": f"Timeout waiting for port {prt}", "pid": child_pid}
                    )
                    return results
        time.sleep(3)
    return results


def stop_office_all(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    order = list(reversed(office_stack_order(data)))
    results: List[Dict[str, Any]] = []
    for pid in order:
        ok, msg = stop_office_process(data, pid)
        results.append({"id": pid, "ok": ok, "message": msg})
    return results
