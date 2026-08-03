# ASR pipeline map

Authoritative short reference: [`Clinical-Note-Generator/docs/ASR_PIPELINE_MAP.md`](../Clinical-Note-Generator/docs/ASR_PIPELINE_MAP.md).

Summary: the UI records **one WebM** per session, transcribes it **once on stop** via **`POST /api/transcribe_diarized`**, and stores a copy as **`asr_recording`** for Re-transcribe.
