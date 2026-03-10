# embedder.py
import os
from functools import lru_cache
from threading import Lock
from typing import List, Optional

import numpy as np
from sentence_transformers import SentenceTransformer

# Keep torchao disabled by default; device selection is handled dynamically.
os.environ.setdefault("TRANSFORMERS_NO_TORCHAO_IMPORT", "1")

_MODEL_CACHE: dict[str, SentenceTransformer] = {}
_MODEL_LOCK = Lock()

# Matryoshka output dimension for supported models (Jina v5, etc.)
# Set to None to use full native dimension.
# Supported values for Jina v5: 768 (full), 512, 256, 128, 64, 32
MATRYOSHKA_DIM: Optional[int] = int(os.environ.get("EMBEDDER_DIM", "0")) or None


def _pick_device() -> str:
    forced = (os.environ.get("EMBEDDER_DEVICE") or "").strip().lower()
    if forced in {"cpu", "cuda", "mps"}:
        return forced
    try:
        import torch  # type: ignore
        if torch.cuda.is_available():
            return "cuda"
    except Exception:
        pass
    return "cpu"


def _get_model(model_name: str) -> SentenceTransformer:
    with _MODEL_LOCK:
        model = _MODEL_CACHE.get(model_name)
        if model is None:
            model = SentenceTransformer(model_name, device=_pick_device())
            _MODEL_CACHE[model_name] = model
        return model


def _supports_matryoshka(model_name: str) -> bool:
    """Check if model supports Matryoshka (truncatable) embeddings."""
    name = model_name.lower()
    return ("jina" in name and "v5" in name) or "matryoshka" in name


def _encode_kwargs(model_name: str) -> dict:
    """Build consistent encode kwargs for all code paths."""
    kwargs = {"normalize_embeddings": True, "convert_to_numpy": True}
    if MATRYOSHKA_DIM and _supports_matryoshka(model_name):
        kwargs["output_dim"] = MATRYOSHKA_DIM
    return kwargs


@lru_cache(maxsize=512)
def _cached_single_embedding(model_name: str, text: str) -> np.ndarray:
    model = _get_model(model_name)
    vec = model.encode([text], **_encode_kwargs(model_name))[0]
    return np.asarray(vec, dtype=np.float32)


class Embedder:
    def __init__(self, model_name: str):
        self.model_name = model_name
        self.model = _get_model(model_name)

    def encode(self, texts: List[str]):
        if len(texts) == 1:
            return [_cached_single_embedding(self.model_name, texts[0])]

        embs = self.model.encode(texts, **_encode_kwargs(self.model_name))
        return [np.asarray(e, dtype=np.float32) for e in embs]
