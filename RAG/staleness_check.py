#!/usr/bin/env python3
"""
staleness_check.py — Phase 6: Staleness Detection

Checks when each specialty was last updated by examining the year metadata
in ChromaDB. Flags specialties where the most recent guideline is older
than a configurable threshold (default: 1 year).

Usage:
  cd /opt/dreamcision/RAG && .venv/bin/python3 staleness_check.py
  cd /opt/dreamcision/RAG && .venv/bin/python3 staleness_check.py --threshold 2
"""
import sys
import datetime
sys.path.insert(0, '.')

import chromadb
from chromadb.config import Settings
from collections import defaultdict

def main():
    threshold_years = 1
    if '--threshold' in sys.argv:
        idx = sys.argv.index('--threshold')
        threshold_years = int(sys.argv[idx + 1])

    current_year = datetime.datetime.now().year
    cutoff_year = current_year - threshold_years

    client = chromadb.PersistentClient(path='./chroma_store', settings=Settings(anonymized_telemetry=False))
    col = client.get_or_create_collection(name='medical_rag')
    total = col.count()

    specialty_latest = {}
    specialty_counts = defaultdict(int)

    batch_size = 1000
    for i in range(0, total, batch_size):
        res = col.get(offset=i, limit=batch_size, include=['metadatas'])
        for meta in res['metadatas']:
            if not meta:
                continue
            spec = meta.get('specialty', 'unknown')
            specialty_counts[spec] += 1

            year = meta.get('year', 'N/A')
            if year and year != 'N/A':
                try:
                    y = int(year)
                    if 2000 <= y <= 2030:
                        if spec not in specialty_latest or y > specialty_latest[spec]:
                            specialty_latest[spec] = y
                except (ValueError, TypeError):
                    pass

    print(f'\n=== Staleness Check (threshold: {threshold_years} year{"s" if threshold_years > 1 else ""}) ===')
    print(f'Current year: {current_year}, Cutoff: {cutoff_year}')
    print(f'Total chunks: {total}')

    stale = []
    current = []
    no_year = []

    for spec in sorted(specialty_counts.keys()):
        count = specialty_counts[spec]
        latest = specialty_latest.get(spec)

        if latest is None:
            no_year.append((spec, count))
        elif latest < cutoff_year:
            stale.append((spec, count, latest))
        else:
            current.append((spec, count, latest))

    print(f'\n--- Stale Specialties (latest year < {cutoff_year}) ---')
    if stale:
        for spec, count, latest in stale:
            age = current_year - latest
            print(f'  ⚠️  {spec}: {count} chunks, latest year {latest} ({age} years old)')
    else:
        print('  ✅ No stale specialties found.')

    print(f'\n--- Current Specialties (latest year >= {cutoff_year}) ---')
    if current:
        for spec, count, latest in current:
            print(f'  ✅ {spec}: {count} chunks, latest year {latest}')
    else:
        print('  (none)')

    print(f'\n--- Specialties with No Year Metadata ---')
    if no_year:
        for spec, count in no_year:
            print(f'  ❓ {spec}: {count} chunks (no valid year found)')
    else:
        print('  ✅ All specialties have year metadata.')

    print(f'\n--- Summary ---')
    print(f'  Stale: {len(stale)}')
    print(f'  Current: {len(current)}')
    print(f'  No year data: {len(no_year)}')
    print(f'  Total specialties: {len(specialty_counts)}')

    return 0 if len(stale) == 0 else 1


if __name__ == '__main__':
    sys.exit(main())