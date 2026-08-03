#!/usr/bin/env python3
"""Merge old snapshot data into current ChromaDB index, re-embedding with Harrier."""

import chromadb
from chromadb import Settings
from embedder import Embedder

SNAPSHOT_PATH = './snapshots/chroma/20260629'
CURRENT_PATH = './chroma_store'
COLLECTION_NAME = 'medical_rag'
BATCH_SIZE = 5000

def main():
    print("Loading snapshot...", flush=True)
    snap_client = chromadb.PersistentClient(path=SNAPSHOT_PATH, settings=Settings(anonymized_telemetry=False))
    snap_coll = snap_client.get_collection(COLLECTION_NAME)
    snap_count = snap_coll.count()
    print(f"Snapshot has {snap_count:,} items", flush=True)

    print("Loading current index...", flush=True)
    curr_client = chromadb.PersistentClient(path=CURRENT_PATH, settings=Settings(anonymized_telemetry=False))
    curr_coll = curr_client.get_collection(COLLECTION_NAME)
    curr_count = curr_coll.count()
    print(f"Current index has {curr_count:,} items", flush=True)

    # Get all current IDs to skip duplicates
    print("Fetching current IDs...", flush=True)
    curr_ids_set = set()
    offset = 0
    while offset < curr_count:
        batch = curr_coll.get(limit=10000, offset=offset, include=[])
        curr_ids_set.update(batch['ids'])
        offset += 10000
    print(f"Current IDs: {len(curr_ids_set):,}", flush=True)

    # Extract snapshot documents, skip ones already in current index
    print("Extracting snapshot documents (batched)...", flush=True)
    new_ids = []
    new_docs = []
    new_meta = []

    fetch_batch = 5000
    for offset in range(0, snap_count, fetch_batch):
        batch = snap_coll.get(limit=fetch_batch, offset=offset, include=['documents', 'metadatas'])
        for i, sid in enumerate(batch['ids']):
            if sid not in curr_ids_set:
                new_ids.append(sid)
                new_docs.append(batch['documents'][i])
                new_meta.append(batch['metadatas'][i])
        print(f"  Fetched {min(offset+fetch_batch, snap_count)}/{snap_count}, new so far: {len(new_ids):,}", flush=True)

    print(f"New items to add: {len(new_ids):,} (skipped {snap_count - len(new_ids):,} duplicates)", flush=True)

    if not new_ids:
        print("Nothing to add — all snapshot items already in current index.", flush=True)
        return

    # Re-embed with Harrier
    print("Loading Harrier embedder...", flush=True)
    embedder = Embedder("microsoft/harrier-oss-v1-0.6b")
    print("Re-embedding...", flush=True)
    total_batches = (len(new_docs) + BATCH_SIZE - 1) // BATCH_SIZE

    for batch_idx in range(total_batches):
        start = batch_idx * BATCH_SIZE
        end = min(start + BATCH_SIZE, len(new_docs))
        batch_docs = new_docs[start:end]
        batch_ids = new_ids[start:end]
        batch_meta = new_meta[start:end]

        embeddings = embedder.encode(batch_docs)

        # Upsert into current collection
        curr_coll.upsert(
            ids=batch_ids,
            embeddings=embeddings,
            documents=batch_docs,
            metadatas=batch_meta
        )

        print(f"  Batch {batch_idx+1}/{total_batches}: upserted {len(batch_ids)} items", flush=True)

    # Verify
    final_count = curr_coll.count()
    print(f"\nDone! Current index now has {final_count:,} items (was {curr_count:,}, added {len(new_ids):,})", flush=True)

if __name__ == '__main__':
    main()
