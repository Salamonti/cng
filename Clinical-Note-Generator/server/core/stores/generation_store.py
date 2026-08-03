import os
import threading
from typing import Any, Dict

from .ttl_store import TTLStore

_TTL = int(os.environ.get("GENERATION_STORE_TTL_SECONDS", "86400"))
# Cap patient materials cache to prevent unbounded memory growth.
# Each entry can hold up to 6 material types â€” ~20KB each at worst.
# 500 * 6 * 20KB = ~60MB worst case; typical usage is far lower.
_PM_MAX = int(os.environ.get("PATIENT_MATERIALS_STORE_MAX_ENTRIES", "500"))

_generation_cache: TTLStore[str, Dict[str, str]] = TTLStore(ttl_seconds=_TTL)
_generation_meta: TTLStore[str, Dict[str, Any]] = TTLStore(ttl_seconds=_TTL)
_consult_comment_store: TTLStore[str, Dict[str, Any]] = TTLStore(ttl_seconds=_TTL)
_order_request_store: TTLStore[str, Dict[str, Any]] = TTLStore(ttl_seconds=_TTL)

_patient_materials_store: TTLStore[str, Dict[str, Any]] = TTLStore(
    ttl_seconds=_TTL, max_entries=_PM_MAX
)

# Shared lock for cache reads/writes that must stay consistent with route handlers.
cache_lock = threading.Lock()
