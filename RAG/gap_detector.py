#!/usr/bin/env python3
"""
gap_detector.py — Phase 6: Gap Detection

Compares web search results against RAG results for the same queries.
When web search returns good results but RAG returns nothing (or very
few results), it logs the gap — indicating where the index needs more content.

Usage:
  cd /opt/dreamcision/RAG && .venv/bin/python3 gap_detector.py
  cd /opt/dreamcision/RAG && .venv/bin/python3 gap_detector.py --json > gaps.json
"""
import sys
import json
import datetime
sys.path.insert(0, '.')

import requests

RAG_API = "http://127.0.0.1:8007/query"
RAG_THRESHOLD = 2  # fewer than this many hits = gap
WEB_THRESHOLD = 2  # web search needs at least this many results to count

# Same 20 queries as test_queries.py
TEST_QUERIES = [
    "pulmonary embolism treatment guidelines",
    "COPD exacerbation management GOLD",
    "asthma stepwise treatment GINA guidelines",
    "acute coronary syndrome management ACC AHA",
    "heart failure treatment algorithm ESC guidelines",
    "hypertension management JNC guidelines",
    "IBD inflammatory bowel disease treatment guidelines",
    "celiac disease diagnosis and management",
    "breast cancer screening guidelines ASCO",
    "colorectal cancer treatment NCCN",
    "community acquired pneumonia treatment IDSA guidelines",
    "antibiotic stewardship program guidelines",
    "stroke management guidelines AHA",
    "epilepsy treatment guidelines AAN",
    "diabetes mellitus management ADA guidelines",
    "thyroid nodule management ATA guidelines",
    "rheumatoid arthritis treatment ACR guidelines",
    "gout management ACR guidelines",
    "CKD chronic kidney disease management KDIGO",
    "diabetic nephropathy treatment guidelines",
]


def query_rag(query):
    """Query the RAG API."""
    try:
        resp = requests.post(RAG_API, json={"query": query, "top_k": 5, "use_web_search": False}, timeout=30)
        data = resp.json()
        hits = data.get("results", data.get("hits", []))
        return {
            "hit_count": len(hits),
            "top_score": hits[0].get("score") if hits else None,
            "specialties": list(set(h.get("metadata", {}).get("specialty", "") for h in hits)),
        }
    except Exception as e:
        return {"hit_count": 0, "error": str(e)}


def query_web(query):
    """Query via web_search module."""
    try:
        from web_search import search_medical
        results = search_medical(query, top_k=5)
        # Filter to meaningful results (has content)
        meaningful = [r for r in results if r.get("content") and len(r.get("content", "")) > 200]
        return {
            "result_count": len(meaningful),
            "sources": list(set(r.get("url", "")[:30] for r in meaningful)),
        }
    except Exception as e:
        return {"result_count": 0, "error": str(e)}


def main():
    print(f'\n=== Gap Detection — Web Search vs RAG ===')
    print(f'RAG threshold: < {RAG_THRESHOLD} hits = gap')
    print(f'Web threshold: >= {WEB_THRESHOLD} results = web has content')

    gaps = []
    covered = []

    for i, query in enumerate(TEST_QUERIES):
        rag = query_rag(query)
        web = query_web(query)

        is_gap = (rag["hit_count"] < RAG_THRESHOLD and web["result_count"] >= WEB_THRESHOLD)

        if is_gap:
            gaps.append({
                "query": query,
                "rag_hits": rag["hit_count"],
                "web_results": web["result_count"],
                "rag_specialties": rag.get("specialties", []),
                "web_sources": web.get("sources", []),
            })
            print(f'  ⚠️  GAP [{i+1:02d}] {query[:50]}... RAG: {rag["hit_count"]} hits, Web: {web["result_count"]} results')
        else:
            covered.append({
                "query": query,
                "rag_hits": rag["hit_count"],
                "web_results": web["result_count"],
            })
            status = "✅" if rag["hit_count"] >= RAG_THRESHOLD else "⚪"
            print(f'  {status} [{i+1:02d}] {query[:50]}... RAG: {rag["hit_count"]} hits, Web: {web["result_count"]} results')

    print(f'\n--- Summary ---')
    print(f'  Gaps (web has content, RAG does not): {len(gaps)}')
    print(f'  Covered (RAG has content): {len(covered)}')
    print(f'  Total queries: {len(TEST_QUERIES)}')

    if gaps:
        print(f'\n--- Gap Details ---')
        for g in gaps:
            print(f'  Query: {g["query"]}')
            print(f'    RAG hits: {g["rag_hits"]}, Web results: {g["web_results"]}')
            if g.get("web_sources"):
                print(f'    Web sources: {g["web_sources"][:3]}')

    if '--json' in sys.argv:
        output = {"gaps": gaps, "covered": covered, "generated": datetime.datetime.now().isoformat()}
        print(json.dumps(output, indent=2))

    return 0


if __name__ == '__main__':
    sys.exit(main())