---
description: Troubleshooting common ASR and transcription issues
---

# Troubleshooting ASR Issues

Problems with speech recognition or transcription? Here are common issues and solutions.

---

## Transcription Is Slow

**Possible causes:**
- Large chunk size setting
- Diarization enabled (adds processing time)
- Server under heavy load
- Network latency

**Try:**
- Reduce chunk size in [ASR Settings](../daily-workflow/asr-settings.md)
- Disable diarization temporarily
- Wait a minute and try again
- Check your connection status (should say "Connected")

---

## Transcription Is Inaccurate

**Possible causes:**
- Background noise or poor audio quality
- Speaking too quickly or overlapping speech
- Medical terminology not recognized
- Wrong language model

**Try:**
- Speak clearly at a normal pace
- Use a headset or position the microphone closer
- Minimize background noise
- Edit the transcription directly in the Chart Data panel
- Re-transcribe the recording with different settings

---

## Streaming Not Working

**Possible causes:**
- Streaming disabled in settings
- Browser compatibility issue
- Network interruption

**Try:**
- Check that streaming is enabled in [ASR Settings](../daily-workflow/asr-settings.md)
- Refresh the page and try again
- Try a different browser (Chrome or Edge recommended)
- Check your internet connection

---

## Diarization Not Separating Speakers

**Possible causes:**
- Similar-sounding voices
- Overlapping speech
- Background noise
- Diarization disabled

**Try:**
- Ensure diarization is enabled in [ASR Settings](../daily-workflow/asr-settings.md)
- Make sure speakers don't talk over each other
- Edit speaker labels manually after transcription
- Try re-recording with clearer speaker separation

---

## Recording Won't Start

**Possible causes:**
- Browser doesn't have microphone permission
- Microphone is in use by another app
- Browser compatibility issue

**Try:**
- Check that your browser has microphone permission (look for the camera/mic icon in the address bar)
- Close other apps that might be using the microphone
- Try a different browser
- Restart the browser

---

## Audio File Upload Fails

**Possible causes:**
- File too large
- Unsupported file format
- Network error during upload

**Try:**
- Check that the file format is supported (WAV, MP3, M4A, WebM)
- Try a smaller file
- Check your internet connection
- Try uploading again

---

## Nothing Shows Up After Transcription

**Possible causes:**
- Very short recording (less than 2 seconds)
- No speech detected (silence)
- Server error

**Try:**
- Make sure you actually spoke during the recording
- Check the server connection status
- Try re-recording
- If the problem persists, contact your administrator

---

**Related:** [ASR Settings](../daily-workflow/asr-settings.md) | [Dictating a Note](../daily-workflow/voice-recording.md)
