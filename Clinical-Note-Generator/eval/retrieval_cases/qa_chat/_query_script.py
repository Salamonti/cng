import json, sys, os, asyncio
sys.path.insert(0, '/opt/dreamcision/Clinical-Note-Generator')
os.environ.setdefault('RAG_URL', 'http://127.0.0.1:8007')

from server.services.rag_http_client import RAGHttpClient

QUESTIONS = [
    ('q01_bisoprolol_max_dose_hf', 'what is the max dose of bisoprolol in heart failure'),
    ('q02_home_oxygen_copd_criteria', 'when do you start home oxygen in COPD'),
    ('q03_gold_severe_copd_exacerbation', 'GOLD criteria for severe COPD exacerbation'),
    ('q04_apixaban_vs_warfarin_ckd_afib', 'apixaban vs warfarin in atrial fibrillation with CKD'),
    ('q05_anticoag_duration_unprovoked_pe', 'how long to anticoagulate after unprovoked PE'),
    ('q06_sarcoid_cardiac_workup', 'sarcoidosis cardiac involvement workup'),
    ('q07_kdigo_nephrotic_referral', 'KDIGO criteria for nephrotic syndrome referral to nephrology'),
    ('q08_t1d_pump_exercise_settings', 'insulin pump settings for exercise in type 1 diabetes'),
    ('q09_mgus_vs_smoldering_myeloma', 'MGUS vs smoldering myeloma monitoring'),
    ('q10_malignant_ascites_ovarian_ca', 'management of malignant ascites in ovarian cancer'),
]

QA_CHAT_TOP_K = 8  # matches qa_chat.py default qa_chat_rag_top_k

async def main():
    rag = RAGHttpClient('http://127.0.0.1:8007', timeout=90_000)
    out = []
    for key, question in QUESTIONS:
        try:
            ctx, refs, used_filters = await rag.query(question, top_k=QA_CHAT_TOP_K)
        except Exception as e:
            print(f'RAG QUERY FAILED for {key}: {e}', file=sys.stderr)
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
            'key': key,
            'question': question,
            'top_k_requested': QA_CHAT_TOP_K,
            'used_filters': used_filters,
            'num_results': len(refs),
            'results': result_summaries,
        })
        print(f'done: {key} -> {len(refs)} results', file=sys.stderr)

    with open('/opt/dreamcision/Clinical-Note-Generator/eval/_p42_work/qa_chat_rag_results.json', 'w', encoding='utf-8') as f:
        json.dump(out, f, indent=2)

asyncio.run(main())
