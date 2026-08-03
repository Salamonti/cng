#!/usr/bin/env python3
"""Rebuild BM25 index for ChromaDB medical_rag collection."""
import sys
sys.path.insert(0, '.')
from bm25_index import warm_bm25
from store import get_client, get_collection

client = get_client('./chroma_store')
col = get_collection(client)
print(f'Collection size: {col.count()}')
helper, ids = warm_bm25(col)
print(f'BM25 index rebuilt with {len(ids)} documents')