# scripts/import_spl.py
#!/usr/bin/env python3
"""
import_spl.py

One-time (or manual) ingestion pipeline for FDA SPL drug labels.

Steps
  1) Run etl/spl_etl.py to convert all_spl.jsonl into cleaned documents.
  2) Register documents with version_manager (updates current_corpus + index).
  3) Chunk only the generated file (to a custom chunk directory).
  4) Embed the chunks (writing to a dedicated embeddings directory).
  5) Upsert into Chroma via update_index.py using the custom dirs.

This script does NOT tie into the automatic Monday workflow; it operates on
explicit input/output paths that you control.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def run_py(script: str, *args: str) -> None:
    cmd = [sys.executable, script, *args]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        sys.stderr.write(proc.stdout)
        sys.stderr.write(proc.stderr)
        raise SystemExit(f"Command failed: {' '.join(cmd)}")
    else:
        if proc.stdout:
            sys.stdout.write(proc.stdout)


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def main() -> None:
    ap = argparse.ArgumentParser(description="Manual importer for FDA SPL drug labels")
    ap.add_argument("--input-jsonl", default="raw_docs/fda_spl_drugs/all_spl.jsonl", help="Normalized SPL JSONL")
    ap.add_argument("--clean-json", default="clean_corpus/fda_spl.clean.json", help="Output cleaned JSON (array)")
    ap.add_argument("--chunk-dir", default="manual_ingest/fda_spl/chunks", help="Directory for chunk output")
    ap.add_argument("--emb-dir", default="manual_ingest/fda_spl/embeddings", help="Directory for embeddings")
    ap.add_argument("--skip-register", action="store_true", help="Skip version_manager registration")
    ap.add_argument("--skip-chunk", action="store_true")
    ap.add_argument("--skip-embed", action="store_true")
    ap.add_argument("--skip-update", action="store_true")
    args = ap.parse_args()

    base = Path(__file__).resolve().parent.parent
    input_jsonl = (base / args.input_jsonl).resolve()
    clean_json = (base / args.clean_json).resolve()
    chunk_dir = ensure_dir((base / args.chunk_dir).resolve())
    emb_dir = ensure_dir((base / args.emb_dir).resolve())

    if not input_jsonl.exists():
        raise SystemExit(f"Input JSONL not found: {input_jsonl}")

    # 1) Run ETL to create cleaned JSON array
    run_py(
        str(base / "etl" / "spl_etl.py"),
        "--in", str(input_jsonl),
        "--out", str(clean_json),
    )

    # 2) Register docs with version_manager (keeps current_corpus + index)
    if not args.skip_register:
        run_py(
            str(base / "version_manager.py"),
            "--input", str(clean_json),
        )

    # 3) Chunk the cleaned JSON only
    if not args.skip_chunk:
        run_py(
            str(base / "chunking_pipeline.py"),
            "--input", str(clean_json.parent),
            "--pattern", clean_json.name,
            "--output", str(chunk_dir),
        )

    # 4) Embed the chunks
    if not args.skip_embed:
        run_py(
            str(base / "embed_chunks.py"),
            "--input", str(chunk_dir),
            "--output", str(emb_dir),
        )

    # 5) Update index with the new embeddings
    if not args.skip_update:
        run_py(
            str(base / "update_index.py"),
            "--emb-dir", str(emb_dir),
            "--chunk-dir", str(chunk_dir),
            "--snapshots", "none",
        )

    print("Import complete.")


if __name__ == "__main__":
    main()
