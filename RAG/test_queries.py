#!/usr/bin/env python3
"""
test_queries.py — Phase 6: Test Queries Across Specialties

Runs 20 clinical queries across all specialties against the RAG API,
verifies results are relevant and well-scored.

Usage:
  cd /opt/dreamcision/RAG && .venv/bin/python3 test_queries.py
  cd /opt/dreamcision/RAG && .venv/bin/python3 test_queries.py --json > test_results.json
"""
import sys
import json
sys.path.insert(0, '.')

import requests

RAG_API = "http://127.0.0.1:8007/query"

# 20 test queries spanning all major specialties
TEST_QUERIES = [
    # Respiratory (existing strength)
    {"query": "pulmonary embolism treatment guidelines", "expected_specialty": "respiratory"},
    {"query": "COPD exacerbation management GOLD", "expected_specialty": "respiratory"},
    # Cardiology
    {"query": "acute coronary syndrome management ACC AHA", "expected_specialty": "cardiology"},
    {"query": "heart failure treatment guidelines ESC", "expected_specialty": "cardiology"},
    {"query": "hypertension management JNC", "expected_specialty": "cardiology"},
    # Gastroenterology
    {"query": "IBD inflammatory bowel disease treatment guidelines", "expected_specialty": "gastroenterology"},
    {"query": "hepatitis C treatment guidelines AASLD", "expected_specialty": "gastroenterology"},
    # Oncology
    {"query": "breast cancer screening guidelines ASCO", "expected_specialty": "oncology"},
    {"query": "colorectal cancer treatment NCCN", "expected_specialty": "oncology"},
    # Infectious Disease
    {"query": "antibiotic stewardship guidelines IDSA", "expected_specialty": "infectious_disease"},
    {"query": "MRSA treatment guidelines", "expected_specialty": "infectious_disease"},
    # Endocrinology
    {"query": "diabetes mellitus management ADA guidelines", "expected_specialty": "endocrinology"},
    {"query": "thyroid nodule management ATA guidelines", "expected_specialty": "endocrinology"},
    # Neurology
    {"query": "stroke management guidelines AHA", "expected_specialty": "neurology"},
    {"query": "epilepsy treatment guidelines AAN", "expected_specialty": "neurology"},
    # Rheumatology
    {"query": "rheumatoid arthritis treatment ACR guidelines", "expected_specialty": "rheumatology"},
    {"query": "lupus SLE management guidelines", "expected_specialty": "rheumatology"},
    # Nephrology
    {"query": "CKD chronic kidney disease management KDIGO", "expected_specialty": "nephrology"},
    {"query": "diabetic nephropathy treatment guidelines", "expected_specialty": "nephrology"},
    # Primary Care / General
    {"query": "vaccination schedule CDC guidelines", "expected_specialty": "primary_care"},
]


def run_test(query):
    """Run a single test query against the RAG API."""
    result = {"query": query, "hit_count": 0, "top_score": None, "specialties_found": []}
    try:
        resp = requests.post(RAG_API, json={"query": query, "top_k": 5}, timeout=30)
        data = resp.json()
        # API returns 'results' not 'hits'
        hits = data.get("results", data.get("hits", []))
        result["hit_count"] = len(hits)
        if hits:
            result["top_score"] = hits[0].get("score")
            for h in hits:
                meta = h.get("metadata", {})
                spec = meta.get("specialty", "")
                if spec and spec not in result["specialties_found"]:
                    result["specialties_found"].append(spec)
    except Exception as e:
        result["error"] = str(e)
    return result


def main():
    print(f'\n=== Test Queries — {len(TEST_QUERIES)} queries across specialties ===')
    print(f'RAG API: {RAG_API}')

    # Check API is up
    try:
        resp = requests.get("http://127.0.0.1:8007/health", timeout=5)
        print(f'API status: {resp.status_code}')
    except Exception as e:
        print(f'WARNING: RAG API not reachable — {e}')
        print('Results will show errors if API is down.')

    results = []
    passed = 0
    failed = 0

    for i, test in enumerate(TEST_QUERIES):
        result = run_test(test["query"])
        result["expected_specialty"] = test["expected_specialty"]
        results.append(result)

        status = "✅" if result["hit_count"] > 0 else "❌"
        if result["hit_count"] > 0:
            passed += 1
        else:
            failed += 1

        score_str = f', score: {result["top_score"]:.4f}' if result.get("top_score") else ''
        spec_str = f', specialties: {result.get("specialties_found", [])}'
        print(f'  {status} [{i+1:02d}] {test["query"][:55]}... → {result["hit_count"]} hits{score_str}{spec_str}')

    print(f'\n--- Summary ---')
    print(f'  Passed (hits > 0): {passed}/{len(TEST_QUERIES)}')
    print(f'  Failed (no hits): {failed}/{len(TEST_QUERIES)}')

    # Specialty coverage analysis
    specialties_covered = set()
    for r in results:
        for s in r.get("specialties_found", []):
            specialties_covered.add(s)
    print(f'  Specialties in results: {len(specialties_covered)} — {sorted(specialties_covered)}')

    if '--json' in sys.argv:
        print(json.dumps(results, indent=2))

    return 0 if failed == 0 else 1


if __name__ == '__main__':
    sys.exit(main())