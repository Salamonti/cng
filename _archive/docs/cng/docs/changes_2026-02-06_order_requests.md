Created 2026-02-06

Summary of Changes (Order & Referral Requests helpers)

1) Backend (FastAPI) - Order/Referral helper generation
- Added new order request pipeline in C:\Clinical-Note-Generator\server\routes\notes.py.
- New endpoint: GET /api/generation/{gen_id}/order_requests
- Runs after note generation (both streaming and non-streaming paths).

2) Two-stage helper generation
- Stage 1 detects items from the Plan section only.
- Stage 2 generates request text per item:
  - Imaging/Procedure: uses FULL NOTE, concise 3–4 lines, no “Order…” phrasing, may refine modality (e.g., HRCT chest).
  - Referral: uses default_note_system_prompt_other + default_note_user_prompts_other.referral + FULL NOTE.
  - Lab: one-line request, no explanations.
  - Medication: plain text, one medication per line, no explanations.

3) Formatting and caps
- Imaging requests wrapped to 3–4 lines via post-processing.
- Imaging max tokens increased to 1200.
- Referral max tokens set to 1200.
- Removed character cap on request text (no truncation on referral output).

4) Medication merging
- Multiple medication items are merged into a single item titled “Medications”.
- Output is deduplicated line-by-line.

5) Frontend UI (PCHost)
- Added “Order & Referral Requests” button under generated note.
- Added modal with editable text boxes and copy buttons.
- Modal opens reliably (attached to document.body) and uses addEventListener for click.
- Polls /api/generation/{gen_id}/order_requests after note generation.

Backups created in C:\project-root\Clinical-Note-Generator\docs\backups
- index.html.20260205_224310.bak
- notes.py.20260205_224310.bak
- notes.py.20260206_135614.bak
- notes.py.20260206_143005.bak
- notes.py.20260206_144916.bak
- notes.py.20260206_145459.bak
- notes.py.20260206_145754.bak
- notes.py.20260206_150127.bak
- notes.py.20260206_150528.bak
