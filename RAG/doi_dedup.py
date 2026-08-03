#!/usr/bin/env python3
"""DOI deduplication for ChromaDB medical_rag collection."""
import chromadb
from chromadb.config import Settings
from collections import defaultdict

client = chromadb.PersistentClient(path='./chroma_store', settings=Settings(anonymized_telemetry=False))
col = client.get_or_create_collection(name='medical_rag')
total = col.count()
batch_size = 1000
deleted = 0
doi_groups = defaultdict(list)

for i in range(0, total, batch_size):
    res = col.get(offset=i, limit=batch_size, include=['metadatas'])
    for j, meta in enumerate(res['metadatas']):
        if meta and 'doi' in meta and meta['doi']:
            doi = meta['doi'].strip()
            if doi and doi != 'N/A':
                doc_id = meta.get('doc_id', res['ids'][j].split('_chunk_')[0] if '_chunk_' in res['ids'][j] else 'unknown')
                doi_groups[(doc_id, doi)].append(res['ids'][j])

for (doc_id, doi), ids in doi_groups.items():
    if len(ids) > 1:
        to_delete = ids[1:]
        col.delete(ids=to_delete)
        deleted += len(to_delete)

final = col.count()
print(f'Deduplication: removed {deleted} duplicates, {final} chunks remaining')