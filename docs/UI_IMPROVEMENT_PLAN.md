# DreamCision UI — Final Improvement Plan (2026-08-30)

Derived from: competitor research (Abridge, Nabla, Heidi, Suki — verified 2026-08-29/30),
HCI/trust research (NEJM AI human-AI team design), editor library survey (npm-verified),
and a full repo review of Clinical-Note-Generator + PCHost.

Hard constraints honored:
- ASR pipeline structure is LOCKED (25s stride / 5s overlap / synchronous refine). No fake streaming captions.
- Vanilla JS, no framework rewrite. New UI in new module files, incremental textarea-by-textarea migration.
- Clinical data / PHIAs: nothing new leaves the server.

## Stage 1 — Layout shell + honest status (pure frontend, ~1 day each step)
1. Right companion panel (desktop >=1280px): modals (patient materials, settings, prior-visits
   drawer) become a docked right panel ~400px, slide-in over editor gutter, collapses to 44px icon
   strip. Pure CSS flex; new file workspace_layout.js.
2. Phone (<768px): single pane + bottom-tab nav — Record | Note | Materials. Sticky bottom
   recording bar (timer, level pulse, pause/stop). 44px targets, no hover deps. Respect existing
   mobile_a11y tests, manifest, service_worker.
3. Status chip next to Record: mode-aware honest label —
   "AI: local server (on-prem)" when LAN whisper reachable; degrade to "offline — queued" badge
   when not. No marketing claims.

## Stage 2 — Linked Evidence (biggest trust payoff; data model already exists)
Every AsrRecordingSegment already stores window_start_sec, transcript_json (word-level
timestamps), committed/refined text, and server audio (7-day retention).
1. Note generator emits per-section provenance (section -> time ranges) in its output JSON.
2. Click a note section -> transcript view scrolls + highlights source turns
   (asr_transcript_view.js already renders Doctor:/Patient: turns).
3. Within 7-day window: play original audio for the source chunk (audio already on server).
4. Sections lacking provenance render with a subtle "no source link" state (honest, per
   automation-bias research).

## Stage 3 — Editing model (TipTap 3.x, textarea-by-textarea)
- TipTap 3.30.x: headless, ~90KB gz total, init from existing textarea content, onUpdate syncs
  back to current save flows. Replace #generatedNote first; transcript textarea stays a textarea
  (read-only view div already handles diarized rendering).
- BubbleMenu on selection: Regenerate / Shorter / Expand / Ask.
- AI-inserted text styled until accepted; per-section edited flags persisted to encounter state.
- Later: prosemirror-changeset marks for tracked AI-vs-human edits; evidence pins as decorations
  (Stage 2 hooks in here).

## Stage 4 — Cross-device (small fixes; sync is 80% already built)
Already synced per-user on userver Postgres: workspace + encounter state (optimistic versioning,
409 guard), transcripts (asr_recording_segments), notes, settings. Gaps:
1. Mirror the offline upload queue server-side (extend queued_job pattern) so any logged-in
   device can flush/see pending uploads (currently IndexedDB/localStorage = device-local).
2. "Stop on A, resume session on B" already works (multi-session per encounter is the model) —
   surface it in UI ("Continue on this device?" after login elsewhere).
3. NO mid-recording handoff (locked pipeline; genuinely hard).

## Deferred / explicitly not doing
- Fake live captions or SSE streaming ASR (locked pipeline, reverted once before).
- React rewrite (BlockNote/Plate ruled out).
- Marketing/deployment-mode positioning work (parked by owner 2026-08-30).
- Voice-editing ("Suki" style) — needs Stage 3 editor first; revisit later.
- Command bar / "Ask this chart" — RAG exists (:8007); natural follow-on after Stage 3.

## Suggested order: 1.3 -> 1.1 -> 1.2 -> 2 -> 3 -> 4.1 -> 4.2
(Chip first: instant trust win, zero risk. Then desktop panel, then phone shell.)
