# C:\RAG\scripts\import_local_pdfs.py
#!/usr/bin/env python3
"""
scripts/import_local_pdfs.py

Import a local folder of guideline PDFs into the RAG pipeline end-to-end:
  1) Extract text from PDFs and write a cleaned JSON array.
  2) Register docs with version_manager (current_corpus + index).
  3) Chunk the cleaned JSON into chunk JSONL.
  4) Embed chunks to a portable artifact folder.
  5) Update the Chroma index (upsert + prune + snapshot).

Usage
  python scripts/import_local_pdfs.py --dir ./local_guidelines

Options
  --source-id       Source label (metadata.source) [default: guidelines_local]
  --society         Optional society/organization label added to metadata
  --out             Output JSON path (default: ./clean_corpus/local_guidelines.clean.json)
  --no-register     Skip version_manager registration
  --no-chunk        Skip chunking
  --no-embed        Skip embedding
  --no-update       Skip update_index (ingest+prune+snapshot)

Dependencies
  - PyPDF2 (preferred), pdfminer.six (fallback)
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os  # noqa: F401
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional


def extract_pdf_text(path: Path) -> str:
    # Try PyPDF2 first
    try:
        import PyPDF2  # type: ignore
        text_parts: List[str] = []
        with open(path, "rb") as f:
            reader = PyPDF2.PdfReader(f)
            for p in reader.pages:
                try:
                    text_parts.append(p.extract_text() or "")
                except Exception:
                    text_parts.append("")
        text = "\n".join(text_parts).strip()
        if text:
            return text
    except Exception:
        pass
    # Fallback to pdfminer
    try:
        from pdfminer.high_level import extract_text as pdfminer_extract_text  # type: ignore
        return (pdfminer_extract_text(str(path)) or "").strip()
    except Exception:
        return ""


def file_date(path: Path) -> str:
    try:
        ts = path.stat().st_mtime
        return dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
    except Exception:
        return dt.date.today().isoformat()


def build_record(path: Path, source_id: str, society: Optional[str]) -> Dict:
    stem = path.stem
    title = stem.replace("_", " ")
    date_str = file_date(path)
    text = extract_pdf_text(path)
    return {
        "id": stem,
        "source": source_id,
        "title": title,
        "date": date_str,
        "link": "",
        "text": text,
        "metadata": {
            "evidence_level": "guideline",
            "has_results": True,
            **({"society": society} if society else {}),
        },
    }


def write_json_array(objs: List[Dict], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(objs, ensure_ascii=False, indent=2), encoding="utf-8")


def run_py(script: str, *args: str) -> None:
    cmd = [sys.executable, script, *args]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        sys.stderr.write(proc.stdout + "\n" + proc.stderr + "\n")
        raise SystemExit(f"Step failed: {' '.join(cmd)}")
    else:
        sys.stdout.write(proc.stdout)


def main():
    ap = argparse.ArgumentParser(description="Import local guideline PDFs end-to-end")
    ap.add_argument("--dir", default="./local_guidelines", help="Directory with PDF files")
    ap.add_argument("--source-id", default="guidelines_local", help="Value for metadata.source")
    ap.add_argument("--society", default=None, help="Optional society/organization label")
    ap.add_argument("--out", default="./clean_corpus/local_guidelines.clean.json", help="Output JSON for cleaned records")
    ap.add_argument("--no-register", action="store_true")
    ap.add_argument("--no-chunk", action="store_true")
    ap.add_argument("--no-embed", action="store_true")
    ap.add_argument("--no-update", action="store_true")
    args = ap.parse_args()

    base = Path.cwd()
    src_dir = Path(args.dir)
    if not src_dir.exists():
        raise SystemExit(f"Input directory not found: {src_dir}")

    # 1) Build cleaned records
    pdfs = sorted([p for p in src_dir.glob("**/*.pdf") if p.is_file()])
    records: List[Dict] = []
    for p in pdfs:
        rec = build_record(p, source_id=args.source_id, society=args.society)
        if not rec.get("text"):
            sys.stderr.write(f"Warning: empty text extracted from {p.name}\n")
        records.append(rec)
    if not records:
        raise SystemExit("No PDFs found or no text extracted")

    out_path = Path(args.out)
    write_json_array(records, out_path)
    print(f"Wrote cleaned records: {out_path} ({len(records)})")

    # 2) Register in version manager (keeps current_corpus + index for pruning)
    if not args.no_register:
        run_py("version_manager.py", "--input", str(out_path))

    # 3) Chunk this cleaned JSON only
    if not args.no_chunk:
        run_py(
            "chunking_pipeline.py",
            "--input", str(out_path.parent),
            "--pattern", out_path.name,
            "--output", "./chunks",
        )

    # 4) Embed chunks to a portable folder (timestamped)
    emb_dir = Path("./embeddings") / ("local_" + dt.datetime.now().strftime("%Y%m%d_%H%M%S"))
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

