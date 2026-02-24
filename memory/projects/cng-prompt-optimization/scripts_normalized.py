#!/usr/bin/env python3
import csv
import json
import random
import re
from pathlib import Path

ROOT = Path('/home/solom/.openclaw/workspace/memory/projects/cng-prompt-optimization')
INPUTS = ROOT / 'inputs'
REPORTS = ROOT / 'reports'
ITER = ROOT / 'iterations'

MAX_ITEMS = {
    '1.8': 2, '2.1': 2, '2.2': 2, '2.3': 2, '2.4': 2, '2.5': 1,
    '3.1': 2, '3.2': 1, '3.3': 2, '3.4': 2, '3.5': 2,
    '4.1': 2, '4.2': 2, '4.3': 2, '4.4': 2, '4.5': 2,
}

ISO_DATE = re.compile(r'\b\d{4}-\d{2}-\d{2}\b')
WORD = re.compile(r"[A-Za-z0-9']+")
MED_HINT = re.compile(r'\b(mg|mcg|g|ml|units?|tablet|tab|capsule|cap|puff|inhal|insulin|po|bid|tid|daily|qhs|prn)\b', re.I)


def extract_between(txt: str, tag: str) -> str:
    m = re.search(rf'<{tag}>(.*?)</{tag}>', txt, re.S)
    return m.group(1).strip() if m else ''


def med_long_line_risk(txt: str) -> bool:
    for ln in txt.splitlines():
        line = ln.strip()
        if not line:
            continue
        if MED_HINT.search(line):
            core = re.sub(r'\([^)]*\)', '', line)
            if len(WORD.findall(core)) > 8:
                return True
    return False


def derive_gold(case_idx: int, case_txt: str):
    caps = dict(MAX_ITEMS)
    blocked = []

    current = extract_between(case_txt, 'CURRENT_ENCOUNTER')
    total_words = len(WORD.findall(case_txt))
    current_words = len(WORD.findall(current))

    # HPI completeness and narrative are constrained by sparse CURRENT_ENCOUNTER transcripts.
    if current_words < 55:
        caps['2.2'] = 1
        blocked.append('2.2 capped at 1: CURRENT_ENCOUNTER too sparse for >=80-word evidence-grounded HPI without drift')
    if current_words < 30:
        caps['2.2'] = 0
        blocked.append('2.2 capped at 0: very sparse encounter text (<30 words)')

    if current_words < 45:
        caps['3.1'] = 1
        blocked.append('3.1 capped at 1: limited encounter narrative makes robust 60+ word non-list HPI unreliable')
    if current_words < 25:
        caps['3.1'] = 0
        blocked.append('3.1 capped at 0: extremely sparse encounter content (<25 words)')

    # Length constraint without hallucination on very sparse records.
    if total_words < 170:
        caps['3.4'] = 1
        blocked.append('3.4 capped at 1: source data sparse; safe non-hallucinatory note may remain <200 words')

    # Medication line-length rubric false-negative risk.
    if med_long_line_risk(case_txt):
        caps['4.2'] = 1
        blocked.append('4.2 capped at 1: source medication strings likely exceed 8-core-word threshold despite correct formatting')

    # In theory, all cases include dated entries; keep rule explicit for portability.
    if not ISO_DATE.search(case_txt):
        caps['4.3'] = 0
        blocked.append('4.3 capped at 0: no ISO-dated investigations present in source')

    achievable = sum(caps.values())
    return {
        'case_index': case_idx,
        'case_id': f'case_{case_idx:03d}',
        'achievable_max_30': achievable,
        'blocked_items': ' | '.join(blocked) if blocked else 'none',
        'rationale': 'Heuristic cap model based on source completeness and known rubric constraints',
        'caps': caps,
    }


def load_cases():
    return json.loads((INPUTS / 'cases.json').read_text())


def run_sample_indices(n_cases: int, run_id: str):
    m = re.search(r'sample(\d+)', run_id)
    if not m:
        return list(range(n_cases))
    n = int(m.group(1))
    rnd = random.Random(42)
    return sorted(rnd.sample(range(n_cases), n))


def main():
    REPORTS.mkdir(exist_ok=True)

    cases = load_cases()
    gold = [derive_gold(i, c['data']) for i, c in enumerate(cases)]

    # Write gold standards CSV
    csv_path = REPORTS / 'case_gold_standards.csv'
    with csv_path.open('w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['case_index', 'case_id', 'achievable_max_30', 'blocked_items', 'rationale'])
        for row in gold:
            w.writerow([row['case_index'], row['case_id'], row['achievable_max_30'], row['blocked_items'], row['rationale']])

    gold_by_idx = {g['case_index']: g for g in gold}

    # Compute normalized metrics per run.
    normalized_rows = []
    for rp in sorted(ITER.glob('*/report.json')):
        rep = json.loads(rp.read_text())
        run_id = rep.get('run_id', rp.parent.name)
        indices = run_sample_indices(len(cases), run_id)
        case_rows = rep.get('cases', [])
        n = min(len(indices), len(case_rows))
        if n == 0:
            continue
        raw_total = 0.0
        raw_max = 30.0 * n
        norm_ratio_sum = 0.0
        for i in range(n):
            src_idx = indices[i]
            gs = gold_by_idx[src_idx]
            score = float(case_rows[i].get('score_total', 0.0))
            raw_total += score
            denom = gs['achievable_max_30']
            norm_ratio_sum += (score / denom) if denom > 0 else 0.0

        normalized_rows.append({
            'run_id': run_id,
            'n_cases': n,
            'raw_mean_30': round(raw_total / n, 4),
            'normalized_mean_pct': round((norm_ratio_sum / n) * 100, 2),
            'estimated_gold_mean_30': round(sum(gold_by_idx[idx]['achievable_max_30'] for idx in indices[:n]) / n, 4),
            'prompt_file': rep.get('metrics', {}).get('prompt_file'),
        })

    (REPORTS / 'normalized_run_metrics.json').write_text(json.dumps(normalized_rows, indent=2))

    # concise markdown view
    lines = [
        '# Normalized Scoring Metrics',
        '',
        '| run_id | n | raw mean /30 | estimated gold mean /30 | normalized mean % |',
        '|---|---:|---:|---:|---:|',
    ]
    for r in sorted(normalized_rows, key=lambda x: x['normalized_mean_pct'], reverse=True):
        lines.append(f"| {r['run_id']} | {r['n_cases']} | {r['raw_mean_30']} | {r['estimated_gold_mean_30']} | {r['normalized_mean_pct']}% |")
    (REPORTS / 'normalized_score_table.md').write_text('\n'.join(lines) + '\n')

    print('Wrote', csv_path)
    print('Wrote', REPORTS / 'normalized_run_metrics.json')
    print('Wrote', REPORTS / 'normalized_score_table.md')


if __name__ == '__main__':
    main()
