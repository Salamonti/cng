# server/metrics.py
import csv
import os
import statistics
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Deque, Dict, List, Optional, Tuple


def _ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


@dataclass
class RouteStats:
    total: int = 0
    ok2xx: int = 0
    client4xx: int = 0
    server5xx: int = 0
    latencies_ms: Deque[float] = field(default_factory=lambda: deque(maxlen=500))

    def record(self, status: int, ms: float) -> None:
        self.total += 1
        if 200 <= status < 300:
            self.ok2xx += 1
        elif 400 <= status < 500:
            self.client4xx += 1
        elif status >= 500:
            self.server5xx += 1
        self.latencies_ms.append(ms)

    def snapshot(self) -> Dict:
        data = list(self.latencies_ms)
        p50 = statistics.median(data) if data else None
        p95 = statistics.quantiles(data, n=20)[18] if len(data) >= 20 else (max(data) if data else None)
        return {
            "total": self.total,
            "2xx": self.ok2xx,
            "4xx": self.client4xx,
            "5xx": self.server5xx,
            "p50_ms": round(p50, 2) if p50 is not None else None,
            "p95_ms": round(p95, 2) if p95 is not None else None,
        }


class Metrics:
    def __init__(self, logs_dir: str):
        self.start_time = time.time()
        self.logs_dir = logs_dir
        _ensure_dir(self.logs_dir)
        self.http_csv = os.path.join(self.logs_dir, "http_requests.csv")
        self._lock = threading.Lock()
        self.routes: Dict[str, RouteStats] = defaultdict(RouteStats)
        # Concurrency tracking
        self._active_requests: int = 0
        self._peak_active: int = 0
        # Specialized metrics
        self.last_models: Dict[str, Optional[str]] = {"llm": None, "whisper": None}
        self.note_recent: Deque[Tuple[float, int]] = deque(maxlen=50)  # (duration_sec, tokens)
        self.ocr_recent: Deque[Tuple[float, float]] = deque(maxlen=50)  # (duration_sec, confidence)

    def inc_active(self) -> None:
        with self._lock:
            self._active_requests += 1
            if self._active_requests > self._peak_active:
                self._peak_active = self._active_requests

    def dec_active(self) -> None:
        with self._lock:
            if self._active_requests > 0:
                self._active_requests -= 1

    def record_http(self, method: str, path: str, status: int, ms: float, in_bytes: int, out_bytes: int) -> None:
        # Normalize path to route root (/ocr, /asr_*, etc.)
        route = path.split("?")[0]
        with self._lock:
            self.routes[route].record(status, ms)
            self._append_http_csv(method, route, status, ms, in_bytes, out_bytes)

    def _append_http_csv(self, method: str, route: str, status: int, ms: float, in_b: int, out_b: int) -> None:
        new_file = not os.path.exists(self.http_csv)
        with open(self.http_csv, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if new_file:
                w.writerow(["ts", "method", "route", "status", "latency_ms", "in_bytes", "out_bytes"])
            w.writerow([int(time.time()), method, route, status, round(ms, 2), in_b, out_b])

    def record_note(self, duration_sec: float, tokens: int, model: Optional[str]) -> None:
        with self._lock:
            self.note_recent.append((duration_sec, tokens))
            if model:
                self.last_models["llm"] = model

    def record_ocr(self, duration_sec: float, confidence: float) -> None:
        with self._lock:
            self.ocr_recent.append((duration_sec, confidence))

    def snapshot(self) -> Dict:
        uptime = int(time.time() - self.start_time)
        per_route = {k: v.snapshot() for k, v in self.routes.items()}
        # tokens/sec
        note_tps = None
        if self.note_recent:
            # Approximate tokens/sec using last N
            tot_tokens = sum(t for _, t in self.note_recent)
            tot_time = sum(d for d, _ in self.note_recent) or 1.0
            note_tps = round(tot_tokens / tot_time, 2)

        # GPU VRAM usage (if NVML available)
        vram = None
        gpu_stats = self._collect_gpu_stats()
        if gpu_stats:
            vram = {"primary": gpu_stats[0], "all": gpu_stats}

        return {
            "uptime_sec": uptime,
            "routes": per_route,
            "models": self.last_models,
            "tokens_per_sec": note_tps,
            "vram": vram,
            "active_requests": self._active_requests,
            "peak_active_requests": self._peak_active,
        }

    def _collect_gpu_stats(self) -> Optional[List[Dict[str, float]]]:
        try:
            import pynvml  # type: ignore

            pynvml.nvmlInit()
            count = pynvml.nvmlDeviceGetCount()
            stats: List[Dict[str, float]] = []
            for idx in range(count):
                handle = pynvml.nvmlDeviceGetHandleByIndex(idx)
                mem = pynvml.nvmlDeviceGetMemoryInfo(handle)
                stats.append(
                    {
                        "index": idx,
                        "used_gb": round(mem.used / 1024**3, 2),
                        "total_gb": round(mem.total / 1024**3, 2),
                    }
                )
            pynvml.nvmlShutdown()
            return stats
        except Exception:
            return None


# Global metrics instance (created in app startup)
metrics: Optional[Metrics] = None
