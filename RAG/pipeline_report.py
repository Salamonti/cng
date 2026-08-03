#!/usr/bin/env python3
"""Generate specialty/source report for ChromaDB medical_rag collection."""
import sys
sys.path.insert(0, '.')
import chromadb
from chromadb.config import Settings
from collections import Counter

client = chromadb.PersistentClient(path='./chroma_store', settings=Settings(anonymized_telemetry=False))
col = client.get_or_create_collection(name='medical_rag')

total = col.count()
specialty_counts = Counter()
source_counts = Counter()
grade_counts = Counter()
year_counts = Counter()

batch_size = 1000
for i in range(0, total, batch_size):
    res = col.get(offset=i, limit=batch_size, include=['metadatas'])
    for meta in res['metadatas']:
        if not meta:
            continue
        specialty_counts[meta.get('specialty', 'unknown')] += 1
        source_counts[meta.get('source', 'unknown')] += 1
        grade_counts[meta.get('grade', 'N/A')] += 1
        year = meta.get('year', 'N/A')
        if year and year != 'N/A':
            year_counts[str(year)] += 1

print(f'\n=== Weekly Pipeline Report ===')
print(f'Total chunks: {total}')
print(f'\n--- By Specialty ---')
for spec, cnt in specialty_counts.most_common():
    print(f'  {spec}: {cnt}')
print(f'\n--- By Source (top 20) ---')
for src, cnt in source_counts.most_common(20):
    print(f'  {src}: {cnt}')
print(f'\n--- By GRADE ---')
for g, cnt in grade_counts.most_common():
    print(f'  {g}: {cnt}')
print(f'\n--- By Year (top 10) ---')
for y, cnt in year_counts.most_common(10):
    print(f'  {y}: {cnt}')