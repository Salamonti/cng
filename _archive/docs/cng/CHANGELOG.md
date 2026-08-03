# DreamCision AI Scribe — Changelog

## [2.0.0] — 2026-06-26

### New Features

- **Password Recovery** — Self-service password reset via email (Resend API)
- **ASR Diarization** — Speaker diarization for multi-speaker recordings
- **ASR Streaming (Chunking)** — Real-time chunked transcription with SSE streaming
- **Default Note Type Selection** — Choose which note type is pre-selected for new encounters
- **Note Type Visibility** — Hide or show specific note types per user preference
- **Patient Materials** — On-demand generation of patient-facing materials (diet plans, exercise plans, health reports, medication guides)
- **Evidence-Based Comments (Overhaul)** — Complete Phase 1-6 rewrite: structured JSON output, severity/confidence scoring, parallel RAG + SearXNG evidence, medication safety + red flag detection, in-memory caching, configurable depth (quick/comprehensive)
- **Order & Referral Requests** — New feature for generating order and referral letters

### Prompt & Output Improvements

- **System Prompt Optimization** — Consolidated from 37 lines to 14 (62% reduction), removed contradictions, unified Conflicts/grounding/anti-meta rules
- **"Other" System Prompt** — Strengthened with missing guardrails (was minimal)
- **User Prompt Cleanup** — Removed duplicated rules from user prompts (Conflicts format, Physical Exam, Patient ID, grounding)
- **Builder.py Cleanup** — Removed redundant NUMERIC_UNIT_STYLE_INSTRUCTION and STYLE REQUIREMENTS blocks
- **Dead Code Removal** — Removed build_note_prompt_legacy (never called)

### Bug Fixes

- **Visual/Layout Fixes** — Sticky header overlap, modal tab buttons on mobile, evidence banner persistence across encounters
- **Orders & Prompts Section** — Fixed rendering and functionality issues
- **Table Contradiction** — Resolved conflicting table formatting rules in prompts
- **Encounter-Scoped Data Clearing** — Fixed stale data persisting across encounter switches

### Infrastructure

- **Medication Correction Pipeline** — Two-layer hybrid: Layer 1 (dictionary with context guards) + Layer 2 (RxNorm normalizer with phonetic matching)
- **RxNorm Activation** — 94,171 terms loaded, type-specific thresholds, Double Metaphone phonetic fallback
- **Service Worker v103** — Updated cache strategy, never-cache for ASR assets
- **Cloudflare Tunnel** — HTTP origin optimization (TTFB 224→186ms)

### Parsing Rules (Verified Safe)

All prompt changes preserve structural elements that backend/frontend parsing depends on:
- `## ` ATX headings for sections — preserved
- Conflicts heading format (`Conflicts` / `Conflicts:`) — preserved
- END_OF_NOTE sentinel — preserved
- Section content between headings — format-agnostic (no constraints)

### API

- **Resend Email Integration** — `RESEND_API_KEY` env var, `noreply@support.dreamcision.com` domain
- **User Preferences Schema v2** — `default_note_type`, `visible_note_types`, `templates`, `templates_other`, `custom_note_types`

---

## [1.0.0] — Initial Release

- Core clinical note generation (consult, SOAP, follow-up, admission, discharge, transfer)
- Whisper ASR (full-upload mode)
- RAG evidence retrieval
- QA chat with web search
- Consult comments (original pipeline)
- User authentication with JWT
- Specialty-aware prompts
- EMR-compatible output sanitization
- Conflicts section detection and separation
- Note type templates (system + user customizable)
