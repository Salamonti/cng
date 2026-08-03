# Whisper / `whisper-server`: `launch.arguments` examples

For **`whisper_instances.*.launch.arguments`** in [`service_endpoints.json`](../../service_endpoints.json): a **JSON array of strings** passed after the binary (same as a shell would pass). If non-empty, it **replaces** the auto-built argv from `model`, `language`, etc. (see `ai_process_launcher`).

**Rule:** One string per argv token: `["--foo", "bar"]` not `["--foo bar"]`.

---

## 1. Long initial prompt (clinical jargon / language hint)

_whisper.cpp-style flags vary by build; verify with `whisper-server --help`._

The auto-generated launcher in `server/core/ai_process_launcher.py` includes
`--convert` for `kind: "whisper"` instances. Keep that flag when replacing
`launch.arguments` manually. The FastAPI ASR route (`server/routes/asr.py`)
normalizes uploads to **16 kHz mono WAV** via ffmpeg before forwarding to
whisper-server (unless normalization is explicitly disabled with
`ASR_NORMALIZE_TO_WAV=0`). The browser sends **WebM** for the full recording on transcribe.

```json
"--prompt",
"A medical dictation: medications, diagnoses, abbreviations. Prefer Canadian spelling."
```

Full `arguments` example:

```json
[
  "--model", "C:\\\\models\\\\whisper\\\\ggml-large-v3.bin",
  "--language", "en",
  "--prompt",
  "Clinical dictation; drug names; vitals; short sentences."
]
```

---

## 2. VAD / no-speech (example tokens)

Your binary may use **`--vad`**, **`--vad-model`**, **`--no-speech-thold`**, or different names. **Do not** paste this blindly — match your `whisper-server` / `whisper.cpp` version.

```json
[
  "--model", "C:\\\\Clinical-Note-Generator\\\\models\\\\whisper\\\\ggml-base.en.bin",
  "--language", "en",
  "--vad",
  "--vad-model", "C:\\\\models\\\\whisper\\\\ggml-silero-v5.1.4.bin",
  "--no-speech-thold", "0.55"
]
```

---

## 3. Thread / worker hints (if supported)

```json
["--threads", "8", "-t", "8"]
```

---

## 4. Repo helper script

From **repository root**:

```text
python tools/argv_to_json.py -- --model C:\models\ggml.bin --language en --vad
```

Stdout is a single JSON array ready to paste into `launch.arguments`.

## 5. “Export from bash” → JSON array

**Bash**

```bash
ARGS=(--model /path/to/model.bin --language en --vad)
python -c "import json,sys; print(json.dumps(sys.argv[1:]))" "${ARGS[@]}"
```

**PowerShell** (after building `$tokens` as separate strings):

```powershell
$tokens = @('--model','C:\models\ggml.bin','--language','en')
$tokens | ConvertTo-Json -Compress
```

Paste the JSON array into `launch.arguments` in `service_endpoints.json` (or merge via admin **JSON ← form** flow).

---

## 6. Safety

- Paths must be valid on the **machine that starts** `whisper-server` (usually the FastAPI host).
- After editing, **Save** `service_endpoints.json` and **restart** the Whisper process (NSSM, admin **Start**, or office supervisor).
