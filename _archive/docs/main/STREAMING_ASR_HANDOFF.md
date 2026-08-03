# DreamCision Streaming ASR — Handoff Report (2026-06-16)

## TL;DR — the one blocker
Streaming connects end-to-end (proxy + routes + client all work), but **produces no transcript** because of an **audio-format mismatch**: the browser sends **WebM/Opus** (MediaRecorder), the Nemotron worker expects raw **PCM16 16 kHz mono**, and **nothing transcodes in between**. The user therefore only sees the parallel Whisper (batch) result. Fix = insert a WebM→PCM16 transcode (server-side ffmpeg per session, recommended) OR capture raw PCM client-side (AudioWorklet).

The Nemotron worker itself was separately broken (dropped ~75% of audio) and **is now fixed** (verified with a direct PCM probe).

---

## Architecture / hosts (all on userver, `ssh eissa@192.168.0.108`)
- **PCHost** (Node/Express) `:3000` — public front door behind Cloudflare (`https://notes.ieissa.com`). Serves static `web/` and proxies `/api/*` → FastAPI. Source: `/opt/dreamcision/PCHost`.
- **FastAPI** (uvicorn) `127.0.0.1:7860` — backend app. Source: `/opt/dreamcision/Clinical-Note-Generator`, venv `.venv`, Python 3.12.
- **Nemotron streaming worker** (FastAPI/uvicorn) `127.0.0.1:8765` on **GPU1** — `nvidia/nemotron-3.5-asr-streaming-0.6b`. Source `~/asr-trial/server/server.py`, venv `~/asr-trial/venv`, systemd unit `nemotron-stream-gpu1`. NOT a git repo.
- **Whisper batch pool** `:8095` / `:8096` — the existing/fallback ASR (unchanged).
- **Gemma refine** `:8081` — used only by `full`/`asr_diarize` profiles (not `asr_only`).
- Git root for PCHost + Clinical-Note-Generator is `/opt/dreamcision`.

## Intended streaming data flow
```
Browser
  POST /api/asr/stream/start            -> {session_id}
  WS   /api/asr/stream/{id}/audio?token=  (uplink: audio frames)
  GET  /api/asr/stream/{id}/events       (SSE downlink: transcript events)
    -> PCHost proxy (server.js)
       -> FastAPI server/routes/asr_stream.py
          -> orchestrator.feed_audio(session_id, bytes)
             -> NemotronAdapter  -> WS ws://127.0.0.1:8765/ws  (worker)
          -> SSE events: session.started / asr.partial / window.committed / session.done
    -> client AsrStreamClient renders draft(#asrStreamStatus) + committed(#transcriptionDisplay)
```
Batch path (MediaRecorder → segment upload → Whisper) **always runs in parallel** as crash backup + fallback. This is by design.

---

## Current live state (verified)
- `dreamcision-fastapi` restarted Tue 2026-06-16 **21:05:20 UTC**; running env has **`STREAMING_ASR_ENABLED=1`** (confirmed in `/proc/<pid>/environ`). Flag set via drop-in `/etc/systemd/system/dreamcision-fastapi.service.d/streaming.conf`.
- `dreamcision-pchost` restarted with Step 8 proxy; `/`, `/api/health`, `notes.ieissa.com` all 200.
- `nemotron-stream-gpu1` active on `:8765`; `/health` 200, model loaded GPU1.
- `GET /api/asr/capabilities` → `{"streaming_enabled":true,"profiles":["asr_only","asr_diarize","full"],"diarization_available":false,"max_session_minutes":30,...}` (public, no auth).
- Client gate: `localStorage.cng_asr_capture_mode` (`batch` default | `stream`). **Real clinical users are on batch/Whisper — unaffected.** Only a browser that opts in streams.

## What WORKS (verified this session)
1. **Proxy + routes**: live browser test logged `POST /api/asr/stream/start` 200, `WebSocket .../audio` **[accepted]**, `GET .../events` 200 (FastAPI access log, 21:46–21:47).
2. **PCHost WS/SSE proxy (Step 8)**: SSE mount with 900 s timeout + `upgrade` handler scoped to `/api/asr/stream/`. Mock tests `PCHost/tests/stream_proxy.test.mjs` (3/3) + live smoke pass.
3. **Nemotron worker (after fix)**: direct PCM16 WS probe of `jfk.wav` returns ~full transcript: `"And so my fellow Americans. ask not. What your country can do? Ask what you can."` (was `"ask not what your country."` before fix). Probe: `/tmp/nemo_probe3.py` run with `~/asr-trial/venv/bin/python`.
4. **Capabilities / Step 13 re-transcribe** routes mounted and authed.
5. **Batch path** unchanged; clinical app healthy.

---

## THE BLOCKER — WebM vs PCM (root cause of "goes straight to Whisper")
- `server/routes/asr_stream.py` (~L136-137): `data = await websocket.receive_bytes(); await orchestrator.feed_audio(session_id, data)` — passes raw bytes through.
- `server/streaming_asr/adapters/nemotron.py`: only helper is `pcm16_from_wav_bytes()` (L47) — expects a **WAV** container; there is **no WebM/Opus decode**.
- Worker `~/asr-trial/server/server.py` `websocket_endpoint`: `pcm16 = np.frombuffer(raw, dtype=np.int16)` — interprets incoming bytes as **raw PCM16**.
- Client `web/js/asr_capture_strategy.js` `StreamingCaptureStrategy.onData()`: sends `blob.arrayBuffer()` of each MediaRecorder chunk — i.e. **WebM/Opus**, 5 s timeslice (`universal_audio_handler.js` `mediaRecorder.start(5000)`).

Net: worker receives WebM bytes reinterpreted as PCM16 → garbage → empty transcript → no SSE `window.committed` → client shows nothing → user sees only Whisper's batch result on stop. `/tmp/asr_stream_committed.jsonl` shows only old `"stub transcript"` entries, none from real sessions.

### Recommended fix — A (server-side ffmpeg), preferred
Per session, spawn `ffmpeg -hide_banner -loglevel error -i pipe:0 -f s16le -ar 16000 -ac 1 pipe:1`. Pipe the incoming WebM stream to stdin (MediaRecorder produces a *continuous* WebM: first chunk has the EBML/segment header, later chunks append — feed them in order to one ffmpeg stdin), read PCM16 from stdout, forward PCM to `NemotronAdapter`. Start on first audio frame for a session, kill on `stop`/disconnect. Insert at `orchestrator.feed_audio` or in `asr_stream.py` WS loop. Watch: don't block the event loop (use `asyncio` subprocess + drain stdout concurrently); handle ffmpeg death/restart; the worker wants ~1.28 s chunks (chunk logic below) but will buffer arbitrary PCM.

### Alternative fix — B (client-side PCM)
Replace MediaRecorder-for-uplink with Web Audio: `AudioContext({sampleRate:16000})` + an `AudioWorklet` that posts Float32 frames; downsample/clamp to Int16; send those over the WS. Keep MediaRecorder running for the batch backup. Lower latency, "correct" for streaming ASR, but more client work + a worklet file + Safari quirks.

---

## Worker fix already applied (so it's not lost — `~/asr-trial` is NOT git)
In `~/asr-trial/server/server.py` `websocket_endpoint`, three bugs dropped most audio. Fixed:
1. `conformer_stream_step(... keep_all_outputs=True ...)` — was `False` (dropped content at chunk boundaries; **the main bug**).
2. Chunk size uses `enc.streaming_cfg.chunk_size[1]` (32 mel frames → `32*MEL_HOP(640)=20480` samples/chunk), was `[0]` (25→16000).
3. First chunk uses `drop_extra_pre_encoded=0` (cache starts at zeros), subsequent `=2`: `drop_extra_pre_encoded=(0 if state.step_num == 0 else 2)`.
Backup: `/tmp/nemo_server.py.bak.*`. Worker emits **cumulative** transcripts (each msg = full-so-far), appends a `<en-US>` lang tag (strip it), resets `current_partial` only on `finalize`.

### Worker — remaining known issue
`finalize` does **not** flush the trailing `< raw_per_chunk` of `sample_buffer`, so the last ~0.7 s of speech is dropped. Add a pad-to-chunk + final `conformer_stream_step` on the `finalize` action before emitting `finalized`.

---

## Other known issues / TODO
1. **Cumulative vs append mapping (verify)**: worker sends cumulative partials. Confirm `orchestrator` → SSE (`asr.partial` vs `window.committed`) → client `createTranscriptState` (`asr_stream_client.js`: partial *replaces* draft, committed *appends*) does not duplicate text for multi-window sessions.
2. **`cng_asr_capture_mode` persistence**: Step 11 settings UI is **not wired** (module `web/js/asr_settings.js` exists, unused). Testing relies on manually setting localStorage in the console; it was observed `null` on reloads — wire the toggle (`asr_settings.js` `resolveToggleState`/`setCaptureMode`).
3. **Record hot-path latency**: the Step 9 dispatch added an `await fetch('/api/asr/capabilities')` before `startSpeechRecognition()` for ALL users (cached after first). Make it non-blocking / prefetch on load — it slightly regresses the "snappy record" we just shipped.
4. **Stop path / fallback (Step 12)** module `web/js/asr_fallback.js` exists but is **not wired** into the stop path; SSE handle close on stop relies on `session.done`.
5. **Sync robustness (separate)**: `server/routes/workspace.py` optimistic-concurrency 409 path is only logged as `put_ok` (conflicts invisible in telemetry); client `auth_workspace.js` retries a 409 only once then defers to poll. 409s were observed in this session's logs. Not streaming-related but flagged by the user.

---

## Files changed this session
**FastAPI (`/opt/dreamcision/Clinical-Note-Generator`)** — on disk, fastapi restarted:
- `server/routes/asr_stream.py` (new, Step 7), `server/routes/asr_capabilities.py` (new), `server/routes/asr_retranscribe.py` (new, Step 13), `server/app.py` (flag-gated mounts + Step 13), `server/streaming_asr/*` (orchestrator, adapters/nemotron.py, contracts, refine, align — Steps 1-6).

**PCHost (`/opt/dreamcision/PCHost`)** — live (static = live-on-write; server.js needed the restart already done):
- `server.js` — Step 8: `/api/asr/stream` SSE mount + `asrStreamWsProxy` + `onAsrStreamUpgrade` attached to httpServer/httpsServer `upgrade`.
- `web/js/workspace_app.js` — streaming dispatch inserted right before `await audioHandler.startSpeechRecognition();` (~L1423). Also consent pre-roll disabled (committed `c2b68a5`).
- `web/universal_audio_handler.js` — `_captureStrategy` field, `setCaptureStrategy()`, `ondataavailable` forward to strategy, `onstop` calls `strategy.stop()`.
- `web/js/asr_capture_strategy.js` — `StreamingCaptureStrategy` + added `getSessionId()`.
- `web/js/asr_stream_client.js` — `parseSseStream`, `connectStream`, `createTranscriptState`.
- `web/js/asr_settings.js`, `web/js/asr_fallback.js` — present, **not wired**.
- `web/index.html` — loads the 4 modules + `workspace_app.js?v=20260616b` (script tags L842-846), uah tag `?v=20260616b` (L19).
- `web/service_worker.js` — `CACHE_NAME='dreamcision-pwa-v86'`.

**Worker (`~/asr-trial/server/server.py`)** — the 3 decode fixes above.

## Backups (userver `/tmp`)
`server.js.bak.20260616_203947`, `workspace_app.bak.*`, `uah.bak.*`, `index.html.bak.*`, `sw.bak.*`, `nemo_server.py.bak.*`. Consent fix committed `c2b68a5`; Step 8 server.js + client wiring are **uncommitted working-tree** changes.

## How to disable streaming (instant, safe)
- Soft (per browser): `localStorage.setItem('cng_asr_capture_mode','batch')`.
- Hard (everyone): `rm /etc/systemd/system/dreamcision-fastapi.service.d/streaming.conf && sudo systemctl daemon-reload && sudo systemctl restart dreamcision-fastapi` → capabilities `streaming_enabled:false` → client resolves to batch; or just leave it on (real users already on batch).

## Quick verification commands
```
# worker transcribes PCM (should print near-full jfk quote):
~/asr-trial/venv/bin/python /tmp/nemo_probe3.py
# capabilities:
curl -s https://notes.ieissa.com/api/asr/capabilities
# live stream attempts:
journalctl -u dreamcision-fastapi --since "5 min ago" | grep asr/stream
```
