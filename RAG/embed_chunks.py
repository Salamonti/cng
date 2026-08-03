# embed_chunks.py
#!/usr/bin/env python3
"""
embed_chunks.py

Purpose
- Load chunk JSONL files from ./chunks/ (output of chunking_pipeline.py).
- Encode each chunk with a Sentence-Transformers model using the project
  Embedder wrapper.
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
  python embed_chunks.py --input ./chunks --output ./embeddings \\
    --model jinaai/jina-embeddings-v5-text-nano --batch 64 --to-chroma

Notes
- This script is idempotent: re-running will overwrite files in --output.
- Deduplicates by chunk ID while streaming — only keeps the last occurrence
  per ID (most recent chunk files are processed last alphabetically).
- Logs progress every 50 batches so systemd journaling captures it.
"""
from __future__ import annotations
import argparse
import json
import sys
import time
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


def collect_and_dedup(chunk_dir: Path) -> Tuple[List[str], List[str], List[Dict]]:
    """Stream through all chunk files, deduplicating by ID (keep last occurrence).

    Returns (ids, texts, metas) with no duplicate IDs.
    Progress is logged to stderr every 100k lines so systemd captures it.
    """
    ids: List[str] = []
    texts: List[str] = []
    metas: List[Dict] = []
    seen_ids: dict[str, int] = {}  # id -> index in lists

    total_lines = 0
    dup_count = 0

    for _id, _txt, _meta in iter_chunks(chunk_dir):
        total_lines += 1

        # Log progress every 100k lines
        if total_lines % 100000 == 0:
            print(f"  Scanned {total_lines} chunk lines, {len(seen_ids)} unique IDs, {dup_count} duplicates...", file=sys.stderr, flush=True)

        # Skip empty entries
        if not _txt or not _id:
            continue

        if _id in seen_ids:
            # Replace the existing entry (keep last / most recent file)
            idx = seen_ids[_id]
            ids[idx] = _id
            texts[idx] = _txt
            metas[idx] = _meta
            dup_count += 1
        else:
            seen_ids[_id] = len(ids)
            ids.append(_id)
            texts.append(_txt)
            metas.append(_meta)

    print(f"  Collected {total_lines} total chunk lines -> {len(ids)} unique, {dup_count} duplicates removed", file=sys.stderr, flush=True)
    return ids, texts, metas


def encode_batches(emb: Embedder, texts: List[str], batch: int) -> np.ndarray:
    """Encode texts in batches with progress logging every 50 batches."""
    vecs: List[np.ndarray] = []
    total_batches = (len(texts) + batch - 1) // batch
    start_time = time.time()

    for i in range(0, len(texts), batch):
        part = texts[i:i+batch]
        enc = emb.encode(part)  # list of np arrays (float32, normalized)
        vecs.extend(enc)

        batch_num = i // batch + 1
        if batch_num % 50 == 0 or batch_num == total_batches:
            elapsed = time.time() - start_time
            rate = batch_num / elapsed if elapsed > 0 else float('inf')
            eta = (total_batches - batch_num) / rate if rate > 0 else 0
            print(f"  Encoding: batch {batch_num}/{total_batches} "
                  f"({i}/{len(texts)} chunks) "
                  f"rate={rate:.1f} batch/s "
                  f"eta={eta:.0f}s", file=sys.stderr, flush=True)

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

    # Chroma has a max batch size — split into chunks
    MAX_BATCH = 5000
    for i in range(0, len(ids), MAX_BATCH):
        batch_ids = ids[i:i+MAX_BATCH]
        batch_texts = texts[i:i+MAX_BATCH]
        batch_metas = metas[i:i+MAX_BATCH]
        batch_embs = embs[i:i+MAX_BATCH].tolist()
        col.add(ids=batch_ids,
                documents=batch_texts,
                metadatas=batch_metas,
                embeddings=batch_embs)
        print(f"  Upserted batch {i//MAX_BATCH + 1}: {len(batch_ids)} items")


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
    model_name = args.model or cfg.get("embedding_model", "jinaai/jina-embeddings-v5-text-nano")

    # Stream chunks and deduplicate by ID (keep last occurrence per ID)
    print("Collecting and deduplicating chunks...", file=sys.stderr, flush=True)
    ids, texts, metas_raw = collect_and_dedup(Path(args.input))

    if not ids:
        raise SystemExit(f"No chunks found in {args.input}")

    # Sanitize metadata to primitives and aligned keys
    metas = sanitize_metas(metas_raw)

    # Encode
    device = os.environ.get("EMBEDDER_DEVICE", "auto")
    print(f"Loading embedding model: {model_name} (device={device})", file=sys.stderr, flush=True)
    emb = Embedder(model_name)

    print(f"Encoding {len(texts)} unique chunks in batches of {args.batch}…", file=sys.stderr, flush=True)
    embs = encode_batches(emb, texts, batch=args.batch)
    assert embs.shape[0] == len(ids), f"Embedding count mismatch: {embs.shape[0]} vs {len(ids)} ids"

    # Save portable artifacts
    out_dir = Path(args.output)
    cfg_out = {**cfg, "embedding_model": model_name, "input_dir": str(Path(args.input).resolve())}
    save_portable(ids, metas, embs, out_dir, cfg_out)
    print(f"Saved embeddings to {out_dir} (N={len(ids)}, dim={embs.shape[1] if embs.size else 0})", file=sys.stderr, flush=True)

    # Optional: write to Chroma as well
    if args.to_chroma:
        if not cfg.get("persist_directory"):
            raise SystemExit("settings.yaml missing 'persist_directory' for Chroma persistence")
        print("Upserting into Chroma collection…", file=sys.stderr, flush=True)
        upsert_chroma(ids, metas, texts, embs, cfg)
        print("Chroma upsert complete.", file=sys.stderr, flush=True)


if __name__ == "__main__":
    main()
