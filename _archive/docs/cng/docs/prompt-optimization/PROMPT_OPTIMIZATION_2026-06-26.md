# Prompt Optimization — 2026-06-26

## Summary

Consolidated and tightened all default prompts (system + user) for both main and "other" note types. Removed contradictions, eliminated duplication, reorganized by importance, and strengthened guardrails.

## Results

| Metric | Before | After | Savings |
|--------|--------|-------|---------|
| System (main) | 37 lines, 13,221 chars (~3,305 tokens) | 14 lines, 7,022 chars (~1,755 tokens) | 47% |
| System (other) | 18 lines, 3,890 chars (~972 tokens) | 10 lines, 3,362 chars (~840 tokens) | 14% |
| Max single prompt (sys + user) | ~21,007 chars (~5,251 tokens) | ~13,483 chars (~3,370 tokens) | 36% |

## Changes Made

### System Prompt (Main) — 37 lines → 14 lines

1. **Fixed table contradiction** — Line 5 said "tables" were OK, Line 36 banned them. Removed "tables" from the formatting line.

2. **Consolidated Conflicts rules** — 7+ scattered rules → 1 consolidated block covering: when to include, heading format, content rules, numbering, "conflicts-only" scope, no action language.

3. **Consolidated grounding/anti-hallucination** — 4 separate rules → 1 block: only use explicit source data, do not invent, garbled data handling, transcription quality.

4. **Consolidated anti-meta-commentary** — 3 rules → 1: no scratchpad, no chain-of-thought, no model deliberation.

5. **Consolidated "Conflicts only" / uncertainty placement** — 5 instances → 1 global rule.

6. **Consolidated Physical Exam rules** — 2 instances → 1 block.

7. **Consolidated Patient ID rules** — 8 lines → 1 block (honorifics, age, DOB, placeholders, identifiers).

8. **Flipped negative → positive framing** — e.g., "Do not add uncertainty markers inline" → "Place all uncertainty exclusively in the Conflicts section".

9. **Reordered rules by importance** — Role → Voice → Grounding → Conflicts → Patient ID → Physical Exam → Formatting → Medications → HPI → Section discipline → Numeric style.

### System Prompt (Other) — 18 lines → 10 lines

10. **Strengthened guardrails** — Added missing rules from main system prompt: garbled data handling, no tag echo, consolidated Conflicts block, anti-meta.

### User Prompts — Removed Duplication

11. **Removed from all user prompts:**
    - "Conflicts section format: The Conflicts title line must be exactly..." (was in 7 user prompts)
    - "Patient Identification must stay free of uncertainty narration..." (was in 5 user prompts)
    - "Physical Exam (mandatory scan of <CURRENT_ENCOUNTER>): Include only documented findings..." (was in 4 user prompts)
    - "Do not invent diagnoses, conclusions, or management decisions." (was in 5 user prompts)
    - "Conflicts section usage: Add a Conflicts section only when..." (was in 7 user prompts)
    - "Omit any section that is not supported by the source material..." (was in 4 user prompts)
    - "Patient Identification must be exactly one factual sentence..." (was in 2 user prompts)

### Builder Code (builder.py)

12. **Removed NUMERIC_UNIT_STYLE_INSTRUCTION** — Was appended to every prompt by builder.py, duplicating the system prompt's numeric/units rule. Removed the constant and all 3 append calls (build_prompt_v8, build_prompt_other, build_note_prompt_legacy).

13. **Removed STYLE REQUIREMENTS block** — No longer appended since the rule lives in the system prompt.

## Files Changed

- `/opt/dreamcision/Clinical-Note-Generator/config/config.json` — All prompt templates
- `/opt/dreamcision/Clinical-Note-Generator/server/core/prompt/builder.py` — Removed NUMERIC_UNIT_STYLE_INSTRUCTION and STYLE REQUIREMENTS blocks

## Parsing Compatibility

All changes are parsing-safe. Verified against:
- Backend: `_is_conflicts_heading_line()`, `_is_markdown_section_heading_line()`, `strip_conflicts_section()`, `_extract_plan_section()`, `parse_note_sections()`
- Frontend: `isConflictsHeadingLine()`, `extractConflictsSection()`, `renderMarkdownSimple()`

No changes to `## ` heading format, Conflicts heading regex, or section structure.

## Dead Code

`build_note_prompt_legacy()` in builder.py and its wrapper in notes.py are NOT called anywhere. Left in place — not part of this optimization.

## Backup

Original config backed up to: `/opt/dreamcision/Clinical-Note-Generator/config/config.json.bak`
