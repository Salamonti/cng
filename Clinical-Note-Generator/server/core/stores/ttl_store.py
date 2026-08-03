import threading
import time
from typing import Dict, Generic, Optional, TypeVar

K = TypeVar("K")
V = TypeVar("V")


class TTLStore(Generic[K, V]):
    _sweep_interval = 60  # seconds between eviction sweeps

    def __init__(self, ttl_seconds: int = 86400, max_entries: Optional[int] = None):
        self._ttl_seconds = int(ttl_seconds)
        self._max_entries = max_entries
        self._lock = threading.RLock()
        self._values: Dict[K, V] = {}
        self._timestamps: Dict[K, float] = {}
        self._sweep_timer: Optional[threading.Timer] = None
        self._start_sweep()

    def _start_sweep(self):
        """Start a periodic background timer that evicts expired entries."""
        if self._sweep_timer is not None and self._sweep_timer.is_alive():
            return  # already running
        self._sweep_timer = threading.Timer(self._sweep_interval, self._sweep_loop)
        self._sweep_timer.daemon = True
        self._sweep_timer.start()

    def _sweep_loop(self):
        """Called periodically; evict expired entries then reschedule."""
        try:
            self.evict_expired()
        except Exception:
            pass  # don't crash the daemon thread
        self._start_sweep()

    def _is_expired(self, key: K, now: Optional[float] = None) -> bool:
        ts = self._timestamps.get(key)
        if ts is None:
            return True
        if now is None:
            now = time.time()
        return (now - ts) > self._ttl_seconds

    def put(self, key: K, value: V) -> None:
        with self._lock:
            if self._max_entries and len(self._values) >= self._max_entries:
                self._evict_oldest_locked()
            self._values[key] = value
            self._timestamps[key] = time.time()

    def _evict_oldest_locked(self) -> int:
        """Evict oldest entries when max_entries is hit. Caller must hold _lock.

        Drops enough entries (25% of current count, min 1) to bring the store
        back under the limit, targeting TTL-expired entries first, then
        strictly oldest-by-timestamp.
        """
        removed = 0
        to_remove = max(len(self._values) // 4, 1)

        # First pass: evict expired
        now = time.time()
        for k in list(self._values.keys()):
            if removed >= to_remove:
                break
            if self._is_expired(k, now=now):
                self._values.pop(k, None)
                self._timestamps.pop(k, None)
                removed += 1

        # Second pass: evict oldest by timestamp
        if removed < to_remove:
            by_time = sorted(self._timestamps.items(), key=lambda x: x[1])
            for k, _ in by_time[removed:]:
                if removed >= to_remove:
                    break
                self._values.pop(k, None)
                self._timestamps.pop(k, None)
                removed += 1

        return removed

    def get(self, key: K, default: Optional[V] = None) -> Optional[V]:
        with self._lock:
            if key not in self._values:
                return default
            if self._is_expired(key):
                self._values.pop(key, None)
                self._timestamps.pop(key, None)
                return default
            return self._values.get(key, default)

    def delete(self, key: K) -> None:
        with self._lock:
            self._values.pop(key, None)
            self._timestamps.pop(key, None)

    def evict_expired(self) -> int:
        now = time.time()
        removed = 0
        with self._lock:
            stale = [k for k in self._values.keys() if self._is_expired(k, now=now)]
            for key in stale:
                self._values.pop(key, None)
                self._timestamps.pop(key, None)
                removed += 1
        return removed

    # Dict-like compatibility used by existing notes code
    def __setitem__(self, key: K, value: V) -> None:
        self.put(key, value)

    def __getitem__(self, key: K) -> V:
        value = self.get(key)
        if value is None:
            raise KeyError(key)
        return value

    def __contains__(self, key: object) -> bool:
        if key is None:
            return False
        with self._lock:
            if key not in self._values:
                return False
            if self._is_expired(key):
                self._values.pop(key, None)
                self._timestamps.pop(key, None)
                return False
            return True

    def __delitem__(self, key: K) -> None:
        self.delete(key)

    def __len__(self) -> int:
        self.evict_expired()
        with self._lock:
            return len(self._values)

    def clear(self) -> None:
        with self._lock:
            self._values.clear()
            self._timestamps.clear()
