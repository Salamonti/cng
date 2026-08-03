#!/usr/bin/env python3
"""
ASR burn-in / stability probe.

Runs repeated requests against the FastAPI proxy (/api/transcribe_diarized) to measure:
- end-to-end latency
- HTTP status distribution
- trace id correlation (X-ASR-Trace-Id)

This does NOT require real audio. It generates a short WAV tone in-memory.

Usage (PowerShell):
  python tools/asr_burn_in.py --base http://127.0.0.1:7860 --token <BEARER> --n 30
"""

from __future__ import annotations

import argparse
import io
import statistics
import time
import wave
from typing import Dict, List, Tuple

import requests


def make_wav_bytes(sec: float = 0.5, sr: int = 16000, freq_hz: float = 440.0) -> bytes:
    import math
    import struct

    n = max(1, int(sec * sr))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        for i in range(n):
            s = int(0.2 * 32767 * math.sin(2 * math.pi * freq_hz * i / sr))
            w.writeframesraw(struct.pack("<h", s))
    return buf.getvalue()


def pct(xs: List[float], p: float) -> float:
    if not xs:
        return 0.0
    xs2 = sorted(xs)
    k = int(round((len(xs2) - 1) * p))
    return xs2[max(0, min(len(xs2) - 1, k))]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:7860", help="FastAPI base URL (no trailing /api)")
    ap.add_argument("--token", default="", help="Bearer token used by app (required unless auth is disabled)")
    ap.add_argument("--n", type=int, default=20, help="Number of requests")
    ap.add_argument("--timeout", type=float, default=60.0, help="Per-request timeout seconds")
    args = ap.parse_args()

    url = args.base.rstrip("/") + "/api/transcribe_diarized"
    wav = make_wav_bytes()

    lat_ms: List[float] = []
    by_status: Dict[int, int] = {}
    traces: List[Tuple[int, str]] = []

    for i in range(1, args.n + 1):
        files = {"audio": ("probe.wav", wav, "audio/wav")}
        data = {"language": "en"}
        headers = {}
        if args.token:
            headers["Authorization"] = f"Bearer {args.token}"
        trace_id = f"burn_{int(time.time()*1000)}_{i}"
        headers["x-asr-trace-id"] = trace_id

        t0 = time.perf_counter()
        try:
            r = requests.post(url, files=files, data=data, headers=headers, timeout=args.timeout)
            dt = (time.perf_counter() - t0) * 1000.0
            lat_ms.append(dt)
            by_status[r.status_code] = by_status.get(r.status_code, 0) + 1
            srv_trace = r.headers.get("X-ASR-Trace-Id") or r.headers.get("x-asr-trace-id") or ""
            traces.append((r.status_code, srv_trace.strip() or trace_id))
            body_preview = (r.text or "").strip().replace("\n", " ")[:120]
            print(f"[{i:02d}/{args.n}] {r.status_code} {dt:7.1f}ms trace={srv_trace or trace_id} body='{body_preview}'")
        except Exception as e:
            dt = (time.perf_counter() - t0) * 1000.0
            by_status[-1] = by_status.get(-1, 0) + 1
            print(f"[{i:02d}/{args.n}] EXC  {dt:7.1f}ms trace={trace_id} err={type(e).__name__}:{e}")

    ok = sum(v for k, v in by_status.items() if k == 200)
    total = sum(by_status.values())
    print("\nSummary")
    print(f"- url: {url}")
    print(f"- ok: {ok}/{total}")
    print(f"- status_counts: {dict(sorted(by_status.items(), key=lambda kv: kv[0]))}")
    if lat_ms:
        print(f"- latency_ms: mean={statistics.mean(lat_ms):.1f} p50={pct(lat_ms,0.50):.1f} p90={pct(lat_ms,0.90):.1f} p99={pct(lat_ms,0.99):.1f} max={max(lat_ms):.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

