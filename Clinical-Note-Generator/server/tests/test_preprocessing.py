import json
import re
import time
from pathlib import Path

import pytest

from server.core.preprocessing.pipeline import PreprocessingPipeline
from server.core.preprocessing.truncation import TokenBudgetTruncator
from server.core.prompt import builder as prompt_builder


def _cfg(enabled: bool = True) -> dict:
    return {
        "preprocessing": {
            "enabled": enabled,
            "steps": {
                "remove_boilerplate": True,
                "collapse_repeated_headers": True,
                "remove_junk_artifacts": True,
                "deduplicate_blocks": True,
                "normalize_whitespace": True,
            },
            "truncation": {
                "prior_visits_budget_tokens": 60,
                "labs_imaging_other_budget_tokens": 60,
                "current_encounter_budget_tokens": 4096,
            },
        }
    }


def _extract_section(full_text: str, section: str) -> str:
    match = re.search(rf"<{section}>\n(.*?)\n</{section}>", full_text or "", re.DOTALL)
    return match.group(1) if match else ""


def test_preprocessing_pipeline_steps():
    pipeline = PreprocessingPipeline(_cfg(True))
    text = (
        "CONFIDENTIAL: DO NOT DISTRIBUTE. Page 1 of 2\n"
        "Test Performed At YARMOUTH REGIONAL HOSPITAL\n"
        "Page 1 of 2\n"
        "Patient: TEST, EXAMPLE MRN: 12345 Visit: 99\n"
        "BP 140/90 mmHg\n"
        "----\n"
        "\n"
        "BP 140/90 mmHg\n"
        "\n"
        "Result Name Results Units Reference Range\n"
        "WBC 10.2\t10e9/L\n"
        "\n\n"
    )

    out = pipeline.process(text)

    assert "CONFIDENTIAL" not in out
    assert "Test Performed At" not in out
    assert "Page 1 of 2" not in out
    assert "Result Name Results Units Reference Range" not in out
    assert out.count("BP 140/90 mmHg") == 1
    assert "WBC 10.2 10e9/L" in out


def test_truncation_respects_budget_and_prioritizes_dates():
    truncator = TokenBudgetTruncator(_cfg(True))
    text = "\n\n".join(
        [
            "2020-01-01 old follow up no numbers",
            "2024-03-01 BP 165/90 HR 110 chest pain 8/10",
            "2018-01-01 unrelated short",
            "2025-12-15 troponin 0.12 ng/mL ECG ST changes",
            "Some filler paragraph with very little signal",
        ]
    )

    out = truncator.truncate_section(text, "prior_visits")
    assert truncator.estimate_tokens(out) <= truncator.budgets["prior_visits"]
    assert "2025-12-15" in out
    assert "troponin 0.12" in out


def test_integration_with_prompt_builder(monkeypatch):
    cfg = _cfg(True)
    cfg["preprocessing"]["truncation"]["prior_visits_budget_tokens"] = 20
    monkeypatch.setattr(prompt_builder, "load_config", lambda: cfg)

    prompt = prompt_builder.build_prompt_v8(
        transcription_text="  Today   patient stable.  ",
        old_visits_text=(
            "CONFIDENTIAL: DO NOT DISTRIBUTE. Page 1 of 2\n"
            "2021-01-01 old item\n\n"
            "2025-11-01 BP 160/100 HR 108\n\n"
            "2020-01-01 low signal"
        ),
        mixed_other_text="Page 1 of 3\nWBC 15.2 10e9/L\n\nWBC 15.2 10e9/L",
        note_type="consult",
    )

    assert "CONFIDENTIAL" not in prompt
    assert "Page 1 of 3" not in prompt
    assert "Today patient stable." in prompt
    assert "2025-11-01 BP 160/100 HR 108" in prompt


def test_performance_overhead_under_100ms():
    pipeline = PreprocessingPipeline(_cfg(True))
    truncator = TokenBudgetTruncator(_cfg(True))

    lines = [f"Page {i} of 50" if i % 15 == 0 else f"2025-01-{(i % 28) + 1:02d} WBC {i/10:.1f} 10e9/L" for i in range(1, 401)]
    blob = "\n".join(lines)

    runs = 20
    start = time.perf_counter()
    for _ in range(runs):
        processed = pipeline.process(blob)
        truncator.truncate_section(processed, "prior_visits")
    elapsed_ms = ((time.perf_counter() - start) / runs) * 1000

    print(f"preprocessing average overhead_ms={elapsed_ms:.2f}")
    assert elapsed_ms < 100.0


def test_evaluation_harness_reports_metrics(capsys):
    cases_path = Path("/home/solom/.openclaw/workspace/memory/projects/cng-prompt-optimization/inputs/cases.json")
    if not cases_path.exists():
        pytest.skip("cases.json not available")

    with cases_path.open("r", encoding="utf-8") as fh:
        cases = json.load(fh)

    eval_cfg = _cfg(True)
    eval_cfg["preprocessing"]["truncation"]["prior_visits_budget_tokens"] = 1024
    eval_cfg["preprocessing"]["truncation"]["labs_imaging_other_budget_tokens"] = 1024
    pipeline = PreprocessingPipeline(eval_cfg)
    truncator = TokenBudgetTruncator(eval_cfg)

    scored_cases = []
    for case in cases:
        data = case.get("data", "")
        prior = _extract_section(data, "PRIOR_VISITS")
        other = _extract_section(data, "LABS_IMAGING_OTHER")
        combined = "\n\n".join([x for x in [prior, other] if x.strip()])
        scored_cases.append((len(combined), case))
    selected = [case for _, case in sorted(scored_cases, key=lambda x: x[0], reverse=True)[:10]]
    total_before_chars = 0
    total_after_chars = 0
    total_before_tokens = 0
    total_after_tokens = 0
    total_ms = 0.0

    kept_date_hits = 0
    kept_numeric_hits = 0

    for case in selected:
        data = case.get("data", "")
        prior = _extract_section(data, "PRIOR_VISITS")
        other = _extract_section(data, "LABS_IMAGING_OTHER")
        combined = "\n\n".join([x for x in [prior, other] if x.strip()])

        before_chars = len(combined)
        before_tokens = truncator.estimate_tokens(combined)

        t0 = time.perf_counter()
        prior_proc = truncator.truncate_section(pipeline.process(prior), "prior_visits")
        other_proc = truncator.truncate_section(pipeline.process(other), "labs_imaging_other")
        elapsed = (time.perf_counter() - t0) * 1000

        after = "\n\n".join([x for x in [prior_proc, other_proc] if x.strip()])
        after_chars = len(after)
        after_tokens = truncator.estimate_tokens(after)

        total_before_chars += before_chars
        total_after_chars += after_chars
        total_before_tokens += before_tokens
        total_after_tokens += after_tokens
        total_ms += elapsed

        date_before = len(re.findall(r"\b\d{4}-\d{2}-\d{2}\b", combined))
        date_after = len(re.findall(r"\b\d{4}-\d{2}-\d{2}\b", after))
        num_before = len(re.findall(r"\b\d+(?:\.\d+)?\b", combined))
        num_after = len(re.findall(r"\b\d+(?:\.\d+)?\b", after))

        if date_before == 0 or date_after > 0:
            kept_date_hits += 1
        if num_before == 0 or num_after > 0:
            kept_numeric_hits += 1

    reduction = 0.0
    if total_before_tokens > 0:
        reduction = 1.0 - (total_after_tokens / total_before_tokens)
    avg_ms = total_ms / max(1, len(selected))

    print("=== Phase 3 Preprocessing Report (10 cases) ===")
    print(
        f"chars_before={total_before_chars} chars_after={total_after_chars} "
        f"tokens_before={total_before_tokens} tokens_after={total_after_tokens} "
        f"size_reduction={reduction * 100:.2f}% avg_latency_ms={avg_ms:.2f} "
        f"date_retention_cases={kept_date_hits}/10 numeric_retention_cases={kept_numeric_hits}/10"
    )

    captured = capsys.readouterr()
    assert "Phase 3 Preprocessing Report" in captured.out
    assert reduction >= 0.25
    assert avg_ms < 100.0
    assert kept_date_hits >= 9
    assert kept_numeric_hits >= 6
