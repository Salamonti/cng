# C:\RAG\chunking_pipeline.py
#!/usr/bin/env python3
"""
Chunking pipeline for the medical RAG project.

After running clean_docs.py, this script:
  1) Loads cleaned documents (JSONL or JSON) from an input directory.
  2) Splits each document into 100–300 word chunks with light sentence awareness.
     - It avoids splitting inside enumerated lists (e.g., "1.", "-", "•", "*").
  3) Preserves and carries forward key metadata (source, year, specialty, plus any others provided).
  4) Writes JSONL chunk files into ./chunks/ (one file per input file by default).

Notes:
  - This runs offline and depends only on Python stdlib.
  - It is careful with text boundaries but keeps performance simple.

Usage examples:
  python chunking_pipeline.py --input ./cleaned --output ./chunks
  python chunking_pipeline.py --input ./cleaned --pattern "*.jsonl" --target_min 100 --target_max 300

"""
from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Tuple, Optional
from utils_meta import normalize_metadata_fields

# -----------------------------
# Helpers
# -----------------------------

LIST_BULLET_RE = re.compile(r"^\s*(?:\d+\.|[\-\u2022\*])\s+")  # 1.  -  •  *
SENTENCE_BOUNDARY_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9(\[])")
WHITESPACE_RE = re.compile(r"\s+")


def word_count(s: str) -> int:
    return 0 if not s else len(WHITESPACE_RE.sub(" ", s).strip().split(" "))


def iter_input_records(path: Path) -> Iterator[Dict]:
    """Yield dict records from .jsonl (one per line) or .json (array)."""
    if path.suffix.lower() == ".jsonl":
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                yield json.loads(line)
    elif path.suffix.lower() == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            for obj in data:
                yield obj
        elif isinstance(data, dict):
            # single record JSON; treat as one-document corpus
            yield data
        else:
            raise ValueError(f"Unsupported JSON top-level type: {type(data)} in {path}")
    else:
        raise ValueError(f"Unsupported input extension: {path.suffix} (expected .json or .jsonl)")


@dataclass
class CleanDoc:
    id: str
    text: str
    metadata: Dict


def normalize_record(obj: Dict, fallback_id: str) -> CleanDoc:
    """Normalize a cleaned record into CleanDoc.

    Expected fields (best effort):
      - text: str (required)
      - id: str (optional)
      - metadata: dict (optional)
      - or flattened fields such as source, year, specialty
    """
    text = obj.get("text", "") or ""
    doc_id = obj.get("id") or fallback_id

    # Pull metadata fields; prefer a nested metadata object if available
    meta = {}
    if isinstance(obj.get("metadata"), dict):
        meta.update(obj["metadata"])  # type: ignore[index]

    # Preserve common fields if present at top level
    for k in ("source", "year", "specialty", "title", "section", "journal", "link", "date"):
        if k in obj and obj[k] is not None:
            meta[k] = obj[k]

    # Ensure primitives only (lists/dicts become strings)
    meta = {k: ("; ".join(v) if isinstance(v, (list, tuple, set)) else (json.dumps(v) if isinstance(v, dict) else v)) for k, v in meta.items()}

    meta = normalize_metadata_fields(meta)
    return CleanDoc(id=str(doc_id), text=str(text), metadata=meta)


# -----------------------------
# Chunking logic
# -----------------------------

@dataclass
class Chunk:
    id: str
    text: str
    metadata: Dict


def is_enumerated_block(paragraph: str) -> bool:
    # Treat as list-block if most lines look like bullets/numbers.
    lines = [ln for ln in paragraph.splitlines() if ln.strip()]
    if not lines:
        return False
    hits = sum(1 for ln in lines if LIST_BULLET_RE.match(ln))
    return hits >= max(2, int(0.6 * len(lines)))  # 60% or at least 2 lines


def is_heading(paragraph: str) -> bool:
    text = paragraph.strip()
    if not text:
        return False
    if text.endswith(":"):
        return True
    words = text.split()
    if len(words) <= 8 and text.isupper():
        return True
    if len(words) <= 6 and text.istitle():
        return True
    return False


def split_sentences(text: str) -> List[str]:
    # Lightweight sentence split; avoids heavy NLP deps
    parts = SENTENCE_BOUNDARY_RE.split(text.strip()) if text.strip() else []
    # Re-attach stray short fragments
    merged: List[str] = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if merged and word_count(p) < 6:
            merged[-1] = merged[-1] + " " + p
        else:
            merged.append(p)
    return merged


def paragraphs(text: str) -> List[str]:
    # Normalize paragraphs by double newlines; keep single newlines inside lists
    blocks = [b.strip() for b in re.split(r"\n\s*\n", text) if b.strip()]
    return blocks


def chunk_paragraph(paragraph: str, target_min: int, target_max: int) -> List[str]:
    """Chunk a non-list paragraph by sentences into target word bands."""
    sents = split_sentences(paragraph)
    chunks: List[str] = []
    cur: List[str] = []
    cur_wc = 0

    for s in sents:
        wc = word_count(s)
        if cur_wc + wc <= target_max:
            cur.append(s)
            cur_wc += wc
        else:
            # if current is empty (very long sentence), hard cut
            if not cur:
                chunks.append(s)
                cur_wc = 0
            else:
                # finalize current if it meets min, else try to squeeze one more
                if cur_wc >= target_min:
                    chunks.append(" ".join(cur))
                    cur, cur_wc = [s], wc
                else:
                    cur.append(s)
                    cur_wc += wc
                    chunks.append(" ".join(cur))
                    cur, cur_wc = [], 0

    if cur:
        chunks.append(" ".join(cur))

    # If last chunk is too small, try to merge with previous
    if len(chunks) >= 2 and word_count(chunks[-1]) < max(40, int(0.4 * target_min)):
        merged = chunks[-2] + " " + chunks[-1]
        chunks = chunks[:-2] + [merged]

    return chunks


def chunk_document(text: str, target_min: int, target_max: int) -> List[Tuple[str, Optional[str]]]:
    blocks = paragraphs(text)
    out: List[Tuple[str, Optional[str]]] = []

    buffer: List[str] = []
    buffer_wc = 0
    current_heading: Optional[str] = None

    def flush_buffer():
        nonlocal buffer, buffer_wc, out, current_heading
        if buffer:
            for piece in chunk_paragraph("\n".join(buffer), target_min, target_max):
                out.append((piece, current_heading))
            buffer, buffer_wc = [], 0

    for blk in blocks:
        if is_heading(blk):
            current_heading = blk.strip().rstrip(":")
            flush_buffer()
            continue
        if is_enumerated_block(blk):
            # finalize any buffered normal text first
            flush_buffer()
            out.append((blk, current_heading))  # keep list blocks intact as single chunk
        else:
            wc = word_count(blk)
            # accumulate smaller paragraphs before splitting by sentence
            if wc < target_min * 0.6:
                buffer.append(blk)
                buffer_wc += wc
                # if buffer got big, flush as chunks
                if buffer_wc >= target_min:
                    flush_buffer()
            else:
                flush_buffer()
                for piece in chunk_paragraph(blk, target_min, target_max):
                    out.append((piece, current_heading))

    flush_buffer()

    # Final pass: split any overly long non-list chunks by sentences
    final_out: List[Tuple[str, Optional[str]]] = []
    for ch, heading in out:
        if is_enumerated_block(ch):
            final_out.append((ch, heading))
        else:
            if word_count(ch) > target_max * 1.5:
                for piece in chunk_paragraph(ch, target_min, target_max):
                    final_out.append((piece, heading))
            else:
                final_out.append((ch, heading))

    return final_out


def build_chunk_text(body: str, title: str = "", heading: Optional[str] = None) -> str:
    header_lines = []
    if title:
        header_lines.append(title.strip())
    if heading:
        header_lines.append(heading.strip())
    if header_lines:
        header = "\n".join(header_lines)
        return f"{header}\n\n{body}"
    return body


# -----------------------------
# I/O
# -----------------------------

def process_file(path: Path, output_dir: Path, target_min: int, target_max: int) -> Tuple[int, int]:
    """Process a single .json/.jsonl file into a .jsonl of chunks. Returns (#docs, #chunks)."""
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / f"{path.stem}.chunks.jsonl"

    n_docs = 0
    n_chunks = 0

    with out_path.open("w", encoding="utf-8") as out:
        for i, obj in enumerate(iter_input_records(path)):
            n_docs += 1
            doc = normalize_record(obj, fallback_id=f"{path.stem}-{i}")
            parts = chunk_document(doc.text, target_min=target_min, target_max=target_max)

            total = len(parts)
            title = str(doc.metadata.get("title") or doc.metadata.get("source") or "").strip()
            for j, (ch, heading) in enumerate(parts):
                chunk_id = f"{doc.id}::p{j}"
                body = build_chunk_text(ch, title=title, heading=heading)
                metadata_dict = {
                    **doc.metadata,
                    "doc_id": doc.id,
                    "group_id": doc.id,
                    "id": chunk_id,
                    "chunk_index": j,
                    "chunk_count": total,
                    "word_count": word_count(ch),
                    "heading": heading or "",
                }
                metadata_dict = normalize_metadata_fields(metadata_dict)
                record = {
                    "id": chunk_id,
                    "text": body,
                    "metadata": metadata_dict,
                }
                out.write(json.dumps(record, ensure_ascii=False) + "\n")
                n_chunks += 1

    return n_docs, n_chunks


def main():
    p = argparse.ArgumentParser(description="Chunk cleaned medical documents into 100–300 word segments")
    p.add_argument("--input", type=str, default="./cleaned", help="Directory containing cleaned .jsonl or .json files")
    p.add_argument("--output", type=str, default="./chunks", help="Directory to write chunked JSONL files")
    p.add_argument("--pattern", type=str, default="*.jsonl", help="Glob pattern within input directory (e.g., *.jsonl or *.json)")
    p.add_argument("--target_min", type=int, default=100, help="Minimum words per chunk")
    p.add_argument("--target_max", type=int, default=300, help="Maximum words per chunk")
    args = p.parse_args()

    in_dir = Path(args.input)
    out_dir = Path(args.output)

    files = sorted(in_dir.glob(args.pattern))
    if not files:
        # fallback: try both
        files = sorted(list(in_dir.glob("*.jsonl")) + list(in_dir.glob("*.json")))

    if not files:
        raise SystemExit(f"No input files found in {in_dir} matching pattern {args.pattern}")

    total_docs = 0
    total_chunks = 0

    for f in files:
        n_docs, n_chunks = process_file(f, out_dir, args.target_min, args.target_max)
        total_docs += n_docs
        total_chunks += n_chunks
        print(f"Processed {f.name}: {n_docs} docs -> {n_chunks} chunks")

    print(f"Done. {total_docs} documents -> {total_chunks} chunks written to {out_dir}")


if __name__ == "__main__":
    main()
