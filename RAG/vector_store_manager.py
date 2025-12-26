# C:\RAG\vector_store_manager.py
#!/usr/bin/env python3
"""
vector_store_manager.py

Purpose
- Load precomputed text embeddings from ./embeddings/ and store them in a ChromaDB persistent collection.
- Maintain metadata to allow filtering by specialty and timestamp.
- Provide hybrid search (semantic + keyword BM25) via the existing retriever.
- Offer a small CLI for ingesting, verifying counts, and ad-hoc searching.

Accepted embedding file formats in ./embeddings/
1) JSONL: one JSON object per line with keys: {"id", "text", "embedding", "metadata"}
2) NPZ: numpy .npz with arrays: ids (str[]), texts (str[]), embeddings (float32[n, d]), metadatas (list[dict])

Examples
- Ingest all files under ./embeddings/:  python vector_store_manager.py ingest --reset
- Count vectors in collection:          python vector_store_manager.py count
- Search with filters:                  python vector_store_manager.py search --q "acute COPD" --k 5 --specialty pulmonology --start 2022-01-01

Notes
- Chroma runs in-process with a persistent directory from settings.yaml.
- Hybrid search uses retriever.search (dense cosine + BM25) already implemented in this project.
"""
from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple, Set

import numpy as np
import yaml

# Project imports
from store import get_client, get_collection
from utils_meta import sanitize_metas
from retriever import search as hybrid_search_impl
from embedder import Embedder
from bm25_index import warm_bm25

# -----------------------------
# Helpers to load embeddings
# -----------------------------

def _load_jsonl(fp: Path) -> Tuple[List[str], List[str], List[List[float]], List[Dict[str, Any]]]:
    ids: List[str] = []
    texts: List[str] = []
    embs: List[List[float]] = []
    metas: List[Dict[str, Any]] = []
    with fp.open("r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            obj = json.loads(line)
            ids.append(str(obj["id"]))
            texts.append(str(obj["text"]))
            embs.append(list(obj["embedding"]))
            meta = dict(obj.get("metadata", {}))
            _ensure_core_meta(meta)
            metas.append(meta)
    return ids, texts, embs, metas


def _load_npz(fp: Path) -> Tuple[List[str], List[str], List[List[float]], List[Dict[str, Any]]]:
    arr = np.load(fp, allow_pickle=True)
    ids = [str(x) for x in arr["ids"].tolist()]
    texts = [str(x) for x in arr["texts"].tolist()]
    embeddings = arr["embeddings"]
    if embeddings.dtype != np.float32:
        embeddings = embeddings.astype(np.float32)
    embs = [v.tolist() for v in embeddings]
    metas_raw = arr["metadatas"].tolist()
    metas: List[Dict[str, Any]] = []
    for m in metas_raw:
        meta = dict(m)
        _ensure_core_meta(meta)
        metas.append(meta)
    return ids, texts, embs, metas


def _load_portable_dir(dir_path: Path, chunk_dir: Optional[Path] = None) -> Tuple[List[str], List[str], List[List[float]], List[Dict[str, Any]]]:
    """Load a portable embedding set (embeddings.npy + ids.jsonl + metadatas.jsonl) and reconstruct texts
    by scanning chunk JSONL files in manifest source_dirs or the provided chunk_dir.
    """
    emb_fp = dir_path / "embeddings.npy"
    ids_fp = dir_path / "ids.jsonl"
    metas_fp = dir_path / "metadatas.jsonl"
    if not (emb_fp.exists() and ids_fp.exists() and metas_fp.exists()):
        raise FileNotFoundError(f"Portable set not found in {dir_path}")

    embs_np = np.load(emb_fp)
    if embs_np.dtype != np.float32:
        embs_np = embs_np.astype(np.float32)
    embs: List[List[float]] = [row.tolist() for row in embs_np]

    ids: List[str] = []
    with ids_fp.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                ids.append(line)

    metas_raw: List[Dict[str, Any]] = []
    with metas_fp.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                m = json.loads(line)
            except Exception:
                m = {}
            metas_raw.append(m)

    # Determine chunk dirs from manifest or arg
    manifest_fp = dir_path / "manifest.json"
    scan_dirs: List[Path] = []
    if manifest_fp.exists():
        try:
            mj = json.loads(manifest_fp.read_text(encoding="utf-8"))
            for s in (mj.get("source_dirs") or []):
                try:
                    scan_dirs.append(Path(s))
                except Exception:
                    pass
        except Exception:
            pass
    if chunk_dir is not None:
        scan_dirs.append(chunk_dir)
    if not scan_dirs:
        scan_dirs.append(Path("./chunks"))

    # Build id->text map by scanning chunk jsonl files in scan_dirs
    needed: Set[str] = set(ids)
    id_to_text: Dict[str, str] = {}
    for base in scan_dirs:
        try:
            files = sorted(list(Path(base).glob("*.chunks.jsonl"))) + sorted(list(Path(base).glob("*.jsonl")))
        except Exception:
            files = []
        for fp in files:
            if not needed:
                break
            with fp.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except Exception:
                        continue
                    _id = str(obj.get("id") or "")
                    if _id and _id in needed:
                        id_to_text[_id] = obj.get("text") or ""
                        needed.discard(_id)
            if not needed:
                break
        if not needed:
            break

    texts: List[str] = [id_to_text.get(i, "") for i in ids]

    # Ensure core meta fields
    metas: List[Dict[str, Any]] = []
    for m in metas_raw:
        meta = dict(m)
        _ensure_core_meta(meta)
        metas.append(meta)

    assert len(ids) == len(embs) == len(metas), "Portable set length mismatch"
    return ids, texts, embs, metas


def _ensure_core_meta(meta: Dict[str, Any]) -> None:
    # Ensure fields used for indexing and filtering exist with reasonable defaults.
    meta.setdefault("specialty", "")
    meta.setdefault("timestamp", "")  # ISO-8601 recommended
    meta.setdefault("source", "")
    meta.setdefault("doc_id", "")
    meta.setdefault("chunk_index", 0)
    meta.setdefault("chunk_count", 0)
    meta.setdefault("word_count", 0)


# -----------------------------
# Manager
# -----------------------------

@dataclass
class ManagerConfig:
    persist_directory: str
    collection_name: str
    embedding_model: str


class VectorStoreManager:
    def __init__(self, cfg: ManagerConfig):
        self.cfg = cfg
        self.client = get_client(cfg.persist_directory)
        self.col = get_collection(self.client, cfg.collection_name)

    # ---- ingest ----
    def ingest_dir(
        self,
        emb_dir: str = "./embeddings",
        reset: bool = False,
        upsert: bool = False,
        delete_where: Optional[Dict[str, Any]] = None,
        chunk_dir: Optional[str] = None,
    ) -> int:
        emb_path = Path(emb_dir)
        assert emb_path.exists(), f"Embeddings directory not found: {emb_dir}"

        if reset:
            try:
                self.client.delete_collection(self.cfg.collection_name)
            except Exception:
                pass
            self.col = get_collection(self.client, self.cfg.collection_name)

        if delete_where:
            try:
                self.col.delete(where=delete_where)  # type: ignore
            except Exception:
                pass

        total_added = 0
        batch_ids: List[str] = []
        batch_texts: List[str] = []
        batch_embs: List[List[float]] = []
        batch_metas: List[Dict[str, Any]] = []

        # If directory looks like a single portable set, ingest it directly
        if (emb_path / "embeddings.npy").exists() and (emb_path / "ids.jsonl").exists() and (emb_path / "metadatas.jsonl").exists():
            ids, texts, embs, metas = _load_portable_dir(emb_path, Path(chunk_dir) if chunk_dir else None)
            # Chroma has a max batch size; slice into safe chunks
            MAX_BATCH = 5000
            for i in range(0, len(ids), MAX_BATCH):
                sl_ids = ids[i:i+MAX_BATCH]
                sl_txt = texts[i:i+MAX_BATCH]
                sl_emb = embs[i:i+MAX_BATCH]
                sl_met = metas[i:i+MAX_BATCH]
                total_added += self._flush_batch(sl_ids, sl_txt, sl_emb, sl_met, upsert=upsert)
            warm_bm25(self.col)
            return total_added

        for fp in sorted(emb_path.glob("**/*")):
            if fp.is_dir():
                continue
            if fp.suffix.lower() == ".jsonl":
                ids, texts, embs, metas = _load_jsonl(fp)
            elif fp.suffix.lower() == ".npz":
                ids, texts, embs, metas = _load_npz(fp)
            else:
                continue  # ignore unknown files

            batch_ids.extend(ids)
            batch_texts.extend(texts)
            batch_embs.extend(embs)
            batch_metas.extend(metas)

            # Flush periodically to avoid huge payloads
            if len(batch_ids) >= 2000:
                total_added += self._flush_batch(batch_ids, batch_texts, batch_embs, batch_metas, upsert=upsert)
                batch_ids, batch_texts, batch_embs, batch_metas = [], [], [], []

        if batch_ids:
            total_added += self._flush_batch(batch_ids, batch_texts, batch_embs, batch_metas, upsert=upsert)

        # Warm BM25 for hybrid retrieval
        warm_bm25(self.col)
        return total_added

    def _flush_batch(
        self,
        ids: List[str],
        texts: List[str],
        embs: List[List[float]],
        metas: List[Dict[str, Any]],
        upsert: bool = False,
    ) -> int:
        metas_clean = sanitize_metas(metas)
        assert len(ids) == len(texts) == len(embs) == len(metas_clean)
        if upsert and hasattr(self.col, "upsert"):
            self.col.upsert(ids=ids, documents=texts, embeddings=embs, metadatas=metas_clean)  # type: ignore[attr-defined]
        else:
            if upsert:
                try:
                    self.col.delete(ids=ids)  # type: ignore
                except Exception:
                    pass
            self.col.add(ids=ids, documents=texts, embeddings=embs, metadatas=metas_clean)  # type: ignore
        return len(ids)

    # ---- counts ----
    def count(self) -> int:
        try:
            return self.col.count()  # newer chroma
        except Exception:
            # Fallback via get
            res = self.col.get()
            ids = res.get("ids", [])
            return len(ids)

    # ---- search ----
    def hybrid_search(
        self,
        query: str,
        k: int = 5,
        specialty: Optional[str] = None,
        start: Optional[str] = None,
        end: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Hybrid search with optional post-filtering by specialty and timestamp window.
        Timestamp format is expected to be ISO-8601; lexical string compare is used.
        """
        emb = Embedder(self.cfg.embedding_model)
        hits = hybrid_search_impl(self.col, emb, query, k=max(k * 3, 10))  # over-fetch then filter

        def _ok(meta: Dict[str, Any]) -> bool:
            sp = str(meta.get("specialty", ""))
            ts = str(meta.get("timestamp", ""))
            if specialty and sp.lower() != specialty.lower():
                return False
            if start and ts and ts < start:
                return False
            if end and ts and ts > end:
                return False
            return True

        filtered = [h for h in hits if _ok(h.get("metadata", {}))]
        filtered.sort(key=lambda r: r["score"], reverse=True)
        return filtered[:k]


# -----------------------------
# CLI
# -----------------------------

def _load_cfg() -> ManagerConfig:
    cfg_raw = yaml.safe_load(open("settings.yaml", "r"))
    return ManagerConfig(
        persist_directory=cfg_raw["persist_directory"],
        collection_name=cfg_raw.get("collection_name", "medical_rag"),
        embedding_model=cfg_raw["embedding_model"],
    )


def _cmd_ingest(args: argparse.Namespace) -> None:
    cfg = _load_cfg()
    mgr = VectorStoreManager(cfg)
    delete_where: Optional[Dict[str, Any]] = None
    if args.delete_source:
        delete_where = {"source": args.delete_source}
    if args.delete_where:
        try:
            delete_where = json.loads(args.delete_where)
        except Exception:
            raise SystemExit("--delete-where must be a JSON object, e.g. '{\"source\":\"guidelines\"}'")
    added = mgr.ingest_dir(
        args.emb_dir,
        reset=args.reset,
        upsert=args.upsert,
        delete_where=delete_where,
        chunk_dir=args.chunk_dir,
    )
    print(f"Ingested {added} items into '{cfg.collection_name}' at {cfg.persist_directory}")
    print(f"Total in collection: {mgr.count()}")


def _cmd_count(args: argparse.Namespace) -> None:
    cfg = _load_cfg()
    mgr = VectorStoreManager(cfg)
    print(mgr.count())


def _cmd_search(args: argparse.Namespace) -> None:
    cfg = _load_cfg()
    mgr = VectorStoreManager(cfg)
    hits = mgr.hybrid_search(
        query=args.q,
        k=args.k,
        specialty=args.specialty,
        start=args.start,
        end=args.end,
    )
    for i, h in enumerate(hits, 1):
        meta = h.get("metadata", {})
        src = meta.get("source", "")
        sp = meta.get("specialty", "")
        ts = meta.get("timestamp", "")
        txt = h["text"][:120].replace("\n", " ") + ("…" if len(h["text"]) > 120 else "")
        print(f"{i:>2}. {h['score']:.4f} | {sp} | {ts} | {src} | {txt}")


def _build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Vector store manager for RAG")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_ing = sub.add_parser("ingest", help="Load embeddings into Chroma (jsonl, npz, or portable dir)")
    p_ing.add_argument("--emb-dir", default="./embeddings", help="Embeddings directory or file path")
    p_ing.add_argument("--reset", action="store_true", help="Drop and recreate the collection before ingest")
    p_ing.add_argument("--upsert", action="store_true", help="Use upsert (or delete+add) to avoid duplicates")
    p_ing.add_argument("--delete-source", help="Pre-delete where metadata.source == value")
    p_ing.add_argument("--delete-where", help="Pre-delete by JSON filter, e.g. '{\"source\":\"guidelines\"}'")
    p_ing.add_argument("--chunk-dir", help="Chunks directory to reconstruct texts for portable sets")
    p_ing.set_defaults(func=_cmd_ingest)

    p_cnt = sub.add_parser("count", help="Print total vector count")
    p_cnt.set_defaults(func=_cmd_count)

    p_s = sub.add_parser("search", help="Hybrid search with optional filters")
    p_s.add_argument("--q", required=True)
    p_s.add_argument("--k", type=int, default=5)
    p_s.add_argument("--specialty")
    p_s.add_argument("--start", help="Start timestamp YYYY-MM-DD or ISO-8601")
    p_s.add_argument("--end", help="End timestamp YYYY-MM-DD or ISO-8601")
    p_s.set_defaults(func=_cmd_search)

    return p


def main(argv: Optional[List[str]] = None) -> None:
    parser = _build_argparser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
