#!/usr/bin/env python3
"""
monitoring_dashboard.py — Phase 6: Monitoring & Quality Dashboard

Comprehensive ChromaDB index statistics:
- Total chunks, specialty distribution, source distribution
- Year distribution, GRADE coverage
- Metadata completeness (specialty, year, GRADE, DOI)
- Per-specialty breakdown with chunk counts and recency

Usage:
  cd /opt/dreamcision/RAG && .venv/bin/python3 monitoring_dashboard.py
  cd /opt/dreamcision/RAG && .venv/bin/python3 monitoring_dashboard.py --json > dashboard.json
"""
import sys
import json
import datetime
sys.path.insert(0, '.')

import chromadb
from chromadb.config import Settings
from collections import Counter, defaultdict

def get_dashboard():
    client = chromadb.PersistentClient(path='./chroma_store', settings=Settings(anonymized_telemetry=False))
    col = client.get_or_create_collection(name='medical_rag')
    total = col.count()

    specialty_counts = Counter()
    source_counts = Counter()
    grade_counts = Counter()
    year_counts = Counter()
    doi_counts = Counter()  # has_doi / no_doi
    specialty_years = defaultdict(list)  # specialty -> [years]
    specialty_sources = defaultdict(Counter)  # specialty -> {source: count}

    batch_size = 1000
    for i in range(0, total, batch_size):
        res = col.get(offset=i, limit=batch_size, include=['metadatas'])
        for meta in res['metadatas']:
            if not meta:
                continue
            spec = meta.get('specialty', 'unknown')
            specialty_counts[spec] += 1

            src = meta.get('source', 'unknown')
            source_counts[src] += 1
            specialty_sources[spec][src] += 1

            grade_counts[meta.get('grade', 'N/A')] += 1

            year = meta.get('year', 'N/A')
            if year and year != 'N/A':
                try:
                    y = int(year)
                    if 2000 <= y <= 2030:
                        year_counts[str(y)] += 1
                        specialty_years[spec].append(y)
                except (ValueError, TypeError):
                    pass

            doi = meta.get('doi', '')
            doi_counts['has_doi' if doi and doi != 'N/A' else 'no_doi'] += 1

    current_year = datetime.datetime.now().year
    stale_threshold = current_year - 1  # >6 months means no update in last year

    # Build per-specialty summary
    specialty_summary = {}
    for spec in specialty_counts:
        years = specialty_years.get(spec, [])
        latest = max(years) if years else None
        count = specialty_counts[spec]
        specialty_summary[spec] = {
            'chunks': count,
            'latest_year': latest,
            'sources': dict(specialty_sources[spec].most_common(5)),
        }

    # Metadata completeness
    has_specialty = sum(1 for s, c in specialty_counts.items() if s != 'unknown')
    has_year = sum(int(y) for y in year_counts.values())
    has_grade = sum(c for g, c in grade_counts.items() if g != 'N/A')
    has_doi = doi_counts.get('has_doi', 0)

    dashboard = {
        'generated': datetime.datetime.now().isoformat(),
        'total_chunks': total,
        'metadata_completeness': {
            'specialty': f'{has_specialty}/{total} ({100*has_specialty/total:.1f}%)',
            'year': f'{has_year}/{total} ({100*has_year/total:.1f}%)',
            'grade': f'{has_grade}/{total} ({100*has_grade/total:.1f}%)',
            'doi': f'{has_doi}/{total} ({100*has_doi/total:.1f}%)',
        },
        'specialty_distribution': dict(specialty_counts.most_common()),
        'source_distribution': dict(source_counts.most_common(20)),
        'grade_distribution': dict(grade_counts.most_common()),
        'year_distribution': dict(sorted(year_counts.items(), reverse=True)),
        'specialty_summary': specialty_summary,
    }
    return dashboard


def print_dashboard(dashboard):
    d = dashboard
    print(f'\n=== RAG Monitoring Dashboard ===')
    print(f'Generated: {d["generated"]}')
    print(f'Total chunks: {d["total_chunks"]}')

    print(f'\n--- Metadata Completeness ---')
    for field, val in d['metadata_completeness'].items():
        print(f'  {field}: {val}')

    print(f'\n--- Specialty Distribution ---')
    for spec, cnt in d['specialty_distribution'].items():
        pct = 100 * cnt / d['total_chunks']
        print(f'  {spec}: {cnt} ({pct:.1f}%)')

    print(f'\n--- Source Distribution (top 20) ---')
    for src, cnt in d['source_distribution'].items():
        print(f'  {src}: {cnt}')

    print(f'\n--- GRADE Distribution ---')
    for g, cnt in d['grade_distribution'].items():
        print(f'  {g}: {cnt}')

    print(f'\n--- Year Distribution ---')
    for y, cnt in d['year_distribution'].items():
        print(f'  {y}: {cnt}')

    print(f'\n--- Specialty Summary ---')
    for spec, info in sorted(d['specialty_summary'].items()):
        latest = info['latest_year'] or 'N/A'
        print(f'  {spec}: {info["chunks"]} chunks, latest year: {latest}')
        for src, cnt in list(info['sources'].items())[:3]:
            print(f'    - {src}: {cnt}')


def main():
    dashboard = get_dashboard()
    if '--json' in sys.argv:
        print(json.dumps(dashboard, indent=2))
    else:
        print_dashboard(dashboard)


if __name__ == '__main__':
    main()