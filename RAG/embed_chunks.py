# C:\RAG\embed_chunks.py
#!/usr/bin/env python3
"""
embed_chunks.py

Purpose
- Load chunk JSONL files from ./chunks/ (output of chunking_pipeline.py).
- Encode each chunk with a Sentence-Transformers model (BioBERT/BGE or any
  model name you pass) using the project Embedder wrapper.
- Save aligned ids, embeddings, and metadata into ./embeddings/ for portable
  reuse. Optionally write them into your Chroma collection.

Inputs
- ./chunks/*.chunks.jsonl (each line: {id, text, metadata})
- settings.yaml (for default embedding_model and chroma persist dir)

Outputs (default: ./embeddings/)
- manifest.json           (summary and config used)
- embeddings.npy          (float32, shape [N, D])
- ids.jsonl               (one id per line, aligned to row in embeddings.npy)
- metadatas.jsonl         (one JSON object per line, aligned)

Optional: --to-chroma will also persist into the configured Chroma collection.

Usage
  python embed_chunks.py --input ./chunks --output ./embeddings \
    --model BAAI/bge-small-en-v1.5 --batch 64 --to-chroma

Notes
- This script is idempotent: re-running will overwrite files in --output.
- For large corpora consider sharding (not implemented here for simplicity).
"""
from __future__ import annotations
import argparse
import json
import os  # noqa: F401
from pathlib import Path
from typing import Dict, Iterator, List, Tuple

import numpy as np
import yaml

# Project modules
from embedder import Embedder
from store import get_client, get_collection
from utils_meta import sanitize_metas


def iter_chunks(chunk_dir: Path) -> Iterator[Tuple[str, str, Dict]]:
    """Yield (id, text, metadata) from all *.chunks.jsonl files."""
    files = sorted(chunk_dir.glob("*.chunks.jsonl"))
    if not files:
        # also allow plain jsonl produced by other tools
        files = sorted(chunk_dir.glob("*.jsonl"))
    for fp in files:
        with fp.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                obj = json.loads(line)
                _id = str(obj.get("id"))
                _txt = obj.get("text") or ""
                _meta = obj.get("metadata") or {}
                yield _id, _txt, _meta


def encode_batches(emb: Embedder, texts: List[str], batch: int) -> np.ndarray:
    vecs: List[np.ndarray] = []
    for i in range(0, len(texts), batch):
        part = texts[i:i+batch]
        enc = emb.encode(part)  # list of np arrays (float32, normalized)
        vecs.extend(enc)
    if not vecs:
        return np.zeros((0, 0), dtype=np.float32)
    dim = vecs[0].shape[-1]
    arr = np.vstack(vecs).astype(np.float32)
    assert arr.shape[1] == dim
    return arr


def save_portable(ids: List[str], metas: List[Dict], embs: np.ndarray, out_dir: Path, cfg: Dict):
    out_dir.mkdir(parents=True, exist_ok=True)

    # ids
    with (out_dir / "ids.jsonl").open("w", encoding="utf-8") as f:
        for _id in ids:
            f.write(str(_id) + "\n")

    # metadatas (aligned)
    with (out_dir / "metadatas.jsonl").open("w", encoding="utf-8") as f:
        for m in metas:
            f.write(json.dumps(m, ensure_ascii=False) + "\n")

    # embeddings
    np.save(out_dir / "embeddings.npy", embs)

    # manifest
    manifest = {
        "count": len(ids),
        "dim": int(embs.shape[1] if embs.size else 0),
        "model": cfg.get("embedding_model"),
        "source_dirs": [str(Path(cfg.get("input_dir", "./chunks")).resolve())],
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def upsert_chroma(ids: List[str], metas: List[Dict], texts: List[str], embs: np.ndarray, cfg: Dict):
    client = get_client(cfg["persist_directory"])  # chroma path
    col = get_collection(client, name=cfg.get("collection_name", "medical_rag"))

    # Chroma expects python lists
    col.add(ids=ids,
            documents=texts,
            metadatas=metas,  # already sanitized below # pyright: ignore[reportArgumentType]
            embeddings=embs.tolist())


def main():
    ap = argparse.ArgumentParser(description="Embed chunks and save aligned outputs")
    ap.add_argument("--input", type=str, default="./chunks", help="Directory with *.chunks.jsonl")
    ap.add_argument("--output", type=str, default="./embeddings", help="Directory to write embeddings + metadata")
    ap.add_argument("--model", type=str, default=None, help="Sentence-Transformers model (e.g., BAAI/bge-small-en-v1.5 or BioBERT variant)")
    ap.add_argument("--batch", type=int, default=64, help="Batch size for embedding")
    ap.add_argument("--to-chroma", action="store_true", help="Also write to Chroma using settings.yaml")
    args = ap.parse_args()

    # Load config and decide model
    cfg = yaml.safe_load(open("settings.yaml", "r")) if Path("settings.yaml").exists() else {}
    model_name = args.model or cfg.get("embedding_model", "BAAI/bge-small-en-v1.5")

    # Collect chunks
    chunk_dir = Path(args.input)
    rows = list(iter_chunks(chunk_dir))
    if not rows:
        raise SystemExit(f"No chunk files found in {chunk_dir}")

    ids: List[str] = []
    texts: List[str] = []
    metas_raw: List[Dict] = []

    for _id, _txt, _meta in rows:
        if not _txt or not _id:
            continue
        ids.append(_id)
        texts.append(_txt)
        metas_raw.append(_meta)

    # Sanitize metadata to primitives and aligned keys
    metas = sanitize_metas(metas_raw)

    # Encode
    print(f"Loading embedding model: {model_name}")
    emb = Embedder(model_name)
    print(f"Encoding {len(texts)} chunks in batches of {args.batch}…")
    embs = encode_batches(emb, texts, batch=args.batch)
    assert embs.shape[0] == len(ids), "Embedding count mismatch vs ids"

    # Save portable artifacts
    out_dir = Path(args.output)
    cfg_out = {**cfg, "embedding_model": model_name, "input_dir": str(chunk_dir)}
    save_portable(ids, metas, embs, out_dir, cfg_out)
    print(f"Saved embeddings to {out_dir} (N={len(ids)}, dim={embs.shape[1] if embs.size else 0})")

    # Optional: write to Chroma as well
    if args.to_chroma:
        if not cfg.get("persist_directory"):
            raise SystemExit("settings.yaml missing 'persist_directory' for Chroma persistence")
        print("Upserting into Chroma collection…")
        upsert_chroma(ids, metas, texts, embs, cfg)
        print("Chroma upsert complete.")


if __name__ == "__main__":
    main()
