import threading
import time
from typing import Dict, Generic, Optional, TypeVar

K = TypeVar("K")
V = TypeVar("V")


class TTLStore(Generic[K, V]):
    def __init__(self, ttl_seconds: int = 86400):
        self._ttl_seconds = int(ttl_seconds)
        self._lock = threading.RLock()
        self._values: Dict[K, V] = {}
        self._timestamps: Dict[K, float] = {}

    def _is_expired(self, key: K, now: Optional[float] = None) -> bool:
        ts = self._timestamps.get(key)
        if ts is None:
            return True
        if now is None:
            now = time.time()
        return (now - ts) > self._ttl_seconds

    def put(self, key: K, value: V) -> None:
        with self._lock:
            self._values[key] = value
            self._timestamps[key] = time.time()

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
