---
description: Speaker diarization — separating clinician and patient voices in recordings
---

# ASR Diarization

Diarization automatically identifies **who is speaking** during a recording, separating the clinician's voice from the patient's voice (or any other speakers present).

---

## How It Works

When diarization is enabled:

1. The system analyzes the audio to detect voice changes.
2. Each speaker is assigned a label (e.g., "Speaker 1", "Speaker 2").
3. The transcription shows speaker labels before each segment of speech.

**Example output:**

> **Speaker 1:** Good morning, how are you feeling today?
>
> **Speaker 2:** I've been having some chest pain for the past few days.
>
> **Speaker 1:** Let me examine that. Can you point to where it hurts?

---

## Enabling Diarization

1. Open **ASR Settings** (gear icon in the recording toolbar).
2. Toggle **Diarization** to "On".
3. Settings are saved automatically and apply to your next recording.

---

## When to Use It

**Use diarization when:**
- You want a clear record of patient statements vs. clinician observations
- You're documenting a consultation with multiple speakers
- You need to verify what the patient said verbatim

**Skip diarization when:**
- You're dictating solo (no patient in the room)
- You're summarizing from memory after the encounter
- You need faster processing and don't need speaker separation

---

## Limitations

- **Similar voices:** If two speakers sound very similar, the system may misattribute speech.
- **Background noise:** Noisy environments can confuse the speaker detection.
- **Overlapping speech:** When people talk at the same time, attribution may be incorrect.
- **Processing time:** Diarization adds extra processing time to the transcription.

---

## Tips for Better Results

- Position the microphone to capture the primary speaker clearly.
- Ask patients to speak one at a time when possible.
- Review the speaker labels after transcription — you can manually correct them in the transcript editor.
- Use a headset or lapel mic for the best speaker separation.

---

**Related:** [ASR Settings](asr-settings.md) | [Dictating a Note](voice-recording.md)
