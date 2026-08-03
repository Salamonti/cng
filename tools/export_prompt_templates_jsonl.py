"""Export PromptTemplates.cleaned.xlsx -> PCHost/web/prompt_templates.jsonl (t, p, optional e).

Applies curation in-process: remove listed titles, rename ambiguous titles, dedupe,
drop examples with fewer than 10 words.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import sys
from collections import Counter
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[1]
XLSX = REPO / "PromptTemplates.cleaned.xlsx"
OUT = REPO / "PCHost" / "web" / "prompt_templates.jsonl"
SHEET = "Prompts templates"

_eng = REPO / "tools" / "prompt_template_english.py"
_spec = importlib.util.spec_from_file_location("prompt_template_english", _eng)
assert _spec and _spec.loader
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
is_english_template = _mod.is_english_template

# Remove these titles (case-insensitive, stripped).
REMOVE_TITLES = {
    "fampridine follow up assessment",
    "ifs based",
    "dg family physician note",
    "registration with a family",
    "cddc procedures",
    "csps intake form2",
    "cvra (. )",
    "chiropractor soap note",
    "soap (issues) plus d",
    "soap with gp letter",
}


def _norm_title(t: str) -> str:
    return re.sub(r"\s+", " ", (t or "").strip().lower())


def _example_word_count(e: str) -> int:
    return len([w for w in re.split(r"\s+", (e or "").strip()) if w])


def followup_title_from_prompt(p: str) -> str:
    s = p or ""
    if "Substance Use Since Last Visit" in s:
        return "Follow-up — Substance use review"
    if "Reviewed procedure:" in s and "Biopsies:" in s:
        return "Follow-up — GI procedure and biopsies"
    if re.search(r"Include these sections in order:\s*Date:\.", s):
        return "Follow-up — Visit date check"
    if "**Investigations**" in s and "**Examination**" in s:
        return "Follow-up — Investigations and management"
    if "Current Treatments:" in s and "Vaccinations:" in s:
        return "Follow-up — Vaccinations and nutrition"
    if "Problem(s) list:" in s and "Head circumference:" in s:
        return "Follow-up — Paediatric growth review"
    if "Mental Status Examination:" in s and "Risk Assessment:" in s:
        return "Follow-up — Psychiatry MSE and risk"
    if "Blood Pressure:" in s and "Thought Process:" in s:
        return "Follow-up — Vitals and mental exam"
    if "Follow the section order" in s and "Use bullet points" in s:
        return "Follow-up — Flexible order (bullets)"
    if "Follow the section order" in s and "Omit any section" in s and "bullet" not in s.lower():
        return "Follow-up — Flexible order (plain)"
    return "Follow-up — General"


def refine_title(t: str, p: str) -> str:
    nt = _norm_title(t)
    s = p or ""

    if nt == "follow-up":
        return followup_title_from_prompt(s)

    if nt == "assessment note":
        if "Cranial Nerve" in s:
            return "Assessment note — Cranial nerve exam"
        if "Follow the section order" in s:
            return "Assessment note — Flexible layout"
        return t

    if nt == "full skin examination":
        if "Lesions of concern" in s:
            return "Full skin examination — Lesions and plan"
        if "Face:" in s and "Ears:" in s:
            return "Full skin examination — Regional exam"
        return t

    if nt == "joint injection":
        if "Joint injected:" in s:
            return "Joint injection — Procedure detail"
        if "Follow the section order" in s:
            return "Joint injection — Flexible layout"
        return t

    if nt == "gp chronic condition management":
        if "SMART Goals" in s:
            return "Gp chronic condition management — SMART goals and tasks"
        if "Follow the section order" in s:
            return "Gp chronic condition management — Flexible layout"
        return t

    return t


def curate_prompt_templates_jsonl(path: Path) -> int:
    """Read JSONL, apply rules, write back. Returns final line count."""
    lines = path.read_text(encoding="utf-8").splitlines()
    rows: list[dict[str, str]] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            o = json.loads(line)
        except json.JSONDecodeError:
            continue
        t = str(o.get("t", "") or "").strip()
        p = str(o.get("p", "") or "").strip()
        e = str(o.get("e", "") or "").strip()
        if _example_word_count(e) < 10:
            continue
        if _norm_title(t) in REMOVE_TITLES:
            continue
        t = refine_title(t, p)
        rows.append({"t": t, "p": p, "e": e})

    seen: set[str] = set()
    out_rows: list[dict[str, str]] = []
    for r in rows:
        key = hashlib.sha256(
            (r["t"] + "\n" + r["p"] + "\n" + r.get("e", "")).encode("utf-8")
        ).hexdigest()
        if key in seen:
            continue
        seen.add(key)
        out_rows.append(r)

    cnt = Counter(r["t"] for r in out_rows)
    seen_dup: Counter[str] = Counter()
    final: list[dict[str, str]] = []
    for r in out_rows:
        t = r["t"]
        if cnt[t] > 1:
            seen_dup[t] += 1
            if seen_dup[t] >= 2:
                t = f"{t} ({seen_dup[t]})"
        er = r.get("e", "").strip()
        out: dict[str, str] = {"t": t, "p": r["p"]}
        if er:
            out["e"] = er
        final.append(out)

    path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in final) + "\n",
        encoding="utf-8",
    )
    return len(final)


def main() -> None:
    if not XLSX.is_file():
        raise SystemExit(f"Missing {XLSX}")
    df = pd.read_excel(XLSX, sheet_name=SHEET)
    n = 0
    skipped = 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8") as f:
        for _, row in df.iterrows():
            t = str(row.get("Title", "") or "").strip()
            p = str(row.get("Prompt", "") or "").strip()
            e = str(row.get("Examples", "") or "").strip()
            if not t and not p:
                continue
            if not is_english_template(t, p):
                skipped += 1
                continue
            obj: dict[str, str] = {"t": t, "p": p}
            if e:
                obj["e"] = e
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")
            n += 1
    print(f"Wrote {n} lines (dropped {skipped} titles with accented vowels or non-Latin script) -> {OUT}")
    try:
        final_n = curate_prompt_templates_jsonl(OUT)
        print(f"After curation: {final_n} lines -> {OUT}")
    except OSError as e:
        print(f"Warning: curation failed: {e}", file=sys.stderr)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] in ("--curate-only", "-c"):
        n = curate_prompt_templates_jsonl(OUT)
        print(f"After curation: {n} lines -> {OUT}")
    else:
        main()
