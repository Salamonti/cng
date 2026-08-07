#!/usr/bin/env python3
"""P2-3: lightweight health check for things systemd's own Restart=/
OnFailure= can't see -- a process can be "active (running)" while the AI
backend it depends on is unreachable, or while the disk it writes PHI to
is nearly full. Runs on a timer (dreamcision-healthcheck.timer); a
non-zero exit here triggers the same telegram-alert@%n.service path the
core DreamCision units already use, so this reuses existing, tested
alerting infrastructure rather than a second notification mechanism.

Each check is independent and best-effort: one check erroring must not
stop the others from running, and everything gets logged (journalctl -u
dreamcision-healthcheck) so an alert message that just says "healthcheck
failed" can be turned into "which check, and why" in one command.
"""
from __future__ import annotations

import shutil
import sys
import urllib.request
from dataclasses import dataclass
from typing import Callable, List


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str


def _http_ok(url: str, timeout: float = 5.0) -> tuple[bool, str]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            if 200 <= resp.status < 300:
                return True, f"HTTP {resp.status}"
            return False, f"HTTP {resp.status}"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def check_fastapi() -> CheckResult:
    ok, detail = _http_ok("http://127.0.0.1:7860/api/health")
    return CheckResult("fastapi", ok, detail)


def check_pchost() -> CheckResult:
    ok, detail = _http_ok("http://127.0.0.1:3000/")
    return CheckResult("pchost", ok, detail)


def check_rag() -> CheckResult:
    ok, detail = _http_ok("http://127.0.0.1:8007/health")
    return CheckResult("rag", ok, detail)


def check_deepseek_text_backend() -> CheckResult:
    ok, detail = _http_ok("http://127.0.0.1:8004/v1/models")
    return CheckResult("deepseek_text_backend_8004", ok, detail)


def check_qwen_vision_backend() -> CheckResult:
    ok, detail = _http_ok("http://127.0.0.1:8001/v1/models")
    return CheckResult("qwen_vision_backend_8001", ok, detail)


def check_asr_primary() -> CheckResult:
    ok, detail = _http_ok("http://192.168.0.9:8095/health")
    return CheckResult("asr_primary_8095", ok, detail)


def check_asr_fallback() -> CheckResult:
    ok, detail = _http_ok("http://192.168.0.9:8096/health")
    return CheckResult("asr_fallback_8096", ok, detail)


def _disk_check(path: str, warn_pct: float = 85.0) -> CheckResult:
    try:
        usage = shutil.disk_usage(path)
        used_pct = 100.0 * usage.used / usage.total
        ok = used_pct < warn_pct
        detail = f"{used_pct:.1f}% used ({usage.free / (1024**3):.1f} GB free)"
        return CheckResult(f"disk_{path}", ok, detail)
    except Exception as exc:
        return CheckResult(f"disk_{path}", False, f"{type(exc).__name__}: {exc}")


def check_disk_root() -> CheckResult:
    return _disk_check("/")


def check_disk_data() -> CheckResult:
    return _disk_check("/data")


CHECKS: List[Callable[[], CheckResult]] = [
    check_fastapi,
    check_pchost,
    check_rag,
    check_deepseek_text_backend,
    check_qwen_vision_backend,
    check_asr_primary,
    check_asr_fallback,
    check_disk_root,
    check_disk_data,
]


def main() -> int:
    results = []
    for check in CHECKS:
        try:
            results.append(check())
        except Exception as exc:
            results.append(CheckResult(check.__name__, False, f"check itself raised: {exc}"))

    failed = [r for r in results if not r.ok]
    for r in results:
        status = "OK  " if r.ok else "FAIL"
        print(f"[{status}] {r.name}: {r.detail}")

    if failed:
        print(f"\n{len(failed)}/{len(results)} check(s) failed: {', '.join(r.name for r in failed)}")
        return 1

    print(f"\nAll {len(results)} checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
