# TOOLS.md - Local Notes

Skills define _how_ tools work. This file is for _your_ specifics — the stuff that's unique to your setup.

## What Goes Here

Things like:

- Camera names and locations
- SSH hosts and aliases
- Preferred voices for TTS
- Speaker/room names
- Device nicknames
- Anything environment-specific

## Examples

```markdown
### Cameras

- living-room → Main area, 180° wide angle
- front-door → Entrance, motion-triggered

### SSH

- home-server → 192.168.1.100, user: admin

### TTS

- Preferred voice: "Nova" (warm, slightly British)
- Default speaker: Kitchen HomePod
```

## Why Separate?

Skills are shared. Your setup is yours. Keeping them apart means you can update skills without losing your notes, and share skills without leaking your infrastructure.

## Git

- Primary repo remote: `origin = https://github.com/Salamonti/cng.git`

## CNG Runtime Notes

- `config/config.json` model path may be stale and does not necessarily reflect live llama-server model.
- Live note-generation server is manually started by Islam on local workstation.
- Primary live model for note-generation tests: `Ministral 3 14B Q5_K_M` on port `8081`.
- Preferred short name for this model: `ministral14`.
- `ministral14` can process images (vision-capable) in addition to text.
- Remote Whisper ASR endpoint (via PCHost proxy): `https://ieissa.com:3443/whisperx` (alt: `https://ieissa.com:3443/api/transcribe_diarized`).
- Remote OCR endpoint (via PCHost proxy): `https://ieissa.com:3443/ocr` (alt: `https://ieissa.com:3443/api/ocr`).
- Both endpoints are reachable and require auth (unauthenticated POST returns 401).

---

Add whatever helps you do your job. This is your cheat sheet.
