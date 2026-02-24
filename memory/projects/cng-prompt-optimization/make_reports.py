#!/usr/bin/env python3
import json
from pathlib import Path

ROOT = Path('/home/solom/.openclaw/workspace/memory/projects/cng-prompt-optimization')
ITER = ROOT / 'iterations'
REPORTS = ROOT / 'reports'
REPORTS.mkdir(exist_ok=True)

runs = []
for p in sorted(ITER.glob('*/report.json')):
    data = json.loads(p.read_text())
    runs.append(data)

rows = []
for r in runs:
    m = r.get('metrics', {})
    rows.append({
        'run_id': r.get('run_id'),
        'n': m.get('n_cases'),
        'mean': m.get('mean_score'),
        'median': m.get('median_score'),
        'stdev': m.get('stdev_score'),
        'lat': m.get('mean_latency_s'),
        'prompt': m.get('prompt_file'),
        'sampler': m.get('sampler'),
    })

rows_sorted = sorted(rows, key=lambda x: (x['mean'] or 0), reverse=True)

lines = []
lines.append('# Optimization Score Table\n')
lines.append('| run_id | n | mean/30 | median | stdev | mean latency s | prompt | sampler |')
lines.append('|---|---:|---:|---:|---:|---:|---|---|')
for r in rows_sorted:
    lines.append(f"| {r['run_id']} | {r['n']} | {r['mean']} | {r['median']} | {r['stdev']} | {r['lat']} | {Path(str(r['prompt'])).name if r['prompt'] else ''} | {json.dumps(r['sampler'])} |")

(REPORTS / 'score_table.md').write_text('\n'.join(lines) + '\n')
(REPORTS / 'score_table.json').write_text(json.dumps(rows_sorted, indent=2))
print('wrote', REPORTS / 'score_table.md')
