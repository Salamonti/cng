# LLM Routing Rules (Fail-Fast + Cost-Aware)

Last updated: 2026-02-25 (UTC)
Owner: Islam

## Core principle
Pick the cheapest model likely to succeed, then escalate quickly without loops.

## Decision table
- ASR task (audio/speech) -> Whisper endpoint first.
- OCR/doc image text task -> OCR endpoint first.
- Routine task (draft/summarize/transform) -> Ministral14 first.
- If first output weak/incorrect -> escalate immediately (no repeated retries on same tier).
- Coding/debug or multi-step tool workflow -> Codex 5.3.
- High-stakes reasoning/writing -> Claude Sonnet 4.6.
- Mission-critical final quality / high ambiguity -> Claude Opus 4.6.
- Optional cross-check for critical decisions -> one second-opinion run on a different top-tier model.

## Retry and escalation caps
- Max retries per tier: 1.
- After 2 failed attempts overall on a path: stop and switch strategy.
- Always provide:
  1) likely root cause,
  2) next best model/path,
  3) explicit user action needed (if any).

## Cost guardrails
- Don’t use Opus for routine tasks.
- Use premium tiers mainly for final-pass verification or explicit user request.
- Prefer specialized endpoints (Whisper/OCR) to avoid expensive misuse of general LLMs.

## Communication rule
For non-trivial tasks, report model choice + short rationale.
