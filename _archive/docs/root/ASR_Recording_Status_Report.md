# Recording / ASR Pipeline — Full Status Report

> **UPDATE 2026-06-12 — the P0/P1 gaps below are now addressed in code (verify scheduling on host).**
> This report is a **point-in-time snapshot from 2026-05-22**. Since then:
> - **§3 / P0 broken purge:** `tools/purge_audio.py` is rewritten — imports `from server.core.db import engine` + `from sqlmodel import Session` (no `SessionLocal`), no FastAPI-route imports, and **adds an orphan-file sweep** over `data/queue_files` and `data/asr_recording_segments`. It runs.
> - **§3 / P1 retention mismatch:** `server/routes/admin.py` now reports `audio_retention_days: 7` (the "60" is gone).
> - **Scheduling:** `tools/run_purge_audio.bat` exists. **Still open:** confirm a daily Scheduled Task actually invokes it on the live host — that is the one remaining compliance action.
> - Master sequencing/status now lives in [`docs/GRAND_PLAN.md`](docs/GRAND_PLAN.md#status--execution-truth-read-first). Treat the sections below as historical context.

**Date:** 2026-05-22
**Scope:** The record → upload → transcribe → retain pipeline, and the
durability/retention work driven by the Nova Scotia 7-day audio rule.

**Verification note:** Findings below are based on direct reads of the source
files (the real filesystem). Syntax/parser checks could not be run from this
environment — run `node --check` on the JS files and `python -m py_compile`
on the Python files on the Windows host as the final gate.

---

## 1. The incident that started this

On 2026-05-22 a doctor dictating on an iPhone (iOS 18.7, Safari) lost a 5–10
minute recording. The diagnostic log (`asr_diagnostics/incidents.jsonl`) shows
the real sequence:

- **15:19 UTC** — iPhone: `client.recording_start_failed` —
  "The MediaRecorder's state must be inactive in order to start recording".
- **15:23–15:26 UTC** — three rounds of `asr.upstream` HTTP 500
  "FFmpeg conversion failed" across all three Whisper servers (8095/8096/8097).

**Root cause:** the recording-start path was not concurrency-safe. A second tap
during microphone initialization produced a desynchronized state machine — a
zombie/!inactive `MediaRecorder` and an upload whose audio container was
incomplete, which every downstream component (proxy ffmpeg, Whisper) rejected.
The button stuck, the encounter locked, and the audio was unrecoverable because
the only "backup" preserved the same broken capture.

---

## 2. What is fixed and verified

### 2.1 Capture / recording state machine (`universal_audio_handler.js`)

| Fix | Status |
| --- | --- |
| Re-entrancy lock — synchronous check+set before any `await` | Verified |
| Phase guard moved **before** the lock (no lock leak) — Defect 1 | Verified |
| `_recordingGeneration` counter — aborts a start superseded by a stop — Defect 2 | Verified |
| Cancel path is minimal — touches only its own stream (no triple-tap clobber) | Verified |
| Min-size guard in `onstop` — rejects < 1000-byte blobs | Verified |
| 15-minute recording timeout (cleared in stop / onerror / catch / onstop) | Verified |
| Aggressive silence: 10 s warning, 30 s hard auto-stop | Verified |
| `forceResetRecording()` escape hatch + `window.forceResetRecording()` | Verified |
| `forceResetRecording()` bumps the generation counter | Verified |
| EBML-header check now fires `_reportAsrIncident` | Verified |

The recording state machine is now sound. The original stuck-button incident
scenario (tap-to-record, second tap during mic init) was traced end-to-end and
resolves cleanly to `idle`.

### 2.2 Server ASR route (`server/routes/asr.py`, `queue.py`) — live after restart

| Fix | Status |
| --- | --- |
| Early reject of < 1000-byte uploads with a clear 400 message | Verified |
| `_sniff_magic()` detects MP4/M4A (`ftyp`) — iOS Safari recordings pass | Verified |
| Format-only reject removed — ffmpeg still handles unrecognised containers | Verified |
| Structured audit logs (`ASR_AUDIT_LOG=1` in `service_endpoints.json`) | Live after restart |
| `queue.py` `MIN_FILE_SIZE` size gate | Verified |
| `_auto_fail_stale_jobs()` — fails pending jobs > 48 h | Verified |
| Client-side retry cap — 5 failures → `abandoned`, poller stops | Verified |

The FastAPI restart activated these route and config changes; they are now in
effect on the server.

### 2.3 Local-copy durability (the NS rule, client side) — verified

The local copy now follows the rule "delete once the server confirms it has the
audio":

- During recording, MediaRecorder chunks are written to IndexedDB every 5 s.
- On stop, the `storeLatest` callback uploads to `/queue` and **returns
  `persistLayerOk`** (`workspace_app.js`).
- `onstop` captures that as `uploadOk` and deletes the IndexedDB backup **only
  when the server confirmed storage**; on failure it calls `markStopped()` so
  the recording can be retried.
- A 7-day IndexedDB TTL remains as a compliance backstop — if upload never
  succeeds, the local copy still self-purges within 7 days.
- `config/config.json` → `audio_retention_days: 7`.

**Client-side retention is compliant on every path.**

---

## 3. What is NOT working — server-side 7-day retention

This is the compliance-critical gap and it is **still not functional.**

`tools/purge_audio.py` exists and its logic is reasonable (correct
`created_at < now − 7 days` cutoff, deletes file + DB row, writes an audit
trail to `asr_diagnostics/audio_purge_log.jsonl`, supports `--dry-run`). But:

1. **The script cannot run — broken import.** Line 25 is
   `from core.db import SessionLocal`, used at line 50 as `SessionLocal()`.
   `core/db.py` defines `engine`, `init_db`, and `get_session` — **there is no
   `SessionLocal`.** The script raises `ImportError` on every invocation and has
   never purged anything. The `sys.path` hack for `from routes.queue import …`
   is also fragile, since `routes/queue.py` uses absolute `server.` imports.

2. **Nothing schedules it.** No cron job, batch file, PowerShell script, or
   Windows scheduled task references `purge_audio.py`. Even with the import
   fixed, nothing would trigger it.

3. **Restarting FastAPI does not help.** `purge_audio.py` is a standalone CLI
   script, not a FastAPI route. A server restart activates route/config changes
   (Section 2.2) but has no effect on a broken, unscheduled standalone script.

4. **`admin.py` still advertises `audio_retention_days: 60`** (line ~1010),
   contradicting the `7` in `config.json`. A "60 days" value should not exist
   anywhere in a system bound by a 7-day rule.

**Net effect: stored audio on the server is never automatically deleted.**
Current server retention is "for the lifetime of the encounter" (per
`test_asr_recording_retention.py`) — effectively indefinite. **The NS 7-day
rule is not met on the server.**

---

## 4. Smaller items to address

- **`uploadOk` gate is too loose.** `persistLayerOk` is set from
  `queueRequest()`, which returns `{ok:true, mode:'local'}` when it falls back
  to the *local* queue (offline / server down). That makes `uploadOk` true and
  deletes the IndexedDB backup even though the server does **not** have the
  file. The delete should require a genuine server confirmation, not any
  `ok:true`.
- **Verify the transcript survives the purge.** The 7-day rule covers audio,
  not text. A background `transcribe` job whose result was never pulled into a
  note may hold the only copy of that transcript on the `QueuedJob` row the
  purge deletes. Confirm the note/transcript is stored independently.
- **Orphan files.** Files in `queue_files/` with no matching job row would not
  be caught by a job-row-based purge. A periodic orphan sweep closes this.
- **Redundant local mechanisms.** `RecordingRecovery` (IndexedDB),
  `app.requestQueue` + `persistLocalQueueMeta`, and `saveFileLocally`
  (download) overlap. Consolidating to one local-first path makes retention
  logic single-sourced.
- **Chunked / resumable upload** remains the robust long-term answer for large
  mobile recordings, but is a separate project — do not block the above on it.

---

## 5. Compliance status (NS 7-day audio rule)

| Layer | Status | Notes |
| --- | --- | --- |
| Phone / browser (IndexedDB) | Compliant | Deleted on confirmed upload; 7-day TTL backstop |
| Server (stored audio) | **Not compliant** | No working purge; retention currently indefinite |
| Transcript / note | OK (verify) | Text is not audio; confirm it survives the purge |

The end-to-end goal — audio gone everywhere within 7 days on every path — is met
on the client but **not on the server.**

---

## 6. Priority action list

**P0 — make the server purge actually work (compliance-blocking)**

1. Fix `purge_audio.py`: replace `from core.db import SessionLocal` /
   `SessionLocal()` with `from core.db import engine` + `from sqlmodel import
   Session` + `with Session(engine) as session:`. Correct the import paths so
   `routes.queue` / `models.queued_job` resolve. Test with
   `python purge_audio.py --dry-run`.
2. Schedule it daily (alongside the canary and incident-rotation jobs).

**P1 — correctness / consistency**

3. Reconcile `admin.py` `audio_retention_days` from 60 to 7.
4. Tighten the `uploadOk` gate to require genuine server confirmation.

**P2 — hardening**

5. Verify the transcript/note survives the audio purge.
6. Add an orphan-file sweep for `queue_files/`.

**Later**

7. Chunked / resumable upload for large mobile recordings.

---

## 7. What the FastAPI restart did and did not do

- **Did:** activated the `asr.py` early-reject + MP4 sniffing, the `queue.py`
  size gate and stale-job auto-fail, and `ASR_AUDIT_LOG=1` structured logging.
  These are now live.
- **Did not:** change anything about `purge_audio.py`. It is a standalone
  script, it still cannot import, and it is still unscheduled — so server-side
  audio retention is still not running.

---

## 8. Bottom line

The recording pipeline itself — the part that failed the doctor on 2026-05-22 —
is genuinely fixed and verified: the state machine is sound, garbage uploads are
rejected on both ends, the doctor can no longer get trapped, and the local copy
follows the retention rule correctly.

The one remaining gap is **server-side 7-day audio deletion**, which is written
but non-functional (broken import, not scheduled). Until P0 items 1 and 2 are
done, the server keeps audio indefinitely and the NS rule is not satisfied on
the server. This needs ~1 small code fix plus a scheduled job — modest work, but
it is the compliance-blocking item and should be next.
