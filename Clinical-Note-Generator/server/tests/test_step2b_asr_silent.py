"""STEP 2b (error-handling plan): triage of the 7 silent handlers in routes/asr.py.

Reclassified per the plan (code-adjacent helpers + ASR proxy error paths):
- L116 `_read_audio_bounded` finally close  -> cleanup-guard, safe-to-swallow (comment)
- L276 `_asr_fallback_url`                  -> best-effort local-dev URL derivation (comment)
- L342 `_candidate_pool_urls`               -> best-effort local-dev 8096 candidate (comment)
- L472 `_infer_file_suffix`                 -> best-effort suffix inference (comment)
- L602 `_normalize_audio_to_wav` finally    -> cleanup-guard temp-file removal (comment)
- L927 HTTPException handler -> record_asr_incident: best-effort telemetry on an
      already-determined error response; failure no longer silent -> logger.warning
- L1075 `asr_engine_info` /asr_engine probe -> expected-failure probe path (falls
      through to /inference) -> rationale comment + logger.debug

Every monkeypatch uses the auto-restoring `monkeypatch` fixture.
"""
import logging

import pytest


@pytest.fixture
def asr_routes():
    from server.routes import asr
    return asr


def _set_asr_env(monkeypatch, primary="http://asr-primary:8095", fallback=""):
    monkeypatch.setenv("ASR_URL", primary)
    monkeypatch.setenv("ASR_URL_FALLBACK", fallback)
    monkeypatch.delenv("ASR_URLS", raising=False)
    monkeypatch.setenv("ASR_API_KEY", "notegenadmin")


# --- L116: cleanup-guard (upload.close must never propagate) ----------------

@pytest.mark.anyio
async def test_read_audio_bounded_close_failure_does_not_propagate(asr_routes, monkeypatch):
    class _FakeUpload:
        def __init__(self, data):
            self._data = data

        async def read(self, n):
            d, self._data = self._data, b""
            return d

        async def close(self):
            raise RuntimeError("close failed")

    monkeypatch.setattr(asr_routes, "_asr_max_upload_bytes", lambda: 350_000_000)
    data = await asr_routes._read_audio_bounded(_FakeUpload(b"hello"), "trace-1")
    assert data == b"hello"


# --- L276: _asr_fallback_url (best-effort local-dev 8096 derivation) --------

def test_asr_fallback_url_derives_8096_in_local_dev(asr_routes, monkeypatch):
    _set_asr_env(monkeypatch, primary="http://127.0.0.1:8095")
    assert asr_routes._asr_fallback_url() == "http://127.0.0.1:8096"


def test_asr_fallback_url_returns_none_for_remote_primary(asr_routes, monkeypatch):
    _set_asr_env(monkeypatch, primary="http://asr-primary:8095")
    assert asr_routes._asr_fallback_url() is None


# --- L342: _candidate_pool_urls (best-effort 8096 auto-add) -----------------

def test_candidate_pool_urls_includes_8096_for_localhost(asr_routes, monkeypatch):
    _set_asr_env(monkeypatch, primary="http://127.0.0.1:8095")
    urls = asr_routes._candidate_pool_urls()
    assert "http://127.0.0.1:8095" in urls
    assert "http://127.0.0.1:8096" in urls


# --- L472: _infer_file_suffix (best-effort, falls back to .bin) -------------

def test_infer_file_suffix_known_and_fallback(asr_routes):
    assert asr_routes._infer_file_suffix("rec.webm", "audio/webm") == ".webm"
    assert asr_routes._infer_file_suffix("", "application/octet-stream") == ".bin"


# --- L602: _normalize_audio_to_wav (non-strict fall-through, cleanup guard) -

def test_normalize_audio_crash_non_strict_returns_original(asr_routes, monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("ffmpeg not installed")
    monkeypatch.setattr(asr_routes.subprocess, "run", _boom)
    data, fname, ctype = asr_routes._normalize_audio_to_wav(
        b"\x00\x01\x02", "clip.webm", "audio/webm", strict=False
    )
    assert data == b"\x00\x01\x02"
    assert fname == "clip.webm"
    assert ctype == "audio/webm"


# --- L927: incident-record failure is logged, error response still returned -

def test_incident_record_failure_logged_and_response_returned(client, monkeypatch, tmp_path, caplog):
    from server.app import app
    from server.core.dependencies import require_api_bearer
    import server.routes.asr as asr_routes

    app.dependency_overrides[require_api_bearer] = lambda: True
    _set_asr_env(monkeypatch)
    asr_routes._primary_down_until = 0.0
    asr_routes._rr_counter = 0
    asr_routes._url_down_until.clear()

    def _boom(*a, **k):
        raise RuntimeError("incident store write failed")
    monkeypatch.setattr(asr_routes, "record_asr_incident", _boom)

    with caplog.at_level(logging.WARNING, logger="cng.asr"):
        resp = client.post("/api/transcribe_diarized")  # no audio -> HTTPException(400)

    assert resp.status_code == 400
    joined = "\n".join(r.getMessage() for r in caplog.records)
    assert "record_asr_incident failed" in joined


# --- L1075: /asr_engine probe failure falls through to /inference -----------

@pytest.mark.anyio
async def test_asr_engine_info_falls_through_when_asr_engine_probe_fails(asr_routes, monkeypatch, caplog):
    class _FakeResp:
        def __init__(self, status, body):
            self.status = status
            self._b = body

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def json(self):
            import json
            return json.loads(self._b)

        async def text(self):
            return self._b

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def get(self, url):
            if url.endswith("/asr_engine"):
                raise RuntimeError("connect refused")
            if url.endswith("/inference"):
                return _FakeResp(200, '{"engine":"whisper.cpp"}')
            return _FakeResp(404, "no")

    def _fac(*a, **k):
        return _FakeSession()

    monkeypatch.setattr(asr_routes.aiohttp, "ClientSession", _fac)
    monkeypatch.setattr(asr_routes, "_candidate_urls", lambda: ["http://asr-primary:8095"])

    with caplog.at_level(logging.DEBUG, logger="cng.asr"):
        info = await asr_routes.asr_engine_info()

    assert info["engine"] == "whisper.cpp"
    assert info["endpoint"] == "/inference"
    assert any("asr_engine probe failed" in r.getMessage() for r in caplog.records)
