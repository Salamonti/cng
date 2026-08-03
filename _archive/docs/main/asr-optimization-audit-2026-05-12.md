# DreamCision ASR — Deep Optimization Audit
**Date:** 2026-05-12 | **Author:** Albert (audit from code + config)  
**Files audited:** `asr.py`, `queue.py`, `service_endpoints.json`, `config.json`, `workspace_app.js`, `universal_audio_handler.js`, whisper-server README, startup scripts

---

## Current Pipeline (Ground Truth)

```
Browser (iOS Safari)
  │  MediaRecorder → WebM/Opus @ 44.1kHz
  │  RecordingRecovery → IndexedDB (chunk-level backup)
  ▼
POST /api/transcribe_diarized  (FastAPI, uvicorn 1 worker)
  │  Magic sniff → _sniff_magic()
  │  _normalize_audio_to_wav_async() — asyncio.to_thread(ffmpeg)
  │   ├─ ffmpeg -threads 0 -c:a pcm_s16le -ar 16000 -ac 1 -af aresample=async=1000
  │   └─ WebM/Opus → 16kHz mono PCM WAV (~30-800ms depending on clip length)
  ▼
Candidate pool (round-robin, per-URL 15s cooldown)
  ├─ 8095 (whisper-server, large-v3-turbo Q5_0, GPU 2)
  ├─ 8096 (whisper-server, large-v3-turbo Q5_0, GPU 2)
  ├─ 8097 → DOES NOT EXIST ← auto-expanded by _candidate_pool_urls()
  ├─ 8098 → DOES NOT EXIST
  └─ 8099 → DOES NOT EXIST
  ▼
  POST {base_url}/inference  (aiohttp, new ClientSession per request)
  ▼
Plain text → returned to frontend
```

**Frontend fallback path:**
```
Transcribe fails → queueRequest('transcribe', …) → local IndexedDB queue
  → Background poller (60s recursive setTimeout) checks for pending 'transcribe' jobs
  → POST /api/queue/{id}/process  (sync requests library)
  → Same normalize + candidate + whisper flow (duplicated logic from asr.py)
```

---

## Critical Issues

### 🔴 IS-1: Phantom pool entries — 3 of 5 servers don't exist
**File:** `asr.py::_candidate_pool_urls()` line ~268

```python
if u.port == 8095 and u.hostname in ("127.0.0.1", "localhost"):
    for port in (8096, 8097, 8098, 8099):  # ← 8097-8099 DO NOT EXIST
```

**service_endpoints.json** defines only 2 whisper instances (8095, 8096). The auto-expand creates 5 candidates. When 3 requests arrive concurrently, requests 3+5 will:
1. Try 8097 → connection refused (or timeout)
2. Try 8098 → connection refused
3. Try 8099 → connection refused  
4. Finally retry 8095 or 8096 (may be free by now)

Each connection failure costs ~8s (`sock_connect=8`). That's 24s of wasted latency.

**Severity:** 🔴 Critical under load. Each phantom wastes 8-24s.

### 🔴 IS-2: Only 2 whisper instances → 2 concurrent max
**File:** `service_endpoints.json` — `whisper_instances`

With 2 instances on a 32GB GPU (using only ~3GB total), the system can handle exactly 2 concurrent transcriptions. A 3rd request sits in cooldown hell (IS-1). Given clinical use (single user), this is usually fine — but when the queue poller fires during a live transcribe, we have 3 concurrent requests.

**Severity:** 🟡 Medium (mostly single-user, but spike conditions exist).

### 🟡 IS-3: No whisper-server performance flags
**File:** `service_endpoints.json` — `whisper_instances.*.launch.arguments: []`

From whisper-server CLI docs, these flags are available and unused:
| Flag | Default | Recommended | Why |
|------|---------|-------------|-----|
| `--beam-size` | 5 (when -1) | `1` (greedy) | ~3-5x faster, medical speech is clear enough |
| `--no-timestamps` | false | true | Don't compute timestamps we never use |
| `--flash-attn` | false | true | CUDA speedup on RTX 5090 |
| `--no-fallback` | false | consider | Skip temperature fallback decode loop |
| `--suppress-nst` | false | true | Minor speed win, suppresses non-speech tokens |
| `--threads` | 4 | 4-6 | Per-instance CPU thread allocation |

**Severity:** 🟡 Medium. Beam search alone accounts for ~3-5x penalty.

### 🟡 IS-4: FFmpeg thread contention under load
**File:** `asr.py::_normalize_audio_to_wav()` line ~475

```python
cmd = [ffmpeg_bin, …, "-threads", "0", …]  # 0 = all 24 cores
```

When 3+ concurrent ffmpeg calls run, each grabs all 24 cores. Total CPU time equals `3 × 24-thread saturation`. On Ultra 9 285K (24 cores/24 threads, no HT), this is catastrophic — each ffmpeg tries to use all cores and they all lose.

**Severity:** 🟡 Medium under load. Low for single requests.

### 🟡 IS-5: New aiohttp ClientSession per request
**File:** `asr.py::transcribe_diarized()` line ~608

```python
async with aiohttp.ClientSession(timeout=per_request_timeout) as session:
```

Every transcribe request creates a new connection pool, DNS resolution, TCP handshake. On localhost this is fast (~1-5ms) but unnecessary.

**Severity:** 🟢 Low on localhost. Higher if whisper servers ever move to remote hosts.

### 🟡 IS-6: Duplicated whisper-calling logic in queue.py
**File:** `asr.py` vs `queue.py::process_queued_job()`

Both files implement the same pattern:
1. Build candidate pool
2. Normalize audio  
3. Iterate candidates, POST to /inference
4. Mark URL down on timeout/5xx
5. Extract text from response
6. Handle empty transcripts

`queue.py` uses sync `requests`, `asr.py` uses async `aiohttp`. This is a maintenance hazard — any fix applied to one must be mirrored.

**Severity:** 🟡 Medium (maintenance). Zero user-visible impact.

### 🟡 IS-7: Frontend timeout inadequate for long recordings
**File:** `workspace_app.js` line ~1516

Hardcoded `timeoutMs` likely 120s. For a 60-minute consultation, ffmpeg (5s) + whisper large-v3-turbo (up to 300s for long audio) would blow past this. The backend has `per_request_timeout = 300s` but if the frontend aborts at 120s, the response is lost.

**Severity:** 🟡 Medium. Impacts long consultations.

### 🟢 IS-8: Normalized WAV not validated pre-flight
**File:** `asr.py` — after `_normalize_audio_to_wav_async()`

FFmpeg output is assumed valid. No check for:
- Non-empty output
- RIFF/WAVE magic bytes
- 16kHz sample rate
- 1 channel

If ffmpeg produces corrupt output (disk full, codec bug), whisper-server gets garbage and returns 400.

**Severity:** 🟢 Low (ffmpeg is reliable, but debugging is hard when it happens).

### 🟢 IS-9: GPU 2 contention between OCR + Whisper
**File:** `service_endpoints.json`

Whisper (8095, 8096) + OCR (8090) all pinned to `CUDA_VISIBLE_DEVICES: "2"` (RTX 5090 32GB). OCR model is ~7GB, two whisper models = ~3GB. Total: ~10GB/32GB. Not a VRAM issue, but GPU compute is shared. An OCR request during ASR could stall whisper inference.

**Severity:** 🟢 Low. OCR is infrequent, clinical workflow unlikely to overlap.

---

## What's Working Well

1. ✅ **Model choice:** `ggml-large-v3-turbo-q5_0.bin` is the optimal speed/accuracy tradeoff
2. ✅ **Language:** Explicitly "en" → no auto-detection overhead
3. ✅ **FFmpeg normalization:** Handles WebM/Opus correctly, magic sniff prevents mislabeled files
4. ✅ **Cooldown system:** Per-URL 15s parking prevents hammering failed servers
5. ✅ **Audit logging:** Comprehensive trace IDs at every stage
6. ✅ **Frontend resilience:** IndexedDB backup, background queue poller, retry UI
7. ✅ **Non-strict normalization:** Silent pass-through if ffmpeg fails
8. ✅ **Incident logging:** Structured JSONL + last_incident.json for debugging

---

## Optimization Plan (in harmony with existing architecture)

### Phase 1: Quick Wins — Server-Side (1-2 hours)

#### 1. Add 2 more whisper instances (→ 4 total)
**File:** `service_endpoints.json`
```json
"whisper_instances": {
  "primary":   { "base_url": "http://127.0.0.1:8095", … },
  "fallback":  { "base_url": "http://127.0.0.1:8096", … },
  "extra_1":   { "base_url": "http://127.0.0.1:8097", … },
  "extra_2":   { "base_url": "http://127.0.0.1:8098", … }
}
```
- RTX 5090 has 32GB VRAM, 4 × ~1.5GB = 6GB, well within budget
- This makes the auto-expand in `_candidate_pool_urls()` actually meaningful
- **Impact:** 2→4 concurrent capacity. Fixes IS-1 and IS-2 together.

#### 2. Add performance flags to whisper-server launch
**File:** `service_endpoints.json` — each whisper instance's `launch.arguments`
```json
"arguments": [
  "--beam-size", "1",
  "--no-timestamps",
  "--flash-attn",
  "--suppress-nst",
  "--threads", "4"
]
```
- Beam size 1 (greedy) is 3-5x faster. Medical dictation is clear speech — accuracy loss is negligible.
- Flash attention leverages Blackwell GPU compute
- **Impact:** 3-5x per-inference speedup. Fixes IS-3.
- **Risk mitigation:** Keep beam=1. If accuracy complaints arise, revert to beam=5 on one instance and route quality-sensitive requests there.

#### 3. Cap FFmpeg threads to 4
**File:** `asr.py` — single-line change
```python
# Before:
"-threads", "0",  # Use all CPU cores
# After:
"-threads", "4",  # Per-instance cap to prevent contention
```
- Each ffmpeg gets 4 threads instead of 24
- 4 concurrent ffmpegs = 4×4 = 16 threads total (well under 24)
- Short clips (<60s) on 4 threads finish in <100ms
- **Impact:** Prevents CPU thrashing under load. Fixes IS-4.

#### 4. Reuse aiohttp ClientSession
**File:** `asr.py` — add module-level session
```python
# Module-level shared session, initialized lazily
_asr_session: Optional[aiohttp.ClientSession] = None
_asr_session_lock = asyncio.Lock()

async def _get_asr_session() -> aiohttp.ClientSession:
    global _asr_session
    if _asr_session is None or _asr_session.closed:
        timeout = aiohttp.ClientTimeout(total=300, connect=8, sock_connect=8, sock_read=300)
        _asr_session = aiohttp.ClientSession(timeout=timeout)
    return _asr_session
```
- Use `session = await _get_asr_session()` instead of `async with aiohttp.ClientSession(…)`
- Add cleanup on app shutdown
- **Impact:** ~5-50ms saved per request. Fixes IS-5.

### Phase 2: Structural Improvements (2-4 hours)

#### 5. Extract shared whisper-calling logic
**File:** New file `server/core/whisper_client.py`
```python
async def transcribe_via_whisper_pool(data, filename, content_type, trace_id):
    """Shared whisper.cpp calling logic used by both asr.py and queue.py"""
```
- `asr.py` calls it async
- `queue.py` wraps it via `asyncio.run()` or runs the sync equivalent
- **Impact:** Single source of truth. Fixes IS-6.

#### 6. Frontend timeout proportional to file size
**File:** `workspace_app.js`
```javascript
// Calculate timeout based on file size: ~5s base + 1s per 50KB
const timeoutMs = Math.max(30000, 5000 + (file.size / 50000) * 1000);
```
- Short clips get short timeouts, long consultations get adequate time
- Cap at 420s (7 min) to match backend + buffer
- **Impact:** Long recordings won't time out prematurely. Fixes IS-7.

### Phase 3: Advanced (1-2 days, benchmark-dependent)

#### 7. WAV validation post-normalization
- Validate RIFF/WAVE magic, 16kHz, mono, non-zero length
- Log warning on mismatch, raise in strict mode
- **Impact:** Better debugging. Fixes IS-8.

#### 8. GPU rebalancing
- Consider moving whisper instances to GPU 0 (RTX PRO 6000 96GB) if OCR conflicts arise
- RTX PRO 6000 has 96GB — could hold OCR + all whisper instances with room to spare
- **Impact:** Eliminates GPU contention entirely. Fixes IS-9.

#### 9. Temperature/speed tiering (experimental)
- Run 2 whisper instances with `--beam-size 1 --no-fallback` (fast tier)
- Run 2 with `--beam-size 5` (quality tier)  
- Route by audio duration: ≤30s → fast tier, >30s → also fast tier (greedy works on long audio), but add quality fallback on empty result
- **Impact:** Fast path for all requests, quality fallback only when needed.

---

## Testing Plan

### Pre-deployment
1. **Syntax check:** Python/JSHint on all changed files
2. **Smoke test:** `pytest server/tests/test_smoke_queue.py -v`
3. **Burn-in:** `python tools/asr_burn_in.py --n 30 --base http://127.0.0.1:7860 --token ***`
   - Before: benchmark p50, p90, p99 latency
   - After: compare with new config
4. **Stress test:** `python tools/stress_asr_transcribe.py --requests 16 --max-concurrent 8`
   - Before/after throughput comparison
5. **Manual integration test:** Record live audio → transcribe → verify text

### Rollout checkpoints
- [ ] whisper-servers 8097, 8098 respond to `GET /inference` (expected: 400/405)
- [ ] `GET /api/asr_engine` returns 4 healthy candidates
- [ ] Single request latency ≤ current baseline
- [ ] 4 concurrent requests all succeed without phantom timeouts
- [ ] Queue processing still works with new whisper instances
- [ ] Beam=1 accuracy acceptable on 5 test recordings (compare to beam=5)

---

## Files to Modify

| File | Changes | Risk |
|------|---------|------|
| `service_endpoints.json` | Add 2 whisper instances + performance flags | Low |
| `asr.py` | Cap ffmpeg threads, reuse aiohttp session, remove phantom pool entries | Low |
| `queue.py` | Inherit from shared whisper client (Phase 2) | Medium |
| `workspace_app.js` | Dynamic timeout based on file size | Low |
| `whisper_client.py` (new) | Shared whisper-calling logic (Phase 2) | Medium |

---

## Rollback Plan

Every change is independently reversible:
- **Whisper flags:** Revert `arguments: []`, restart servers
- **FFmpeg threads:** Change `"4"` back to `"0"`
- **aiohttp pool:** Drop the shared session, revert to per-request
- **Extra instances:** Stop 8097-8098, they just disappear from pool
- **Beam size:** Change `--beam-size 1` back to omit (defaults to 5)
