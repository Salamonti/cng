# C:\RAG\embedder.py
import os
from functools import lru_cache
from threading import Lock
from typing import List

import numpy as np
from sentence_transformers import SentenceTransformer

# Keep torchao disabled by default; device selection is handled dynamically.
os.environ.setdefault("TRANSFORMERS_NO_TORCHAO_IMPORT", "1")

_MODEL_CACHE: dict[str, SentenceTransformer] = {}
_MODEL_LOCK = Lock()


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


@lru_cache(maxsize=512)
def _cached_single_embedding(model_name: str, text: str) -> np.ndarray:
    model = _get_model(model_name)
    vec = model.encode([text], normalize_embeddings=True, convert_to_numpy=True)[0]
    return np.asarray(vec, dtype=np.float32)


class Embedder:
    def __init__(self, model_name: str):
        self.model_name = model_name
        self.model = _get_model(model_name)

    def encode(self, texts: List[str]):
        if len(texts) == 1:
            return [_cached_single_embedding(self.model_name, texts[0])]
        
        # For Jina v5 models, use Matryoshka embeddings for speed
        # Can specify output_dim: 768 (full), 512, 256, 128, 64, 32
        # Smaller dimensions = faster but slightly less accurate
        model_name_lower = self.model_name.lower()
        if "jina" in model_name_lower and "v5" in model_name_lower:
            # Use 512-dim for good speed/quality balance
            embs = self.model.encode(
                texts, 
                normalize_embeddings=True, 
                convert_to_numpy=True,
                output_dim=512  # Matryoshka: 768 → 512 (33% faster)
            )
        else:
            embs = self.model.encode(texts, normalize_embeddings=True, convert_to_numpy=True)
        
        return [np.asarray(e, dtype=np.float32) for e in embs]
