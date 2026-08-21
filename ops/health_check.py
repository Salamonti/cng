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

import os
import shutil
import sys
import urllib.request
from dataclasses import dataclass
from typing import Callable, List

# The alert brain (dreamcision-healthcheck-alert-brain.sh) reads this file to
# learn WHICH checks failed, so the Telegram message can say "rag: HTTP 503"
# instead of a generic "health check failed". Presence = failed, absence = OK.
FAILURE_DETAILS_FILE = "/run/dreamcision-healthcheck/failure-details"


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


def check_llm_backend_8004() -> CheckResult:
    # multimodal-sole: Qwen3.8-27B NVFP4 (text+vision) on :8004.
    ok, detail = _http_ok("http://127.0.0.1:8004/v1/models")
    return CheckResult("llm_backend_8004", ok, detail)


def check_vision_backend_8001() -> CheckResult:
    # multimodal-sole: vision is served by the same model on :8004, so :8001 is
    # not part of this topology. Kept as a no-op so the check list stays stable
    # across flip-back; re-enable the probe when returning to text-vision-split.
    return CheckResult("vision_backend_8001", True, "n/a (multimodal-sole)")


def check_asr_primary() -> CheckResult:
    # ASR on workstation RTX 5090 (userver whisper disabled for DeepSeek VRAM).
    ok, detail = _http_ok("http://127.0.0.1:8095/health")
    return CheckResult("asr_primary_local_8095", ok, detail)


def check_asr_fallback() -> CheckResult:
    ok, detail = _http_ok("http://127.0.0.1:8096/health")
    return CheckResult("asr_fallback_local_8096", ok, detail)


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
    check_llm_backend_8004,
    check_vision_backend_8001,
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
        # Write details for the alert brain (best-effort; never fail the check
        # itself because of a logging hiccup).
        try:
            os.makedirs(os.path.dirname(FAILURE_DETAILS_FILE), exist_ok=True)
            with open(FAILURE_DETAILS_FILE, "w") as f:
                f.write(f"{len(failed)}/{len(results)} check(s) failed: {', '.join(r.name for r in failed)}\n")
                for r in failed:
                    f.write(f"- {r.name}: {r.detail}\n")
        except OSError:
            pass
        return 1

    # Healthy: clear any stale failure details so the alert brain sees "OK".
    try:
        if os.path.exists(FAILURE_DETAILS_FILE):
            os.unlink(FAILURE_DETAILS_FILE)
    except OSError:
        pass

    print(f"\nAll {len(results)} checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
