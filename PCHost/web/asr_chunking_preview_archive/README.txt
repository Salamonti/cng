Optional live-preview ASR (PCM via AudioWorklet -> ~30s WAV chunks) was removed from
universal_audio_handler.js to keep a single code path: MediaRecorder WebM backup ->
transcribe full file on stop.

To review the old implementation, search the git history for this folder name or for
removed symbols such as _ensurePcmWorklet, flushPcmChunk, transcribeOne, _transcribeViaApi.

This directory is a breadcrumb only; it does not load at runtime.
