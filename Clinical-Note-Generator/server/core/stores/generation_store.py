from typing import Any, Dict

from .ttl_store import TTLStore

_generation_cache: TTLStore[str, Dict[str, str]] = TTLStore(ttl_seconds=86400)
_generation_meta: TTLStore[str, Dict[str, Any]] = TTLStore(ttl_seconds=86400)
_consult_comment_store: TTLStore[str, Dict[str, Any]] = TTLStore(ttl_seconds=86400)
_order_request_store: TTLStore[str, Dict[str, Any]] = TTLStore(ttl_seconds=86400)
