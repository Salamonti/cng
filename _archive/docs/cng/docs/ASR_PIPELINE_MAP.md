# ASR pipeline map (FastAPI + PCHost)

Clinical path: **one contiguous WebM** from `MediaRecorder` (no timeslice) → on stop, **`POST /api/transcribe_diarized`** with the full file → transcript into `#transcriptionDisplay`. The same recording is uploaded as **`asr_recording`** for Re-transcribe / retention (see `queue.py`, encounter TTL).

## Browser (`PCHost/web/universal_audio_handler.js`)

- `MediaRecorder` without timeslice → single WebM on stop.
- Callback order: `storeLatest` (persist `asr_recording`) then **`fullFileTranscribe`** → `transcribeAudio()` in `workspace_app.js`.

## FastAPI (`server/routes/asr.py`)

- `POST /api/transcribe_diarized`: bearer auth, optional ffmpeg normalization to 16 kHz WAV, pool routing to whisper-server **`/inference`**, long per-attempt timeout for full recordings.

## Stress / tests

- `pytest server/tests/test_asr_proxy.py` — routing, cooldowns, timeouts.
- `pytest server/tests/test_asr_concurrent_stress.py` — concurrent full-file requests (mock upstream).

## Related

- [`WHISPER_LAUNCH_ARGUMENTS.md`](./WHISPER_LAUNCH_ARGUMENTS.md)
