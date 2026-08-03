Patient consent audio (first Record press per encounter)
=========================================================

Place your consent announcement MP3 here as:

  consent_recording.mp3

The workspace loads it from the same origin as the page, e.g.:

  .../media/consent_recording.mp3

Copy from your machine (example WSL path):

  \\wsl.localhost\Ubuntu\tmp\consent_recording.mp3

→ copy into this folder as consent_recording.mp3

Optional overrides (browser console or a small boot script):

  window.CNG_CONSENT_RECORDING_URL = 'https://example.com/consent.mp3';
  window.CNG_CONSENT_RECORDING_DISABLED = true;   // skip playback (testing)
