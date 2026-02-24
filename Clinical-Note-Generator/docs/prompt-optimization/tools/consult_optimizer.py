#!/usr/bin/env python3
import argparse
import datetime as dt
import json
import os
import random
import re
import statistics
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, List, Tuple

import urllib.request

ROOT = Path('/home/solom/.openclaw/workspace/memory/projects/cng-prompt-optimization')
INPUTS = ROOT / 'inputs'
ITERATIONS = ROOT / 'iterations'
REPORTS = ROOT / 'reports'

REQUIRED_HEADINGS = [
    'Patient ID',
    'History of Present Illness',
    'Past Medical History',
    'Medications',
    'Allergies',
    'Family History',
    'Social History',
    'Physical Examination',
    'Investigations',
    'Impression',
    'Plan',
]

PLAN_FORBIDDEN_IN_HPI = re.compile(r"\b(i will|we will|restart|increase|decrease|order|refer|arrange|prescribe|follow-up)\b", re.I)
FIRST_PERSON = re.compile(r"\b(i will|we will|i am|we are)\b", re.I)
BULLET_LINE = re.compile(r"^\s*([-*]|\d+\.)\s+", re.M)
BAD_DATE = re.compile(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b")
ISO_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")


@dataclass
class SamplerConfig:
    temperature: float = 0.2
    top_p: float = 0.9
    max_tokens: int = 6000


@dataclass
class CaseResult:
    idx: int
    case_id: str
    latency_s: float
    score_total: float
    score_breakdown: Dict[str, float]
    failures: List[str]


def load_cases(path: Path) -> List[dict]:
    return json.loads(path.read_text())


def call_model(endpoint: str, model: str, system_prompt: str, user_prompt_template: str, case_text: str, sampler: SamplerConfig, timeout: int = 240) -> Tuple[str, float]:
    user_prompt = user_prompt_template.replace('{case_text}', case_text)
    current_date = dt.datetime.utcnow().strftime('%Y-%m-%d')
    system_prompt = system_prompt.replace('{CURRENT_DATE}', current_date)

    payload = {
        'model': model,
        'messages': [
            {'role': 'system', 'content': system_prompt},
            {'role': 'user', 'content': user_prompt},
        ],
        'temperature': sampler.temperature,
        'top_p': sampler.top_p,
        'max_tokens': sampler.max_tokens,
    }
    t0 = time.time()
    req = urllib.request.Request(
        endpoint.rstrip('/') + '/v1/chat/completions',
        data=json.dumps(payload).encode('utf-8'),
        headers={'Content-Type': 'application/json'},
        method='POST',
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode('utf-8'))
    txt = data['choices'][0]['message']['content']
    return txt, time.time() - t0


def split_prompt(prompt_text: str) -> Tuple[str, str]:
    sys_marker = 'SYSTEM PROMPT'
    user_marker = 'USER PROMPT'
    i = prompt_text.find(sys_marker)
    j = prompt_text.find(user_marker)
    if i < 0 or j < 0:
        raise ValueError('Prompt file format not recognized')
    sys_text = prompt_text[i + len(sys_marker):j]
    user_text = prompt_text[j + len(user_marker):]
    # remove decoration lines
    sys_text = re.sub(r'^=+\n?', '', sys_text.strip(), flags=re.M)
    user_text = re.sub(r'^=+\n?', '', user_text.strip(), flags=re.M)
    return sys_text.strip(), user_text.strip()


def extract_final_note(full_output: str) -> str:
    m = re.search(r'(^|\n)Patient ID\s*:?', full_output)
    if m:
        return full_output[m.start() + (1 if full_output[m.start()] == '\n' else 0):].strip()
    # fallback if Stage 7 label appears
    m2 = re.search(r'STAGE\s*7[^\n]*\n', full_output, re.I)
    if m2:
        return full_output[m2.end():].strip()
    return full_output.strip()


def section_map(note: str) -> Dict[str, str]:
    positions = []
    for h in REQUIRED_HEADINGS:
        m = re.search(rf'(?m)^{re.escape(h)}\s*:?', note)
        if m:
            positions.append((h, m.start(), m.end()))
    positions.sort(key=lambda x: x[1])
    out = {}
    for i, (h, s, e) in enumerate(positions):
        line_end = note.find('\n', e)
        if line_end == -1:
            line_end = len(note)
        inline = note[e:line_end].strip()
        end = positions[i + 1][1] if i + 1 < len(positions) else len(note)
        body = note[line_end:end].strip() if line_end < end else ''
        if inline:
            out[h] = (inline + '\n' + body).strip()
        else:
            out[h] = body.strip()
    return out


def count_sentences(text: str) -> int:
    return len([x for x in re.split(r'(?<=[.!?])\s+', text.strip()) if x.strip()])


def med_line_core_words(line: str) -> int:
    core = re.sub(r'\([^)]*\)', '', line)
    return len([w for w in core.split() if w.strip()])


def score_note(note: str) -> Tuple[float, Dict[str, float], List[str]]:
    sec = section_map(note)
    failures = []
    scores = {}

    # 1.8 hallucination proxy
    angle = len(re.findall(r'[<>]', note))
    sq = len(re.findall(r'[\[\]]', note))
    if angle == 0 and sq == 0:
        scores['1.8'] = 2
    elif angle + sq <= 2:
        scores['1.8'] = 1
        failures.append('1.8 minor forbidden markers')
    else:
        scores['1.8'] = 0
        failures.append('1.8 hallucination/markers')

    # 2.1 sections present
    missing = [h for h in REQUIRED_HEADINGS if h not in sec]
    if len(missing) == 0:
        scores['2.1'] = 2
    elif len(missing) == 1:
        scores['2.1'] = 1
        failures.append('2.1 one section missing')
    else:
        scores['2.1'] = 0
        failures.append(f'2.1 sections missing: {missing}')

    hpi = sec.get('History of Present Illness', '')
    hpi_wc = len(hpi.split())
    hpi_forbidden = len(PLAN_FORBIDDEN_IN_HPI.findall(hpi))
    if hpi_wc >= 80 and hpi_forbidden == 0:
        scores['2.2'] = 2
    elif hpi_wc >= 40 and hpi_forbidden <= 1:
        scores['2.2'] = 1
        failures.append('2.2 weak HPI completeness')
    else:
        scores['2.2'] = 0
        failures.append('2.2 poor HPI completeness')

    imp = sec.get('Impression', '')
    imp_sent = count_sentences(imp)
    imp_bul = len(BULLET_LINE.findall(imp))
    if 2 <= imp_sent <= 6 and imp_bul == 0 and len(imp) > 50:
        scores['2.3'] = 2
    elif imp_sent == 1 or (1 <= imp_bul <= 2):
        scores['2.3'] = 1
        failures.append('2.3 impression marginal')
    else:
        scores['2.3'] = 0
        failures.append('2.3 impression poor')

    nd_sections = ['Allergies', 'Family History', 'Physical Examination']
    blanks = 0
    for h in nd_sections:
        txt = sec.get(h, '').strip().lower()
        if not txt:
            blanks += 1
        elif txt in {'not documented', 'none documented', 'not available'}:
            pass
    if blanks == 0:
        scores['2.4'] = 2
    elif blanks == 1:
        scores['2.4'] = 1
        failures.append('2.4 one blank section')
    else:
        scores['2.4'] = 0
        failures.append('2.4 multiple blank sections')

    scores['2.5'] = 1

    # 3.1
    hpi_bullets = len(BULLET_LINE.findall(hpi))
    if hpi_bullets == 0 and hpi_wc >= 60:
        scores['3.1'] = 2
    elif 1 <= hpi_bullets <= 2:
        scores['3.1'] = 1
        failures.append('3.1 bullets in HPI')
    else:
        scores['3.1'] = 0
        failures.append('3.1 HPI list-like/short')

    scores['3.2'] = 1

    plan = sec.get('Plan', '')
    fp = len(FIRST_PERSON.findall(plan))
    if fp >= 3:
        scores['3.3'] = 2
    elif fp >= 1:
        scores['3.3'] = 1
        failures.append('3.3 weak first-person')
    else:
        scores['3.3'] = 0
        failures.append('3.3 no first-person')

    total_wc = len(note.split())
    if 200 <= total_wc <= 1000:
        scores['3.4'] = 2
    elif 100 <= total_wc <= 199:
        scores['3.4'] = 1
        failures.append('3.4 short note')
    else:
        scores['3.4'] = 0
        failures.append('3.4 bad length')

    if note.rstrip().endswith('...') or note.rstrip().endswith('…'):
        scores['3.5'] = 0
        failures.append('3.5 truncated')
    else:
        scores['3.5'] = 2

    # 4.1 section order
    idxs = []
    for h in REQUIRED_HEADINGS:
        m = re.search(rf'(?m)^{re.escape(h)}\s*:?', note)
        idxs.append(m.start() if m else -1)
    present = [i for i in idxs if i >= 0]
    in_order = all(present[i] <= present[i+1] for i in range(len(present)-1))
    if len(present) == len(REQUIRED_HEADINGS) and in_order:
        scores['4.1'] = 2
    elif in_order and len(present) >= 9:
        scores['4.1'] = 1
        failures.append('4.1 section partially missing')
    else:
        scores['4.1'] = 0
        failures.append('4.1 bad order')

    # 4.2 medication format
    med = sec.get('Medications', '')
    if med.strip().lower() == 'not documented':
        overlong = 0
    else:
        lines = [ln.strip() for ln in med.splitlines() if ln.strip()]
        overlong = sum(1 for ln in lines if med_line_core_words(ln) > 8)
    if med.strip() and overlong == 0:
        scores['4.2'] = 2
    elif overlong == 1:
        scores['4.2'] = 1
        failures.append('4.2 one overlong med line')
    else:
        scores['4.2'] = 0
        failures.append('4.2 meds format bad/empty')

    # 4.3 investigations format
    inv = sec.get('Investigations', '')
    has_iso = bool(ISO_DATE.search(inv))
    has_bad = bool(BAD_DATE.search(inv))
    has_pending = bool(re.search(r'\bpending\b', inv, re.I))
    if has_iso and not has_bad and not has_pending:
        scores['4.3'] = 2
    elif has_iso:
        scores['4.3'] = 1
        failures.append('4.3 investigations has format issues')
    else:
        scores['4.3'] = 0
        failures.append('4.3 investigations no iso date')

    # 4.4 plan format
    numbered = len(re.findall(r'(?m)^\s*\d+\.\s+', plan))
    fp2 = len(FIRST_PERSON.findall(plan))
    if numbered >= 3 and fp2 >= 2:
        scores['4.4'] = 2
    elif numbered >= 2 and fp2 >= 1:
        scores['4.4'] = 1
        failures.append('4.4 plan marginal')
    else:
        scores['4.4'] = 0
        failures.append('4.4 plan format fail')

    sq_only = len(re.findall(r'[\[\]]', note))
    if sq_only == 0:
        scores['4.5'] = 2
    elif sq_only <= 2:
        scores['4.5'] = 1
        failures.append('4.5 minor brackets')
    else:
        scores['4.5'] = 0
        failures.append('4.5 many brackets')

    total = sum(scores.values())
    return total, scores, failures


def run_eval(run_id: str, prompt_path: Path, endpoint: str, model: str, sampler: SamplerConfig, sample_n: int = 0, seed: int = 42):
    all_cases = load_cases(INPUTS / 'cases.json')
    selected_indices = list(range(len(all_cases)))
    if sample_n and sample_n < len(all_cases):
        rnd = random.Random(seed)
        selected_indices = sorted(rnd.sample(range(len(all_cases)), sample_n))
    cases = [all_cases[i] for i in selected_indices]
    prompt_text = prompt_path.read_text()
    system_prompt, user_prompt = split_prompt(prompt_text)

    out_dir = ITERATIONS / run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    results: List[dict] = []
    for i, case in enumerate(cases):
        src_idx = selected_indices[i]
        try:
            raw, latency = call_model(endpoint, model, system_prompt, user_prompt, case['data'], sampler)
            note = extract_final_note(raw)
            total, breakdown, failures = score_note(note)
            rec = asdict(CaseResult(i, str(case.get('case_id', '')), latency, total, breakdown, failures))
            rec['source_index'] = src_idx
            rec['source_case_id'] = f'case_{src_idx:03d}'
            results.append(rec)
            (out_dir / f'case_{i:03d}.txt').write_text(note)
        except Exception as e:
            rec = asdict(CaseResult(i, str(case.get('case_id', '')), 0.0, 0.0, {}, [f'exception: {e}']))
            rec['source_index'] = src_idx
            rec['source_case_id'] = f'case_{src_idx:03d}'
            results.append(rec)

    metrics = {}
    if results:
        totals = [r['score_total'] for r in results]
        lats = [r['latency_s'] for r in results if r['latency_s'] > 0]
        metrics = {
            'n_cases': len(results),
            'mean_score': round(statistics.mean(totals), 4),
            'median_score': round(statistics.median(totals), 4),
            'stdev_score': round(statistics.pstdev(totals), 4),
            'min_score': min(totals),
            'max_score': max(totals),
            'mean_latency_s': round(statistics.mean(lats), 3) if lats else None,
            'sampler': asdict(sampler),
            'prompt_file': str(prompt_path),
        }

    # aggregate item means
    items = sorted({k for r in results for k in r['score_breakdown'].keys()})
    item_means = {}
    for k in items:
        vals = [r['score_breakdown'].get(k, 0) for r in results]
        item_means[k] = round(statistics.mean(vals), 4)

    fail_counter: Dict[str, int] = {}
    for r in results:
        for f in r['failures']: 
            key = f.split(':')[0]
            fail_counter[key] = fail_counter.get(key, 0) + 1

    report = {
        'run_id': run_id,
        'timestamp_utc': dt.datetime.utcnow().isoformat() + 'Z',
        'metrics': metrics,
        'item_means': item_means,
        'top_failures': sorted(fail_counter.items(), key=lambda x: x[1], reverse=True)[:20],
        'cases': results,
    }

    (out_dir / 'report.json').write_text(json.dumps(report, indent=2))
    return report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--endpoint', default='http://ieissa.com:8081')
    ap.add_argument('--model', default='Ministral-3-14B-Instruct-2512-Q5_K_M.gguf')
    ap.add_argument('--prompt', default=str(INPUTS / 'prompt_v2.txt'))
    ap.add_argument('--run-id', required=True)
    ap.add_argument('--sample-n', type=int, default=0)
    ap.add_argument('--temperature', type=float, default=0.2)
    ap.add_argument('--top-p', type=float, default=0.9)
    ap.add_argument('--max-tokens', type=int, default=6000)
    args = ap.parse_args()

    sampler = SamplerConfig(args.temperature, args.top_p, args.max_tokens)
    report = run_eval(args.run_id, Path(args.prompt), args.endpoint, args.model, sampler, sample_n=args.sample_n)
    print(json.dumps({'run_id': args.run_id, 'metrics': report['metrics'], 'item_means': report['item_means']}, indent=2))


if __name__ == '__main__':
    main()
