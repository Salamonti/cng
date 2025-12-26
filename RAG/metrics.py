# C:\RAG\metrics.py
from __future__ import annotations

import contextlib
import contextvars
import csv
import datetime as _dt
import os
import time
from typing import Any, Dict, Iterable, Optional

_CURRENT_METRICS: contextvars.ContextVar[Optional["RequestMetrics"]] = contextvars.ContextVar(
    "current_request_metrics", default=None
)


class RequestMetrics:
    """Lightweight per-request metrics recorder with lap timings & counters."""

    DEFAULT_LAP_ORDER = ["embed_query", "vector_search", "bm25_search", "hybrid_merge", "build_prompt", "ttfb_llm"]
    DEFAULT_COUNTER_ORDER = [
        "retrieved_k",
        "unique_docs",
        "sources_diversity",
        "coverage_hits",
        "overlap_tokens",
        "score_mean",
        "score_max",
        "score_min",
        "specialty_mean_scores",
        "year_span",
    ]

    def __init__(self, query: str, top_k: int, request_id: str = "", log_path: Optional[str] = None) -> None:
        self.query = (query or "").strip()
        self.top_k = int(top_k)
        self.request_id = request_id or ""
        self.log_path = log_path or os.path.join("logs", "request_metrics.csv")
        self.started_at = time.perf_counter()
        self.timestamp = _dt.datetime.utcnow().isoformat()
        self.measurements: Dict[str, float] = {}
        self.counters: Dict[str, Any] = {}
        self.total_elapsed: Optional[float] = None

    # ------------------------------------------------------------------
    # Context helpers
    # ------------------------------------------------------------------
    @contextlib.contextmanager
    def activate(self) -> Iterable["RequestMetrics"]:
        """Bind this metrics instance to the current context for downstream helpers."""
        token = _CURRENT_METRICS.set(self)
        try:
            yield self
        finally:
            _CURRENT_METRICS.reset(token)

    @contextlib.contextmanager
    def measure(self, name: str) -> Iterable[None]:
        """Record wall time spent inside the managed block."""
        start = time.perf_counter()
        try:
            yield
        finally:
            elapsed = time.perf_counter() - start
            self.measurements[name] = self.measurements.get(name, 0.0) + elapsed

    def set_measurement(self, name: str, seconds: float) -> None:
        self.measurements[name] = float(seconds)

    def record_counter(self, name: str, value: Any) -> None:
        self.counters[name] = value

    def increment_counter(self, name: str, delta: float) -> None:
        self.counters[name] = self.counters.get(name, 0) + delta

    def finish(self) -> float:
        self.total_elapsed = time.perf_counter() - self.started_at
        return self.total_elapsed

    # ------------------------------------------------------------------
    # Serialization helpers
    # ------------------------------------------------------------------
    def _lap_keys(self) -> Iterable[str]:
        seen = set()
        for key in self.DEFAULT_LAP_ORDER:
            seen.add(key)
            yield key
        for key in self.measurements.keys():
            if key not in seen:
                yield key

    def _counter_keys(self) -> Iterable[str]:
        seen = set()
        for key in self.DEFAULT_COUNTER_ORDER:
            if key in self.counters:
                seen.add(key)
                yield key
        for key in self.counters.keys():
            if key not in seen:
                yield key

    def to_row(self) -> Dict[str, Any]:
        total = self.total_elapsed if self.total_elapsed is not None else (time.perf_counter() - self.started_at)
        row: Dict[str, Any] = {
            "timestamp": self.timestamp,
            "query": self.query,
            "top_k": self.top_k,
            "request_id": self.request_id,
        }
        for key in self._lap_keys():
            value = self.measurements.get(key)
            row[f"{key}_ms"] = round(value * 1000, 3) if value is not None else ""
        row["total_ms"] = round(total * 1000, 3)
        for key in self._counter_keys():
            row[key] = self.counters.get(key, "")
        return row

    def log(self) -> None:
        row = self.to_row()
        path = self.log_path
        os.makedirs(os.path.dirname(path), exist_ok=True)
        file_exists = os.path.exists(path)
        fieldnames = list(row.keys())
        if file_exists:
            # Preserve existing header ordering by reading first line if possible
            try:
                with open(path, "r", encoding="utf-8") as existing:
                    header = existing.readline().strip().split(",")
                    if header and set(header) == set(fieldnames):
                        fieldnames = header
            except Exception:
                pass
        with open(path, "a", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            if not file_exists:
                writer.writeheader()
            writer.writerow({k: row.get(k, "") for k in fieldnames})

    # ------------------------------------------------------------------
    # Static helpers
    # ------------------------------------------------------------------
    @classmethod
    def current(cls) -> Optional["RequestMetrics"]:
        return _CURRENT_METRICS.get()


def get_current_metrics() -> Optional[RequestMetrics]:
    return RequestMetrics.current()


@contextlib.contextmanager
def maybe_measure(name: str):
    metrics = get_current_metrics()
    if metrics:
        with metrics.measure(name):
            yield
    else:
        with contextlib.nullcontext():
            yield
