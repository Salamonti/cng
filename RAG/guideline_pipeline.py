#!/usr/bin/env python3
"""
Guideline pipeline: fetch, merge, chunk, embed, index.

Usage:
  python3 guideline_pipeline.py --fetch --merge --chunk --embed
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import subprocess
import time

# ============================================================================
# Fetch
# ============================================================================

def fetch_guidelines():
    """Fetch guidelines from all sources."""
    print("Fetching guidelines from all sources...")
    
    # Run guideline_sources.py for each source
    sources = ["gold", "gina", "nice", "who", "ean", "aaos"]
    
    for source in sources:
        output = f"./raw_docs/guidelines_{source}.jsonl"
        print(f"\n  Fetching {source}...")
        result = subprocess.run(
            ["python3", "guideline_sources.py", "--source", source, "--output", output],
            capture_output=True, text=True, timeout=300
        )
        print(result.stdout[-300:])
        time.sleep(2)


# ============================================================================
# Merge
# ============================================================================

def _guideline_content_len(g: dict) -> int:
    """Approximate how complete a guideline record's content is."""
    return len((g.get("text") or ""))


def merge_guidelines(input_dir: str = "./raw_docs",
                     output_file: str = "./raw_docs/guidelines_merged.jsonl"):
    """Merge all guideline JSONL files into one, deduplicating by title.

    Full-text-repair passes (e.g. fetch_full_text.py writing
    guidelines_gin_full.jsonl from guidelines_gin.jsonl) re-emit the same
    titles with a populated "text" field added. Since source files are
    processed in sorted(glob(...)) order and "guidelines_gin." sorts before
    "guidelines_gin_", a first-one-wins dedup would silently keep the
    original abstract-only record and discard the repaired one. Instead,
    when a title recurs, keep whichever copy has more content.
    """
    print("\nMerging guidelines...")

    # Find all guideline JSONL files
    jsonl_files = sorted(glob.glob(os.path.join(input_dir, "guidelines_*.jsonl")))

    # Skip the merged file itself
    jsonl_files = [f for f in jsonl_files if "merged" not in f]

    by_title: dict = {}
    title_order: list = []

    for jsonl_file in jsonl_files:
        with open(jsonl_file, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    g = json.loads(line)
                    title = g.get("title", "").lower().strip()
                    if not title:
                        continue
                    existing = by_title.get(title)
                    if existing is None:
                        by_title[title] = g
                        title_order.append(title)
                    elif _guideline_content_len(g) > _guideline_content_len(existing):
                        by_title[title] = g

    merged = [by_title[title] for title in title_order]

    # Save merged file
    with open(output_file, "w", encoding="utf-8") as f:
        for g in merged:
            f.write(json.dumps(g, ensure_ascii=False) + "\n")
    
    # Count by source
    source_counts = {}
    for g in merged:
        source = g.get("source", "GIN")
        source_counts[source] = source_counts.get(source, 0) + 1
    
    print(f"Merged {len(merged)} guidelines")
    print(f"By source: {json.dumps(source_counts, indent=2)}")
    
    return merged


# ============================================================================
# Chunk
# ============================================================================

def chunk_guidelines(input_file: str = "./raw_docs/guidelines_merged.jsonl",
                     output_dir: str = "./chunks"):
    """Chunk guidelines using the existing chunking pipeline."""
    print("\nChunking guidelines...")
    
    # Create chunks directory
    os.makedirs(output_dir, exist_ok=True)
    
    # Run chunking pipeline
    result = subprocess.run(
        ["python3", "chunking_pipeline.py", "--input", os.path.dirname(input_file),
         "--output", output_dir],
        capture_output=True, text=True, timeout=300
    )
    print(result.stdout[-500:])
    
    # Count chunks
    chunk_files = glob.glob(os.path.join(output_dir, "*.jsonl"))
    total_chunks = 0
    for cf in chunk_files:
        with open(cf, "r") as f:
            total_chunks += sum(1 for line in f if line.strip())
    
    print(f"Created {total_chunks} chunks")
    return total_chunks


# ============================================================================
# Embed and Index
# ============================================================================

def embed_and_index(chunk_dir: str = "./chunks"):
    """Embed chunks and index to ChromaDB."""
    print("\nEmbedding and indexing...")
    
    # Run embed_chunks.py
    result = subprocess.run(
        ["python3", "embed_chunks.py", "--input", chunk_dir,
         "--output", "./embeddings", "--to-chroma", "--batch", "32"],
        capture_output=True, text=True, timeout=600
    )
    print(result.stdout[-500:])


# ============================================================================
# Main
# ============================================================================

def main():
    parser = argparse.ArgumentParser(description="Guideline pipeline")
    parser.add_argument("--fetch", action="store_true", help="Fetch guidelines from all sources")
    parser.add_argument("--chunk", action="store_true", help="Chunk guidelines")
    parser.add_argument("--embed", action="store_true", help="Embed and index to ChromaDB")
    parser.add_argument("--merge", action="store_true", help="Merge all guideline files")
    args = parser.parse_args()
    
    if args.fetch:
        fetch_guidelines()
    
    if args.merge:
        merge_guidelines()
    
    if args.chunk:
        chunk_guidelines()
    
    if args.embed:
        embed_and_index()


if __name__ == "__main__":
    main()
