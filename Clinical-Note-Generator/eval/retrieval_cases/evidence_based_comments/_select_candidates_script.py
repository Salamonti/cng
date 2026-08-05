import json, glob, sys, os
sys.path.insert(0, '/opt/dreamcision/Clinical-Note-Generator')
from server.services.consult_focus_builder import extract_section_by_heading, build_consult_focus

DATA_DIR = '/opt/dreamcision/Clinical-Note-Generator/data/datasets'
files = sorted(glob.glob(os.path.join(DATA_DIR, 'cases_*.jsonl')))

candidates = []
for fp in files:
    with open(fp, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            nt = rec.get('note_type')
            if nt not in ('consult', 'followup'):
                continue
            note = ((rec.get('output_deid') or {}).get('note')) or ''
            if not note or len(note) < 400:
                continue
            imp = extract_section_by_heading(note, 'Impression', aliases=['Assessment','Diagnosis','Impressions'])
            plan = extract_section_by_heading(note, 'Plan', aliases=['Management','Recommendations','Plan of Care','Treatment Plan'])
            combined_words = len((imp + ' ' + plan).split())
            if combined_words < 60:
                continue
            candidates.append({
                'case_id': rec.get('case_id'),
                'note_type': nt,
                'source_file': os.path.basename(fp),
                'created_at': rec.get('created_at'),
                'imp_preview': imp[:160].replace(chr(10), ' '),
                'combined_words': combined_words,
            })

print(f'Total candidates: {len(candidates)}', file=sys.stderr)
with open('/opt/dreamcision/Clinical-Note-Generator/eval/_p42_work/candidates.json', 'w', encoding='utf-8') as out:
    json.dump(candidates, out, indent=2)
for c in candidates[:200]:
    print(f"{c['source_file']} | {c['note_type']} | {c['combined_words']}w | {c['case_id'][:12]} | {c['imp_preview']}")
