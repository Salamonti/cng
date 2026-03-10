# C:\RAG\update_index.py
#!/usr/bin/env python3
"""
update_index.py

Purpose
- Insert new embeddings into the Chroma vector store (upsert semantics).
- Remove obsolete chunks for documents no longer present in the current corpus.
- Maintain daily and weekly snapshots of the Chroma persistence directory.
- Print an update/prune summary for observability.

Integration
- Respects settings in settings.yaml (persist_directory, collection_name, embedding_model).
- Reuses VectorStoreManager for portable artifact ingest (embeddings.npy + ids.jsonl + metadatas.jsonl).
- Prunes by comparing doc_ids present in Chroma vs IDs in ./current_corpus/*.json (from version_manager).

Usage
  python update_index.py --emb-dir ./embeddings --chunk-dir ./chunks --snapshots both

Notes
- Safe by default: performs upsert; prunes only when a doc_id is absent from current_corpus.
- Snapshots are simple filesystem copies of the Chroma persistence directory.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import yaml

from store import get_client, get_collection
from vector_store_manager import VectorStoreManager


def load_settings() -> Dict:
    p = Path("settings.yaml")
    if not p.exists():
        raise SystemExit("settings.yaml not found")
    return yaml.safe_load(p.read_text(encoding="utf-8"))


def col_counts(col) -> int:
    try:
        return int(col.count())
    except Exception:
        res = col.get()
        return len(res.get("ids", []))


def load_active_doc_ids(current_dir: Path) -> Set[str]:
    """Collect active document IDs from ./current_corpus/*.json. Only docs with a non-empty 'id'."""
    active: Set[str] = set()
    if not current_dir.exists():
        return active
    for fp in sorted(current_dir.glob("*.json")):
        try:
            obj = json.loads(fp.read_text(encoding="utf-8"))
        except Exception:
            continue
        did = str(obj.get("id") or "").strip()
        if did:
            active.add(did)
    return active


def build_docid_index(col) -> Tuple[Dict[str, List[str]], Dict[str, int]]:
    """Return (doc_id -> chunk_ids, doc_id -> chunk_count) by reading all metadatas.
    For moderate corpora only; optimize/paginate if your store grows large."""
    # Some Chroma versions do not allow 'ids' in include — request metadatas only; ids are still returned.
    res = col.get(include=["metadatas"])  # type: ignore
    ids = res.get("ids", [])
    metas = res.get("metadatas", [])
    by_doc: Dict[str, List[str]] = {}
    counts: Dict[str, int] = {}
    for cid, m in zip(ids, metas):
        doc_id = str((m or {}).get("doc_id") or "")
        if not doc_id:
            # cannot reason about archived state without a doc_id
            continue
        by_doc.setdefault(doc_id, []).append(cid)
    for k, v in by_doc.items():
        counts[k] = len(v)
    return by_doc, counts


def delete_doc_chunks(col, doc_ids: List[str]) -> int:
    """Delete all chunks for the given doc_ids; returns deleted-chunk count (best-effort)."""
    deleted = 0
    for did in doc_ids:
        try:
            # Best-effort: count first for reporting
            res = col.get(where={"doc_id": did}, include=["ids"])  # type: ignore
            cids = res.get("ids", [])
            if cids:
                deleted += len(cids)
            col.delete(where={"doc_id": did})  # type: ignore
        except Exception:
            # continue with others
            pass
    return deleted


def snapshot_chroma(persist_dir: Path, mode: str, out_root: Path) -> Optional[Path]:
    out_root.mkdir(parents=True, exist_ok=True)
    now = dt.datetime.now()
    try:
        if mode == "daily":
            name = now.strftime("%Y%m%d")
        elif mode == "weekly":
            iso = now.isocalendar()
            name = f"{iso.year}W{iso.week:02d}"
        else:
            return None
        target = out_root / name
        if target.exists():
            # keep existing snapshot; do not overwrite
            return target
        shutil.copytree(persist_dir, target)
        (target / "SNAPSHOT.json").write_text(
            json.dumps({
                "created": now.isoformat(),
                "source": str(persist_dir.resolve()),
            }, indent=2), encoding="utf-8"
        )
        return target
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser(description="Update Chroma index from embeddings, prune archived, and snapshot")
    ap.add_argument("--emb-dir", default="./embeddings", help="Embeddings directory (portable set)")
    ap.add_argument("--chunk-dir", default="./chunks", help="Chunks directory for text reconstruction if needed")
    ap.add_argument("--snapshots", choices=["none", "daily", "weekly", "both"], default="both", help="Create snapshots after update")
    ap.add_argument("--no-prune", action="store_true", help="Skip pruning archived/obsolete doc_ids")
    args = ap.parse_args()

    cfg = load_settings()
    persist_dir = Path(cfg["persist_directory"]).resolve()
    collection_name = cfg.get("collection_name", "medical_rag")

    # Prepare collection
    client = get_client(str(persist_dir))
    col = get_collection(client, collection_name)

    before = col_counts(col)

    # Ingest new embeddings (upsert semantics)
    vsm = VectorStoreManager(type("C", (), cfg) if False else type("Cfg", (), {
        "persist_directory": str(persist_dir),
        "collection_name": collection_name,
        "embedding_model": cfg.get("embedding_model", ""),
    }))
    added = vsm.ingest_dir(emb_dir=args.emb_dir, upsert=True, chunk_dir=args.chunk_dir)

    # Prune obsolete doc_ids (compare against ./current_corpus)
    pruned_docs = 0
    pruned_chunks = 0
    current_dir = Path("./current_corpus")
    if not args.no_prune and current_dir.exists():
        active_ids = load_active_doc_ids(current_dir)
        by_doc, _counts = build_docid_index(col)
        obsolete = [did for did in by_doc.keys() if did not in active_ids]
        if obsolete:
            pruned_docs = len(obsolete)
            pruned_chunks = delete_doc_chunks(col, obsolete)

    after = col_counts(col)

    # Snapshots
    snapshot_paths: List[str] = []
    snap_root = Path("./snapshots/chroma")
    if args.snapshots in ("daily", "both"):
        p = snapshot_chroma(persist_dir, "daily", snap_root)
        if p:
            snapshot_paths.append(str(p))
    if args.snapshots in ("weekly", "both"):
        p = snapshot_chroma(persist_dir, "weekly", snap_root)
        if p:
            snapshot_paths.append(str(p))

    report = {
        "collection": collection_name,
        "persist_directory": str(persist_dir),
        "added_or_upserted": int(added),
        "pruned_doc_ids": int(pruned_docs),
        "pruned_chunks": int(pruned_chunks),
        "count_before": int(before),
        "count_after": int(after),
        "snapshots": snapshot_paths,
        "timestamp": dt.datetime.now().isoformat(),
    }
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
