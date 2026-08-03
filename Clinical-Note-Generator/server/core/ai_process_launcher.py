"""Build and control Windows-native llama-server / whisper-server processes from service_endpoints.json."""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_DEFAULT_LLAMA = r"C:\Projects\llama.cpp\build\bin\release\llama-server.exe"
_DEFAULT_WHISPER = r"C:\Projects\whisper.cpp\build\bin\release\whisper-server.exe"


def parse_port_from_url(url: str) -> Optional[int]:
    if not url or not isinstance(url, str):
        return None
    u = url.strip()
    try:
        if "://" in u:
            rest = u.split("://", 1)[1].split("/", 1)[0]
        else:
            rest = u.split("/", 1)[0]
        if ":" in rest:
            return int(rest.rsplit(":", 1)[1])
    except (ValueError, IndexError):
        pass
    return None


def _bool_env(v: Any) -> bool:
    return str(v or "").strip().lower() in {"1", "true", "yes", "on"}


def _merge_env(base: Optional[Dict[str, str]], extra: Dict[str, str]) -> Dict[str, str]:
    out = dict(os.environ)
    if base:
        for k, v in base.items():
            if isinstance(k, str) and v is not None:
                out[k] = str(v)
    for k, v in extra.items():
        out[k] = v
    return out


def _resolve_executable(
    data: Dict[str, Any],
    launch: Dict[str, Any],
    kind: str,
) -> str:
    ex = str(launch.get("executable") or "").strip()
    if ex:
        return ex
    defaults = data.get("ai_binary_defaults")
    if isinstance(defaults, dict):
        if kind == "whisper":
            w = str(defaults.get("whisper_server") or "").strip()
            if w:
                return w
        elif kind == "llama":
            l = str(defaults.get("llama_server") or "").strip()
            if l:
                return l
            p = str(defaults.get("llama_puzzle_server") or "").strip()
            if p:
                return p
    if kind == "whisper":
        return os.environ.get("WHISPER_SERVER_EXE", _DEFAULT_WHISPER).strip() or _DEFAULT_WHISPER
    return os.environ.get("LLAMA_SERVER_EXE", _DEFAULT_LLAMA).strip() or _DEFAULT_LLAMA


def _sampler_cli_args(sampler: Any) -> List[str]:
    if not isinstance(sampler, dict):
        return []
    out: List[str] = []
    temp = sampler.get("temperature", sampler.get("temp"))
    if temp is not None and str(temp).strip() != "":
        out += ["--temp", str(temp)]
    mp = sampler.get("min_p")
    if mp is not None and str(mp).strip() != "":
        out += ["--min-p", str(mp)]
    tp = sampler.get("top_p")
    if tp is not None and str(tp).strip() != "":
        out += ["--top-p", str(tp)]
    tk = sampler.get("top_k")
    if tk is not None and str(tk).strip() != "":
        out += ["--top-k", str(tk)]
    return out


def build_argv_for_instance(
    data: Dict[str, Any],
    group: str,
    instance_id: str,
) -> Tuple[str, List[str], Dict[str, str], Optional[str], Optional[str]]:
    """
    Returns: executable, argv (including exe), env overrides for launch, cwd, log_file path.
    """
    if group not in {"llama_instances", "whisper_instances"}:
        raise ValueError("group must be llama_instances or whisper_instances")
    block = data.get(group)
    if not isinstance(block, dict):
        raise ValueError(f"Missing {group}")
    inst = block.get(instance_id)
    if not isinstance(inst, dict):
        raise ValueError(f"Unknown instance id {instance_id!r}")

    kind = str(inst.get("kind") or ("whisper" if group == "whisper_instances" else "llama")).strip().lower()
    if kind not in {"llama", "whisper", "custom"}:
        kind = "llama"

    launch = inst.get("launch")
    launch = launch if isinstance(launch, dict) else {}
    base_url = str(inst.get("base_url") or "").strip()
    port = parse_port_from_url(base_url)
    if port is None:
        p = inst.get("port")
        if p is not None:
            try:
                port = int(p)
            except (TypeError, ValueError):
                port = None
    if port is None:
        raise ValueError("Set base_url with a port or integer port")

    env_launch = launch.get("env")
    env_overrides: Dict[str, str] = {}
    if isinstance(env_launch, dict):
        for k, v in env_launch.items():
            if isinstance(k, str) and v is not None:
                env_overrides[k] = str(v)

    log_file = str(launch.get("log_file") or "").strip() or None
    cwd = str(launch.get("working_dir") or "").strip() or None

    raw_args = launch.get("arguments")
    if isinstance(raw_args, list) and len(raw_args) > 0:
        exe = _resolve_executable(data, launch, kind if kind != "custom" else "llama")
        argv = [exe] + [str(x) for x in raw_args]
        return exe, argv, env_overrides, cwd, log_file

    model = str(inst.get("model") or "").strip()
    if kind == "custom" or not model:
        raise ValueError("Provide launch.arguments (non-empty) or model path for auto-generated argv")

    exe = _resolve_executable(data, launch, kind)
    if kind == "whisper":
        lang = str(inst.get("language") or launch.get("language") or "en").strip() or "en"
        threads = str(inst.get("threads") or launch.get("threads") or "8").strip()
        argv = [
            exe,
            "-m",
            model,
            "-l",
            lang,
            "--host",
            "0.0.0.0",
            "--port",
            str(port),
            "-p",
            str(inst.get("whisper_processors") or launch.get("processors") or "4"),
            "--convert",
            "-t",
            threads,
        ]
        extra = inst.get("whisper_extra_args")
        if isinstance(extra, list):
            argv += [str(x) for x in extra]
        return exe, argv, env_overrides, cwd, log_file

    # llama
    mmproj = str(inst.get("mmproj") or "").strip()
    host_bind = str(inst.get("bind_host", launch.get("bind_host", "0.0.0.0"))).strip() or "0.0.0.0"
    argv: List[str] = [
        exe,
        "-m",
        model,
        "--host",
        host_bind,
        "--port",
        str(port),
    ]
    if mmproj:
        argv += ["--mmproj", mmproj]

    ctx = inst.get("context_slots", launch.get("context_slots"))
    if ctx is not None and str(ctx).strip() != "":
        argv += ["-c", str(int(ctx))]

    argv += ["-ctk", str(inst.get("ctk", launch.get("ctk") or "q8_0"))]
    argv += ["-ctv", str(inst.get("ctv", launch.get("ctv") or "q8_0"))]

    if _bool_env(inst.get("flash_attention", launch.get("flash_attention", True))):
        argv += ["-fa", "on"]

    ngl = inst.get("ngl", launch.get("ngl", 999))
    try:
        argv += ["-ngl", str(int(ngl))]
    except (TypeError, ValueError):
        argv += ["-ngl", "999"]

    if _bool_env(inst.get("mmap", launch.get("mmap", True))):
        argv.append("--mmap")
    else:
        argv.append("--no-mmap")
    if _bool_env(inst.get("direct_io", launch.get("direct_io", True))):
        argv.append("--direct-io")

    argv += _sampler_cli_args(inst.get("sampler"))

    chat_kw = inst.get("chat_template_kwargs") or launch.get("chat_template_kwargs")
    if isinstance(chat_kw, str) and chat_kw.strip():
        argv += ["--chat-template-kwargs", chat_kw.strip()]

    par = inst.get("parallel", launch.get("parallel", 2))
    try:
        argv += ["--parallel", str(int(par))]
    except (TypeError, ValueError):
        argv += ["--parallel", "2"]

    extra = inst.get("extra_llama_args")
    if isinstance(extra, list):
        argv += [str(x) for x in extra]

    return exe, argv, env_overrides, cwd, log_file


def tcp_port_listening(host: str, port: int, timeout: float = 0.15) -> bool:
    return socket_connect_probe(host, port, timeout)


def socket_connect_probe(host: str, port: int, timeout: float = 0.15) -> bool:
    import socket

    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _windows_listen_pids(port: int) -> List[int]:
    if sys.platform != "win32":
        return []
    out: List[int] = []
    try:
        r = subprocess.run(
            ["netstat", "-ano", "-p", "tcp"],
            capture_output=True,
            text=True,
            timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        text = (r.stdout or "") + "\n" + (r.stderr or "")
        for line in text.splitlines():
            if "LISTENING" not in line.upper():
                continue
            if not re.search(rf":{int(port)}\s", line):
                continue
            parts = line.split()
            if parts:
                last = parts[-1].strip()
                if last.isdigit():
                    pid = int(last)
                    if pid > 0:
                        out.append(pid)
    except Exception:
        pass
    return sorted(set(out))


def stop_listeners_on_port(port: int) -> Tuple[bool, str]:
    """Best-effort: kill processes listening on TCP port (Windows)."""
    if sys.platform != "win32":
        return False, "Stop is only implemented on Windows."
    pids = _windows_listen_pids(port)
    if not pids:
        return True, f"No LISTENING process found on port {port}."
    lines: List[str] = []
    for pid in pids:
        try:
            cr = subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                text=True,
                timeout=30,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            lines.append((cr.stdout or "") + (cr.stderr or ""))
        except Exception as e:
            lines.append(str(e))
    return True, "; ".join(lines)[:4000]


def start_instance(
    data: Dict[str, Any],
    group: str,
    instance_id: str,
) -> Tuple[bool, str, Optional[int]]:
    """
    Spawn process. Returns (ok, message, pid or None).
    """
    blk = data.get(group)
    if not isinstance(blk, dict):
        blk = {}
    inst0 = blk.get(instance_id)
    if not isinstance(inst0, dict):
        inst0 = {}

    exe, argv, env_overrides, cwd, log_file = build_argv_for_instance(data, group, instance_id)
    port = parse_port_from_url(str(inst0.get("base_url") or ""))
    if port is None and inst0.get("port") is not None:
        try:
            port = int(inst0["port"])
        except (TypeError, ValueError):
            port = None
    if port is not None and tcp_port_listening("127.0.0.1", port):
        return False, f"Port {port} already has a listener; stop it first or pick another port.", None

    log_fp = None
    try:
        if log_file:
            lp = Path(log_file)
            lp.parent.mkdir(parents=True, exist_ok=True)
            log_fp = open(lp, "a", encoding="utf-8")
        env = _merge_env(env_overrides, {})
        cwd_path = cwd or str(Path(exe).parent)
        creation = 0
        if sys.platform == "win32":
            creation = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
        kw: Dict[str, Any] = {
            "cwd": cwd_path if cwd_path else None,
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
        return True, f"Started {exe} (pid={proc.pid}). Log={log_file or 'n/a'}", proc.pid
    except Exception as e:
        return False, str(e), None
    finally:
        pass
