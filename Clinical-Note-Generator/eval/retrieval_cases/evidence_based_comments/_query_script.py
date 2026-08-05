import json, glob, sys, os, asyncio
sys.path.insert(0, '/opt/dreamcision/Clinical-Note-Generator')
os.environ.setdefault('RAG_URL', 'http://127.0.0.1:8007')

from server.services.consult_focus_builder import build_consult_focus
from server.services.rag_http_client import RAGHttpClient

DATA_DIR = '/opt/dreamcision/Clinical-Note-Generator/data/datasets'

SELECTED = [
    'a66b681a0e09',  # nephrotic syndrome / proteinuric kidney disease
    'aa2bd4474ecc',  # severe COPD with persistent hypoxia
    'f3834f3c7d29',  # type 1 diabetes management, insulin pump
    'e767671ecba8',  # atrial fibrillation anticoagulation
    '14aa74860833',  # recurrent pulmonary embolism
    'ff1b282119a1',  # sarcoidosis
    '2536b702a4e6',  # CHF + atrial fibrillation
    'ccc10db55a3e',  # MGUS
    '0e86968a0dc2',  # acute symptomatic anemia
    '7eed06b829d7',  # ovarian cancer with recurrent ascites
]

def find_record(short_id):
    for fp in sorted(glob.glob(os.path.join(DATA_DIR, 'cases_*.jsonl'))):
        with open(fp, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                cid = rec.get('case_id') or ''
                if cid.startswith(short_id):
                    return rec, os.path.basename(fp)
    return None, None

async def main():
    rag = RAGHttpClient('http://127.0.0.1:8007', timeout=90_000)
    out = []
    for short_id in SELECTED:
        rec, fp = find_record(short_id)
        if not rec:
            print(f'MISSING: {short_id}', file=sys.stderr)
            continue
        note = ((rec.get('output_deid') or {}).get('note')) or ''
        focus, used_sections = build_consult_focus(note, strategy='sections')
        focus_word_count = len(focus.split())
        base_top_k = 16
        consult_cap = 5
        requested_top_k = base_top_k
        if focus_word_count >= max(90, 150):
            requested_top_k = min(base_top_k, consult_cap)
        requested_top_k = max(3, requested_top_k)

        try:
            ctx, refs, used_filters = await rag.query(focus, top_k=requested_top_k)
        except Exception as e:
            print(f'RAG QUERY FAILED for {short_id}: {e}', file=sys.stderr)
            ctx, refs, used_filters = '', [], {'error': str(e)}

        result_summaries = []
        for r in refs:
            md = r.get('metadata', {}) or {}
            result_summaries.append({
                'title': md.get('title'),
                'source': md.get('source'),
                'year': md.get('year'),
                'section': md.get('section'),
                'link': md.get('link'),
                'tier': r.get('tier'),
                'score': r.get('score'),
                'text_preview': (r.get('text') or '')[:400],
            })

        out.append({
            'case_id': rec.get('case_id'),
            'note_type': rec.get('note_type'),
            'source_file': fp,
            'focus_query': focus,
            'used_sections': used_sections,
            'top_k_requested': requested_top_k,
            'used_filters': used_filters,
            'num_results': len(refs),
            'results': result_summaries,
        })
        print(f"done: {short_id} -> {len(refs)} results, top_k={requested_top_k}", file=sys.stderr)

    with open('/opt/dreamcision/Clinical-Note-Generator/eval/_p42_work/ebc_rag_results.json', 'w', encoding='utf-8') as f:
        json.dump(out, f, indent=2)

asyncio.run(main())
