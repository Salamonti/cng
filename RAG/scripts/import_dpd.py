# scripts/import_dpd.py
#!/usr/bin/env python3
"""
End-to-end importer for Health Canada DPD tab/CSV files, mirroring import_local_pdfs.py

Steps
  1) Run DPD ETL to build a cleaned JSON array (one card per drug_id).
  2) Register in version manager (keeps current_corpus + index for pruning).
  3) Chunk that cleaned JSON only.
  4) Embed chunks to timestamped ./embeddings subfolder.
  5) Update index (upsert + prune + snapshots).

Usage
  python scripts/import_dpd.py --dir ./dpd_raw

Options
  --source-id     Value for metadata.source (default: dpd_ca)
  --dump-date     YYYYMMDD; sets metadata.version_namespace and record.date fallback
  --out           Output JSON (default: ./clean_corpus/dpd.clean.json)
  --no-register   Skip version_manager registration
  --no-chunk      Skip chunking
  --no-embed      Skip embedding
  --no-update     Skip update_index (ingest+prune+snapshot)
"""
from __future__ import annotations

import argparse
import datetime as dt
import subprocess
import sys
from pathlib import Path


def run_py(script: str, *args: str) -> None:
    cmd = [sys.executable, script, *args]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        sys.stderr.write(proc.stdout + "\n" + proc.stderr + "\n")
        raise SystemExit(f"Step failed: {' '.join(cmd)}")
    else:
        sys.stdout.write(proc.stdout)


def main() -> None:
    ap = argparse.ArgumentParser(description="Import DPD tables end-to-end")
    ap.add_argument("--dir", default="./dpd_raw", help="Directory with DPD .txt/.csv files")
    ap.add_argument("--source-id", default="dpd_ca", help="Value for metadata.source")
    ap.add_argument("--dump-date", default=None, help="Optional YYYYMMDD stamp for version_namespace")
    ap.add_argument("--out", default="./clean_corpus/dpd.clean.json", help="Output JSON for cleaned records")
    ap.add_argument("--no-register", action="store_true")
    ap.add_argument("--no-chunk", action="store_true")
    ap.add_argument("--no-embed", action="store_true")
    ap.add_argument("--no-update", action="store_true")
    args = ap.parse_args()

    in_dir = Path(args.dir)
    if not in_dir.exists():
        raise SystemExit(f"Input directory not found: {in_dir}")

    # 1) Build cleaned records via ETL
    run_py(
        "etl/dpd_etl.py",
        "--in", str(in_dir),
        "--out", str(Path(args.out)),
        "--source-id", args.source_id,
        *(["--dump-date", args.dump_date] if args.dump_date else []),
    )

    # 2) Register in version manager (keeps current_corpus + index for pruning)
    if not args.no_register:
        run_py("version_manager.py", "--input", str(Path(args.out)))

    # 3) Chunk this cleaned JSON only
    if not args.no_chunk:
        out_path = Path(args.out)
        run_py(
            "chunking_pipeline.py",
            "--input", str(out_path.parent),
            "--pattern", out_path.name,
            "--output", "./chunks",
        )

    # 4) Embed chunks to a portable folder (timestamped)
    stamp = (args.dump_date or dt.datetime.now().strftime("%Y%m%d_%H%M%S"))
    emb_dir = Path("./embeddings") / ("dpd_" + stamp)
    if not args.no_embed:
        run_py(
            "embed_chunks.py",
            "--input", "./chunks",
            "--output", str(emb_dir),
            "--batch", "64",
        )

    # 5) Update index (upsert + prune + snapshot)
    if not args.no_update:
        run_py(
            "update_index.py",
            "--emb-dir", str(emb_dir),
            "--chunk-dir", "./chunks",
            "--snapshots", "both",
        )

    print("Done.")


if __name__ == "__main__":
    main()
