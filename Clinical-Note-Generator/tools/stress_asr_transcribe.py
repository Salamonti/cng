#!/usr/bin/env python3
"""
Live stress test: full-file POST /api/transcribe_diarized.

Defaults mirror typical clinical load:
  • WAV lengths from 3 s to 66 s (linear spacing across all requests)
  • At most 8 concurrent HTTP posts (e.g. 11 requests → 3 wait for a slot)

Requires a running FastAPI instance and a valid bearer token.

Examples:
  set API_BASE=http://127.0.0.1:7860/api
  set AUTH_TOKEN=your_jwt
  python tools/stress_asr_transcribe.py

  python tools/stress_asr_transcribe.py --requests 16 --max-concurrent 8

Uses urllib.request (stdlib only).
"""

from __future__ import annotations

import argparse
import concurrent.futures
import io
import json
import os
import sys
import time
import wave
from pathlib import Path
from typing import List, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

MIN_SEC_DEFAULT = 3.0
MAX_SEC_DEFAULT = 66.0


def _wav_bytes(duration_sec: float) -> bytes:
    buf = io.BytesIO()
    rate = 16000
    n = max(480, int(rate * max(MIN_SEC_DEFAULT, duration_sec)))
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(b"\x00\x00" * n)
    return buf.getvalue()


def _durations_linear(n: int, lo: float, hi: float) -> List[float]:
    if n < 2:
        return [lo]
    return [lo + (hi - lo) * i / (n - 1) for i in range(n)]


def _post_full_file(
    api_base: str,
    token: str,
    idx: int,
    wav: bytes,
    timeout_sec: int,
) -> Tuple[int, int, str]:
    """POST multipart with audio only (full-file transcribe path)."""
    import urllib.error
    import urllib.request

    boundary = f"----stress{idx:x}{int(time.time() * 1000)}"
    lines: List[bytes] = [
        f"--{boundary}\r\n".encode(),
        b'Content-Disposition: form-data; name="audio"; filename="clip.wav"\r\n',
        b"Content-Type: audio/wav\r\n\r\n",
        wav,
        b"\r\n",
        f"--{boundary}--\r\n".encode(),
    ]
    body = b"".join(lines)
    url = api_base.rstrip("/") + "/transcribe_diarized"
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
    )
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            text = resp.read().decode("utf-8", errors="replace")
            dt = int((time.perf_counter() - t0) * 1000)
            return resp.status, dt, text[:400]
    except urllib.error.HTTPError as e:
        dt = int((time.perf_counter() - t0) * 1000)
        raw = e.read().decode("utf-8", errors="replace")[:400]
        return e.code, dt, raw
    except Exception as e:
        dt = int((time.perf_counter() - t0) * 1000)
        return -1, dt, str(e)[:400]


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Full-file ASR stress (max concurrent clients → rest queue)."
    )
    ap.add_argument("--api-base", default=os.environ.get("API_BASE", "http://127.0.0.1:7860/api"))
    ap.add_argument("--token", default=os.environ.get("AUTH_TOKEN", ""), help="Bearer JWT")
    ap.add_argument(
        "--requests",
        type=int,
        default=11,
        help="Total jobs (default 11 = 8 running + 3 queued when max-concurrent is 8)",
    )
    ap.add_argument(
        "--max-concurrent",
        type=int,
        default=8,
        help="Thread pool size = max simultaneous HTTP posts (default 8)",
    )
    ap.add_argument("--min-sec", type=float, default=MIN_SEC_DEFAULT)
    ap.add_argument("--max-sec", type=float, default=MAX_SEC_DEFAULT)
    args = ap.parse_args()

    if not args.token.strip():
        print("Missing AUTH_TOKEN or --token", file=sys.stderr)
        return 2

    if args.requests < 1:
        print("--requests must be >= 1", file=sys.stderr)
        return 2
    if args.max_concurrent < 1:
        print("--max-concurrent must be >= 1", file=sys.stderr)
        return 2

    durations = _durations_linear(args.requests, args.min_sec, args.max_sec)
    # Long clips need client timeouts >= server full-file budget (often 300s+).
    timeout_sec = max(420, int(args.max_sec) + 120)

    payloads = [(i, _wav_bytes(durations[i])) for i in range(len(durations))]

    print(
        json.dumps(
            {
                "api_base": args.api_base,
                "requests": len(payloads),
                "max_concurrent": args.max_concurrent,
                "min_sec": args.min_sec,
                "max_sec": args.max_sec,
                "timeout_sec_per_request": timeout_sec,
                "note": "full-file transcribe only",
            },
            indent=2,
        )
    )

    t_batch = time.perf_counter()

    def job(item):
        idx, wav = item
        return _post_full_file(args.api_base, args.token, idx, wav, timeout_sec)

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.max_concurrent) as ex:
        futs = [ex.submit(job, p) for p in payloads]
        results = [f.result() for f in concurrent.futures.as_completed(futs)]

    elapsed_ms = int((time.perf_counter() - t_batch) * 1000)
    codes = [r[0] for r in results]
    ok = sum(1 for c in codes if c == 200)
    latencies = [r[1] for r in results if r[0] == 200]

    summary = {
        "elapsed_ms_wall": elapsed_ms,
        "http_200": ok,
        "total": len(results),
        "status_histogram": {},
        "latency_ms": {
            "min": min(latencies) if latencies else None,
            "max": max(latencies) if latencies else None,
            "avg": int(sum(latencies) / len(latencies)) if latencies else None,
        },
    }
    for c in codes:
        summary["status_histogram"][str(c)] = summary["status_histogram"].get(str(c), 0) + 1

    print(json.dumps(summary, indent=2))
    if ok != len(results):
        print("Sample failures:", file=sys.stderr)
        for r in results:
            if r[0] != 200:
                print(r, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
