#!/usr/bin/env python3
"""Verify ChromaDB index with test query."""
from sentence_transformers import SentenceTransformer
import chromadb
from chromadb.config import Settings

model = SentenceTransformer('microsoft/harrier-oss-v1-0.6b')
qvec = model.encode(['pulmonary embolism'])[0]

client = chromadb.PersistentClient(path='./chroma_store', settings=Settings(anonymized_telemetry=False))
col = client.get_or_create_collection(name='medical_rag')

results = col.query(
    query_embeddings=[qvec.tolist()],
    n_results=3,
    include=['documents', 'metadatas']
)

print(f'Verification query returned {len(results["ids"][0])} results')
for i, (id_, meta) in enumerate(zip(results['ids'][0], results['metadatas'][0])):
    print(f'  {i+1}. {id_[:50]}')
    if meta:
        print(f'     Source: {meta.get("source", "N/A")}')