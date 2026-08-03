import io
import json
import wave


def _set_asr_env(monkeypatch, primary="http://asr-primary:8095", fallback=""):
    monkeypatch.setenv("ASR_URL", primary)
    monkeypatch.setenv("ASR_URL_FALLBACK", fallback)
    monkeypatch.delenv("ASR_URLS", raising=False)
    monkeypatch.setenv("ASR_API_KEY", "notegenadmin")
    monkeypatch.setenv("ASR_NORMALIZE_TO_WAV", "0")


def _reset_asr_state(asr_routes):
    """Clear all routing/cooldown caches so tests don't bleed into each other."""
    asr_routes._primary_down_until = 0.0
    asr_routes._rr_counter = 0
    asr_routes._url_down_until.clear()


def _set_metrics_log_path(monkeypatch, tmp_path):
    import server.app as app_mod

    monkeypatch.setattr(app_mod._metrics, "http_csv", str(tmp_path / "http_requests_test.csv"))


def _fake_webm_bytes(size=1600):
    return bytes([0x1A, 0x45, 0xDF, 0xA3]) + (b"\x00" * max(0, size - 4))


def _fake_mp4_bytes(size=1600):
    head = b"\x00\x00\x00\x18ftypM4A \x00\x00\x00\x00M4A mp42"
    return head + (b"\x00" * max(0, size - len(head)))


def _wav_bytes(frames=800):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(b"\x00\x00" * frames)
    return buf.getvalue()


class _FakeResponse:
    def __init__(self, status, body):
        self.status = status
        self._body = body

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def text(self):
        return self._body

    async def json(self):
        return json.loads(self._body)


class _FakeClientSession:
    def __init__(self, response_plan):
        # response_plan: {url: [(status, body), ...]}
        self._response_plan = {k: list(v) for k, v in response_plan.items()}

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def post(self, url, data=None, headers=None):
        plan = self._response_plan.get(url, [])
        if not plan:
            raise RuntimeError(f"No fake response configured for {url}")
        status, body = plan.pop(0)
        return _FakeResponse(status=status, body=body)


class _FakeClientSessionFactory:
    def __init__(self, response_plan):
        self.response_plan = response_plan

    def __call__(self, *args, **kwargs):
        return _FakeClientSession(self.response_plan)


def test_asr_upstream_json_error_with_200_returns_503(client, monkeypatch, tmp_path):
    from server.app import app
    from server.core.dependencies import require_api_bearer
    import server.routes.asr as asr_routes

    app.dependency_overrides[require_api_bearer] = lambda: True
    _set_metrics_log_path(monkeypatch, tmp_path)
    _set_asr_env(monkeypatch)
    monkeypatch.setattr(asr_routes, "_primary_down_until", 0.0)
    monkeypatch.setattr(asr_routes, "_rr_counter", 0)

    session_factory = _FakeClientSessionFactory(
        {
            "http://asr-primary:8095/inference": [
                (200, json.dumps({"error": "failed to read audio data"})),
            ]
        }
    )
    monkeypatch.setattr(asr_routes.aiohttp, "ClientSession", session_factory)

    resp = client.post(
        "/api/transcribe_diarized",
        files={"audio": ("clip.webm", _fake_webm_bytes(), "audio/webm")},
    )

    assert resp.status_code == 503
    body = resp.json()
    assert body["error"] == "service_unavailable"
    detail = body["detail"]
    assert detail["service"] == "asr"
    assert any("upstream_error" in e for e in detail["errors"])


def test_asr_empty_payload_with_200_returns_503(client, monkeypatch, tmp_path):
    from server.app import app
    from server.core.dependencies import require_api_bearer
    import server.routes.asr as asr_routes

    app.dependency_overrides[require_api_bearer] = lambda: True
    _set_metrics_log_path(monkeypatch, tmp_path)
    _set_asr_env(monkeypatch)
    monkeypatch.setattr(asr_routes, "_primary_down_until", 0.0)
    monkeypatch.setattr(asr_routes, "_rr_counter", 0)

    session_factory = _FakeClientSessionFactory(
        {
            "http://asr-primary:8095/inference": [
                (200, json.dumps({"text": "   "})),
            ]
        }
    )
    monkeypatch.setattr(asr_routes.aiohttp, "ClientSession", session_factory)

    resp = client.post(
        "/api/transcribe_diarized",
        files={"audio": ("clip.webm", _fake_webm_bytes(), "audio/webm")},
    )

    assert resp.status_code == 503
    body = resp.json()
    assert body["error"] == "service_unavailable"
    assert any("empty transcription" in e for e in body["detail"]["errors"])


def test_asr_fallback_used_when_primary_returns_200_error_payload(client, monkeypatch, tmp_path):
    from server.app import app
    from server.core.dependencies import require_api_bearer
    import server.routes.asr as asr_routes

    app.dependency_overrides[require_api_bearer] = lambda: True
    _set_metrics_log_path(monkeypatch, tmp_path)
    _set_asr_env(monkeypatch, primary="http://asr-primary:8095", fallback="http://asr-fallback:8096")
    monkeypatch.setattr(asr_routes, "_primary_down_until", 0.0)
    monkeypatch.setattr(asr_routes, "_rr_counter", 0)

    session_factory = _FakeClientSessionFactory(
        {
            "http://asr-primary:8095/inference": [
                (200, json.dumps({"error": "failed to read audio data"})),
            ],
            "http://asr-fallback:8096/inference": [
                (200, json.dumps({"text": "fallback transcript"})),
            ],
        }
    )
    monkeypatch.setattr(asr_routes.aiohttp, "ClientSession", session_factory)

    resp = client.post(
        "/api/transcribe_diarized",
        files={"audio": ("clip.webm", _fake_webm_bytes(), "audio/webm")},
    )

    assert resp.status_code == 200
    assert resp.text.strip() == "fallback transcript"


def test_asr_tiny_payload_rejected_before_upstream(client, monkeypatch, tmp_path):
    from server.app import app
    from server.core.dependencies import require_api_bearer
    import server.routes.asr as asr_routes

    app.dependency_overrides[require_api_bearer] = lambda: True
    _set_metrics_log_path(monkeypatch, tmp_path)
    _set_asr_env(monkeypatch)
    _reset_asr_state(asr_routes)

    session_factory = _FakeClientSessionFactory(
        {
            "http://asr-primary:8095/inference": [
                (200, json.dumps({"text": "should not be called"})),
            ]
        }
    )
    monkeypatch.setattr(asr_routes.aiohttp, "ClientSession", session_factory)

    resp = client.post(
        "/api/transcribe_diarized",
        files={"audio": ("clip.webm", b"\x1c\x53\xbb\x6b\x80", "audio/webm")},
    )

    assert resp.status_code == 400
    assert "too small" in resp.text.lower()


def test_asr_accepts_ios_mp4_magic_even_when_named_webm(client, monkeypatch, tmp_path):
    from server.app import app
    from server.core.dependencies import require_api_bearer
    import server.routes.asr as asr_routes

    app.dependency_overrides[require_api_bearer] = lambda: True
    _set_metrics_log_path(monkeypatch, tmp_path)
    _set_asr_env(monkeypatch)
    _reset_asr_state(asr_routes)

    assert asr_routes._suffix_for_normalization(
        _fake_mp4_bytes(),
        "recording.webm",
        "audio/webm",
    ) == ".m4a"

    session_factory = _FakeClientSessionFactory(
        {
            "http://asr-primary:8095/inference": [
                (200, json.dumps({"text": "mp4 ok"})),
            ],
        }
    )
    monkeypatch.setattr(asr_routes.aiohttp, "ClientSession", session_factory)

    resp = client.post(
        "/api/transcribe_diarized",
        files={"audio": ("recording.webm", _fake_mp4_bytes(), "audio/webm")},
    )
    assert resp.status_code == 200
    assert resp.text.strip() == "mp4 ok"


def test_asr_engine_reports_ffmpeg_and_normalization(client, monkeypatch, tmp_path):
    from server.app import app
    from server.core.dependencies import require_api_bearer
    import server.routes.asr as asr_routes

    app.dependency_overrides[require_api_bearer] = lambda: True
    _set_metrics_log_path(monkeypatch, tmp_path)
    _set_asr_env(monkeypatch)
    monkeypatch.setenv("ASR_NORMALIZE_TO_WAV", "1")
    monkeypatch.setattr(asr_routes, "_primary_down_until", 0.0)
    monkeypatch.setattr(asr_routes, "_rr_counter", 0)
    monkeypatch.setattr(asr_routes, "_resolve_ffmpeg_bin", lambda: r"C:\ffmpeg\bin\ffmpeg.exe")

    class _FakeGetSession(_FakeClientSession):
        def get(self, url):
            if url.endswith("/asr_engine"):
                return _FakeResponse(200, json.dumps({"engine": "whisper.cpp", "model": "base.en"}))
            return _FakeResponse(404, "")

    class _FakeGetSessionFactory:
        def __call__(self, *args, **kwargs):
            return _FakeGetSession({})

    monkeypatch.setattr(asr_routes.aiohttp, "ClientSession", _FakeGetSessionFactory())

    resp = client.get("/api/asr_engine")
    assert resp.status_code == 200
    body = resp.json()
    assert body["normalize_to_wav"] == "1"
    assert body["ffmpeg_bin"] == r"C:\ffmpeg\bin\ffmpeg.exe"


def test_asr_per_url_cooldown_deprioritizes_failed_url(monkeypatch):
    """A URL that returned 5xx must be pushed to the back of the candidate list on the next request."""
    import server.routes.asr as asr_routes

    monkeypatch.setenv("ASR_URL", "http://127.0.0.1:8095")
    monkeypatch.setenv("ASR_URL_FALLBACK", "")
    monkeypatch.delenv("ASR_URLS", raising=False)
    _reset_asr_state(asr_routes)

    pool = asr_routes._candidate_pool_urls()
    assert "http://127.0.0.1:8095" in pool
    assert "http://127.0.0.1:8096" in pool
    assert "http://127.0.0.1:8097" not in pool

    # Mark 8096 as cooling — it should fall to the back even when sticky-pinned to it.
    asr_routes._mark_url_down("http://127.0.0.1:8096")
    ordered = asr_routes._candidate_urls(preferred_index=1)
    assert ordered[-1] == "http://127.0.0.1:8096"
    assert "http://127.0.0.1:8096" in ordered


def test_asr_url_cooldown_clears_after_success(monkeypatch):
    import server.routes.asr as asr_routes

    monkeypatch.setenv("ASR_URL", "http://127.0.0.1:8095")
    monkeypatch.setenv("ASR_URL_FALLBACK", "")
    _reset_asr_state(asr_routes)

    asr_routes._mark_url_down("http://127.0.0.1:8096")
    assert asr_routes._url_down_until.get("http://127.0.0.1:8096", 0) > 0

    asr_routes._mark_url_healthy("http://127.0.0.1:8096")
    assert "http://127.0.0.1:8096" not in asr_routes._url_down_until


def test_asr_full_mode_uses_long_timeout(monkeypatch, client, tmp_path):
    from server.app import app
    from server.core.dependencies import require_api_bearer
    import server.routes.asr as asr_routes

    app.dependency_overrides[require_api_bearer] = lambda: True
    _set_metrics_log_path(monkeypatch, tmp_path)
    _set_asr_env(monkeypatch)
    _reset_asr_state(asr_routes)

    captured = {}
    real_timeout = asr_routes.aiohttp.ClientTimeout

    def _capture_timeout(*args, **kwargs):
        captured["timeout"] = kwargs
        return real_timeout(*args, **kwargs)

    monkeypatch.setattr(asr_routes.aiohttp, "ClientTimeout", _capture_timeout)

    session_factory = _FakeClientSessionFactory(
        {
            "http://asr-primary:8095/inference": [
                (200, json.dumps({"text": "hello world"})),
            ],
        }
    )
    monkeypatch.setattr(asr_routes.aiohttp, "ClientSession", session_factory)

    resp = client.post(
        "/api/transcribe_diarized",
        files={"audio": ("clip.wav", _wav_bytes(), "audio/wav")},
    )
    assert resp.status_code == 200

    # Full file: long budget required for multi-minute recordings.
    assert "timeout" in captured
    assert captured["timeout"]["total"] >= 180


def test_asr_500_marks_url_down_for_subsequent_routing(monkeypatch, client, tmp_path):
    """A real 5xx from upstream must register that URL in per-URL cooldown."""
    from server.app import app
    from server.core.dependencies import require_api_bearer
    import server.routes.asr as asr_routes

    app.dependency_overrides[require_api_bearer] = lambda: True
    _set_metrics_log_path(monkeypatch, tmp_path)
    _set_asr_env(monkeypatch, primary="http://asr-primary:8095", fallback="http://asr-fallback:8096")
    _reset_asr_state(asr_routes)

    session_factory = _FakeClientSessionFactory(
        {
            "http://asr-primary:8095/inference": [
                (500, "boom"),
            ],
            "http://asr-fallback:8096/inference": [
                (200, json.dumps({"text": "ok"})),
            ],
        }
    )
    monkeypatch.setattr(asr_routes.aiohttp, "ClientSession", session_factory)

    resp = client.post(
        "/api/transcribe_diarized",
        files={"audio": ("clip.wav", _wav_bytes(), "audio/wav")},
    )
    assert resp.status_code == 200
    assert resp.text.strip() == "ok"
    # Primary is now in per-URL cooldown.
    assert asr_routes._url_down_until.get("http://asr-primary:8095", 0) > 0
    # And fallback should be marked healthy after success.
    assert "http://asr-fallback:8096" not in asr_routes._url_down_until


def test_asr_429_marks_url_down_and_fails_over(monkeypatch, client, tmp_path):
    from server.app import app
    from server.core.dependencies import require_api_bearer
    import server.routes.asr as asr_routes

    app.dependency_overrides[require_api_bearer] = lambda: True
    _set_metrics_log_path(monkeypatch, tmp_path)
    _set_asr_env(monkeypatch, primary="http://asr-primary:8095", fallback="http://asr-fallback:8096")
    _reset_asr_state(asr_routes)

    session_factory = _FakeClientSessionFactory(
        {
            "http://asr-primary:8095/inference": [
                (429, "too many requests"),
            ],
            "http://asr-fallback:8096/inference": [
                (200, json.dumps({"text": "fallback ok"})),
            ],
        }
    )
    monkeypatch.setattr(asr_routes.aiohttp, "ClientSession", session_factory)

    resp = client.post(
        "/api/transcribe_diarized",
        files={"audio": ("clip.wav", _wav_bytes(), "audio/wav")},
    )
    assert resp.status_code == 200
    assert resp.text.strip() == "fallback ok"
    assert asr_routes._url_down_until.get("http://asr-primary:8095", 0) > 0


def test_asr_retryable_payload_error_marks_url_down(monkeypatch, client, tmp_path):
    from server.app import app
    from server.core.dependencies import require_api_bearer
    import server.routes.asr as asr_routes

    app.dependency_overrides[require_api_bearer] = lambda: True
    _set_metrics_log_path(monkeypatch, tmp_path)
    _set_asr_env(monkeypatch, primary="http://asr-primary:8095", fallback="http://asr-fallback:8096")
    _reset_asr_state(asr_routes)

    session_factory = _FakeClientSessionFactory(
        {
            "http://asr-primary:8095/inference": [
                (200, json.dumps({"error": "server busy, try again"})),
            ],
            "http://asr-fallback:8096/inference": [
                (200, json.dumps({"text": "fallback transcript"})),
            ],
        }
    )
    monkeypatch.setattr(asr_routes.aiohttp, "ClientSession", session_factory)

    resp = client.post(
        "/api/transcribe_diarized",
        files={"audio": ("clip.webm", _fake_webm_bytes(), "audio/webm")},
    )

    assert resp.status_code == 200
    assert resp.text.strip() == "fallback transcript"
    assert asr_routes._url_down_until.get("http://asr-primary:8095", 0) > 0


def test_asr_candidate_pool_uses_asr_urls_env(monkeypatch):
    import server.routes.asr as asr_routes

    monkeypatch.delenv("ASR_URL", raising=False)
    monkeypatch.delenv("ASR_URL_FALLBACK", raising=False)
    monkeypatch.setenv("ASR_URLS", "http://asr-a:8095, http://asr-b:8096 ; http://asr-a:8095")
    _reset_asr_state(asr_routes)

    pool = asr_routes._candidate_pool_urls()
    assert pool == ["http://asr-a:8095", "http://asr-b:8096"]


def test_asr_candidate_pool_defaults_to_localhost_when_unset(monkeypatch):
    import server.routes.asr as asr_routes

    monkeypatch.delenv("ASR_URL", raising=False)
    monkeypatch.delenv("ASR_URL_FALLBACK", raising=False)
    monkeypatch.delenv("ASR_URLS", raising=False)
    _reset_asr_state(asr_routes)

    pool = asr_routes._candidate_pool_urls()
    assert pool[0] == "http://127.0.0.1:8095"
    assert "http://127.0.0.1:8096" in pool


def test_asr_candidate_pool_respects_explicit_empty_primary(monkeypatch):
    import server.routes.asr as asr_routes

    monkeypatch.setenv("ASR_URL", "")
    monkeypatch.delenv("ASR_URL_FALLBACK", raising=False)
    monkeypatch.delenv("ASR_URLS", raising=False)
    _reset_asr_state(asr_routes)

    assert asr_routes._candidate_pool_urls() == []


def test_asr_url_whitespace_only_falls_back_to_default(monkeypatch):
    """Stray whitespace in ASR_URL must not yield an empty upstream pool."""
    import server.routes.asr as asr_routes

    monkeypatch.setenv("ASR_URL", "   \t  ")
    monkeypatch.delenv("ASR_URL_FALLBACK", raising=False)
    monkeypatch.delenv("ASR_URLS", raising=False)
    _reset_asr_state(asr_routes)

    assert asr_routes._asr_url() == "http://127.0.0.1:8095"
    pool = asr_routes._candidate_pool_urls()
    assert pool and pool[0] == "http://127.0.0.1:8095"


def test_asr_request_works_with_asr_urls_only(client, monkeypatch, tmp_path):
    from server.app import app
    from server.core.dependencies import require_api_bearer
    import server.routes.asr as asr_routes

    app.dependency_overrides[require_api_bearer] = lambda: True
    _set_metrics_log_path(monkeypatch, tmp_path)
    monkeypatch.delenv("ASR_URL", raising=False)
    monkeypatch.delenv("ASR_URL_FALLBACK", raising=False)
    monkeypatch.setenv("ASR_URLS", "http://asr-a:8095,http://asr-b:8096")
    monkeypatch.setenv("ASR_API_KEY", "notegenadmin")
    monkeypatch.setenv("ASR_NORMALIZE_TO_WAV", "0")
    _reset_asr_state(asr_routes)

    session_factory = _FakeClientSessionFactory(
        {
            "http://asr-a:8095/inference": [
                (200, json.dumps({"text": "from pooled endpoints"})),
            ],
        }
    )
    monkeypatch.setattr(asr_routes.aiohttp, "ClientSession", session_factory)

    resp = client.post(
        "/api/transcribe_diarized",
        files={"audio": ("clip.webm", _fake_webm_bytes(), "audio/webm")},
    )
    assert resp.status_code == 200
    assert resp.text.strip() == "from pooled endpoints"


def test_asr_wav_mislabeled_as_webm_still_normalizes(client, monkeypatch, tmp_path):
    """Server sniffs RIFF/WAVE magic so a wrongly named .webm filename still normalizes."""
    from server.app import app
    from server.core.dependencies import require_api_bearer
    import server.routes.asr as asr_routes

    app.dependency_overrides[require_api_bearer] = lambda: True
    _set_metrics_log_path(monkeypatch, tmp_path)
    _set_asr_env(monkeypatch)
    monkeypatch.setenv("ASR_NORMALIZE_TO_WAV", "1")
    _reset_asr_state(asr_routes)

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(b"\x00\x00" * 800)
    wav_bytes = buf.getvalue()

    session_factory = _FakeClientSessionFactory(
        {
            "http://asr-primary:8095/inference": [
                (200, json.dumps({"text": " ok "})),
            ],
        }
    )
    monkeypatch.setattr(asr_routes.aiohttp, "ClientSession", session_factory)

    resp = client.post(
        "/api/transcribe_diarized",
        files={"audio": ("recording.webm", wav_bytes, "audio/webm")},
    )
    assert resp.status_code == 200
    assert resp.text.strip() == "ok"


def test_asr_full_mode_normalization_failure_falls_through_to_upstream(client, monkeypatch, tmp_path):
    """Full-file mode keeps the resilient pass-through behavior so legitimate uploads where
    whisper-server's bundled ffmpeg can decode something ours can't still succeed."""
    from server.app import app
    from server.core.dependencies import require_api_bearer
    import server.routes.asr as asr_routes

    app.dependency_overrides[require_api_bearer] = lambda: True
    _set_metrics_log_path(monkeypatch, tmp_path)
    _set_asr_env(monkeypatch)
    monkeypatch.setenv("ASR_NORMALIZE_TO_WAV", "1")
    _reset_asr_state(asr_routes)

    def _explode(data, filename, content_type, *, strict=False):
        if strict:
            raise asr_routes.AudioNormalizationError("fragmented webm: no EBML header")
        return data, filename, content_type

    monkeypatch.setattr(asr_routes, "_normalize_audio_to_wav", _explode)

    session_factory = _FakeClientSessionFactory(
        {
            "http://asr-primary:8095/inference": [
                (200, json.dumps({"text": "ok"})),
            ],
        }
    )
    monkeypatch.setattr(asr_routes.aiohttp, "ClientSession", session_factory)

    resp = client.post(
        "/api/transcribe_diarized",
        files={"audio": ("recording.webm", _fake_webm_bytes(), "audio/webm")},
    )
    assert resp.status_code == 200
    assert resp.text.strip() == "ok"


def test_asr_auto_derives_localhost_fallback_when_unset(client, monkeypatch, tmp_path):
    from server.app import app
    from server.core.dependencies import require_api_bearer
    import server.routes.asr as asr_routes

    app.dependency_overrides[require_api_bearer] = lambda: True
    _set_metrics_log_path(monkeypatch, tmp_path)
    _set_asr_env(monkeypatch, primary="http://127.0.0.1:8095", fallback="")
    monkeypatch.setattr(asr_routes, "_primary_down_until", 0.0)
    monkeypatch.setattr(asr_routes, "_rr_counter", 0)

    session_factory = _FakeClientSessionFactory(
        {
            "http://127.0.0.1:8095/inference": [
                (200, json.dumps({"error": "failed to read audio data"})),
            ],
            "http://127.0.0.1:8096/inference": [
                (200, json.dumps({"text": "auto fallback transcript"})),
            ],
        }
    )
    monkeypatch.setattr(asr_routes.aiohttp, "ClientSession", session_factory)

    resp = client.post(
        "/api/transcribe_diarized",
        files={"audio": ("clip.webm", _fake_webm_bytes(), "audio/webm")},
    )

    assert resp.status_code == 200
    assert resp.text.strip() == "auto fallback transcript"


def test_asr_client_incident_writes_last_incident_file(client, monkeypatch, tmp_path):
    from pathlib import Path
    import json

    from server.app import app
    from server.core.dependencies import require_api_bearer

    app.dependency_overrides[require_api_bearer] = lambda: True

    diag_dir = tmp_path / "diag"
    monkeypatch.setenv("ASR_INCIDENT_DIR", str(diag_dir))

    tid = "test_trace_123"
    resp = client.post(
        "/api/asr_client_incident",
        json={
            "trace_id": tid,
            "stage": "client.unit_test",
            "outcome": "ok",
            "message": "hello",
        },
    )
    assert resp.status_code == 200

    last_path = Path(diag_dir) / "last_incident.json"
    assert last_path.is_file()
    payload = json.loads(last_path.read_text(encoding="utf-8"))
    assert payload["trace_id"] == tid
    assert payload["stage"] == "client.unit_test"

    log_path = Path(diag_dir) / "incidents.jsonl"
    assert log_path.is_file()
