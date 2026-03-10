# C:\RAG\chunker.py
from typing import List, Dict
import re
import os  # noqa: F401
import json  # noqa: F401
from utils_meta import normalize_metadata_fields
from pathlib import Path
from pydantic import BaseModel

class Chunk(BaseModel):
    id: str
    text: str
    metadata: Dict

PHI_PATTERNS = [
    r"\b\d{3}-\d{2}-\d{4}\b",               # SSN pattern (example)
    r"\b\d{3}-\d{3}-\d{4}\b",               # phone
    r"\b\d{1,2}/\d{1,2}/\d{2,4}\b",         # dates
    r"\bMRN[:\s]*\d+\b",                     # MRN
    r"\b[A-Z][a-z]+ [A-Z][a-z]+\b",         # naive First Last (toy only)
]

def deidentify(text: str) -> str:
    redacted = text
    for pat in PHI_PATTERNS:
        redacted = re.sub(pat, "[REDACTED]", redacted)
    return redacted

def chunk_text(doc_text: str, chunk_size=1800, overlap=200) -> List[str]:
    s = doc_text
    chunks = []
    start = 0
    while start < len(s):
        end = min(len(s), start + chunk_size)
        chunk = s[start:end]
        chunks.append(chunk)
        if end == len(s): 
            break
        start = end - overlap
        if start < 0: 
            start = 0
    return chunks

def read_corpus(corpus_dir: str) -> List[Chunk]:
    chunks: List[Chunk] = []
    for p in sorted(Path(corpus_dir).glob("*.txt")):
        raw = p.read_text(encoding="utf-8")
        red = deidentify(raw)
        meta = extract_metadata(red)
        body = strip_header(red)
        parts = chunk_text(body)
        for i, ch in enumerate(parts):
            chunk_id = f"{p.stem}::p{i}"
            metadata = normalize_metadata_fields({**meta, "doc_id": p.stem, "group_id": p.stem, "id": chunk_id, "chunk_index": i})
            chunks.append(Chunk(
                id=chunk_id,
                text=ch,
                metadata=metadata
            ))
    return chunks

def extract_metadata(text: str) -> Dict:
    meta = {}
    m1 = re.search(r"Source:\s*(.+?);", text)
    m2 = re.search(r"Section:\s*(.+)\n", text)
    m3 = re.search(r"last_updated:\s*([0-9\-]+)", text)
    if m1: 
        meta["source"] = m1.group(1).strip()
    if m2: 
        meta["section"] = m2.group(1).strip()
    if m3: 
        meta["last_updated"] = m3.group(1).strip()
    # you can add specialty/doc_type later
    return meta

def strip_header(text: str) -> str:
    # remove the first 2 lines where we stashed Source/Section/last_updated
    lines = text.splitlines()
    return "\n".join(lines[2:]) if len(lines) > 2 else text
