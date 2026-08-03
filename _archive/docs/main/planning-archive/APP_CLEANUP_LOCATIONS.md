# Cleanup Targets & Source Locations

## 1. Remove em-dash line separators + parse double asterisks in generated notes
- `cleanMarkdownFences()` strips ```` ``` ```` fences and blindly removes every `---` sequence (`PCHost/web/index.html:2214-2236`). Adjusting this function affects all text sanitization; only the generated note should lose the em-dash separators.
- Streaming sanitization lives in `cleanNoteChunk()` and `finalizeNoteText()` (`index.html:2295-2348`). This is the safest place to normalize em-dash line breaks within the generated note textarea without touching other fields.
- Clipboard exports already strip `**` via `copyNote()` (`index.html:4344-4359`), but that happens only when the user taps Copy. Apply the same markdown-bold removal (and any em-dash cleanup) in `finalizeNoteText()` so the textarea and downstream workspace state stay consistent.

## 2. Increase padding on the RAG “Consult Comment” pane
- The consult evidence output is rendered inside `<div id="consultComment" class="form-control consult-comment">` (`index.html:1985-2002`). This element inherits `.form-control { padding: 12px; }` and only overrides `white-space` via `.consult-comment` (`index.html:748-770`).
- Add a dedicated rule (e.g., `.consult-comment`) or card-specific wrapper spacing at `index.html:748-770` to bump padding/margins without affecting other form controls.

## 3. Increase QA token allowance (~30%) & document prompt rigidity
- The QA chat config caps completions at `qa_chat_max_tokens: 700` (`Clinical-Note-Generator/config/config.json:70-94`). The route reads that value directly when calling `collect_completion()` (`server/routes/qa_chat.py:191-197`), so raising the limit to ~900 requires touching both the config default and any env/override validation.
- Prompt rigidity comes from `_build_prompt()` hard-coding sections and fallback instructions (`server/routes/qa_chat.py:144-166`). The instructions force “Direct Answer, Differential, Workup, Management, Safety” headings even when users ask for other formats, which is the behavior to revisit while bumping the token budget.

## 4. Fix/remove stuck “ASR: Uploading…” text
- When speech recognition stops, the audio handler calls `_asrStatus('processing', 'Uploading recorded audio for transcription')` (`PCHost/web/universal_audio_handler.js:205-220`), but there is no matching reset once the upload finishes.
- The status label lives at `#asrStreamStatus` inside the Current Encounter card (`index.html:1850-1862`) and is updated via `setAsrStatusCallback` (`index.html:2449-2468`). Clearing/hiding that element after the upload completes (success or failure) will remove the lingering “Uploading…” text.

## 5. Raise truncation budgets to 12 000 tokens per section
- Default budgets are defined twice: `config/config.json` (`prior_visits_budget_tokens`, `labs_imaging_other_budget_tokens`, `current_encounter_budget_tokens` at lines 80-93) and `server/core/preprocessing/constants.py:1-17`. Update both to 12000 to keep configuration + code in sync.
- Enforcement happens inside `TokenBudgetTruncator` (`server/core/preprocessing/truncation.py:14-63`), so tests referencing the old values (e.g., `server/tests/test_preprocessing.py:20-32`) and docs (`docs/ENV_VARIABLES.md:140-150`) should be updated once the constants change.
