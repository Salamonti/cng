"""
Stress: concurrent full-file /api/transcribe_diarized.

Models clinical use (“record → transcribe full clip on stop”):
- WAV lengths between 3 and 66 seconds (payload size like real dictation).
- At most 8 requests in flight at once; extra jobs wait (e.g. 11 jobs → 3 queued).

Uses httpx ASGITransport + mocked upstream whisper HTTP.
Run: pytest server/tests/test_asr_concurrent_stress.py -v
"""

import asyncio
import io
import json
import wave

import httpx
import pytest
from httpx import ASGITransport

# Match realistic operator expectation: pool / GPU slots ≈ 8, surplus waits.
MAX_CONCURRENT = 8
# Explicitly > MAX_CONCURRENT so some requests must queue (8 active + 3 waiting).
TOTAL_JOBS = 11
MIN_SEC = 3.0
MAX_SEC = 66.0


def _pcm_wav_bytes(duration_sec: float) -> bytes:
    """16 kHz mono PCM WAV (silence); duration matches real recording length."""
    buf = io.BytesIO()
    rate = 16000
    n = max(1, int(rate * max(MIN_SEC, duration_sec)))
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(b"\x00\x00" * n)
    return buf.getvalue()


def _durations_linear(n: int) -> list:
    """Spread from MIN_SEC to MAX_SEC inclusive."""
    if n < 2:
        return [MIN_SEC]
    return [MIN_SEC + (MAX_SEC - MIN_SEC) * i / (n - 1) for i in range(n)]


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


class _StressSharedSession:
    """Shared pop queue across concurrent ClientSession instances."""

    def __init__(self, factory: "_StressFakeSessionFactory"):
        self._f = factory

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def post(self, url, data=None, headers=None):
        if not self._f._plan:
            raise RuntimeError(f"Stress fake queue exhausted for {url}")
        body = self._f._plan.pop(0)
        return _FakeResponse(status=200, body=body)


class _StressFakeSessionFactory:
    def __init__(self, response_bodies: list):
        self._plan = list(response_bodies)

    def __call__(self, *args, **kwargs):
        return _StressSharedSession(self)


def _set_metrics_log_path(monkeypatch, tmp_path):
    import server.app as app_mod

    monkeypatch.setattr(app_mod._metrics, "http_csv", str(tmp_path / "http_requests_stress.csv"))


def _reset_asr_state(asr_routes):
    asr_routes._primary_down_until = 0.0
    asr_routes._rr_counter = 0
    asr_routes._url_down_until.clear()


@pytest.fixture
def stress_app(tmp_path, monkeypatch):
    """App + sqlite + auth override (same pattern as conftest client)."""
    import server.core.db as db
    from server.app import app
    from sqlmodel import create_engine

    db_file = tmp_path / "stress.sqlite"
    eng = create_engine(
        f"sqlite:///{db_file.as_posix()}",
        connect_args={"check_same_thread": False},
    )
    db.engine = eng
    try:
        import core.db as core_db  # type: ignore

        core_db.engine = eng
    except ImportError:
        pass
    db.init_db()

    from server.core.dependencies import require_api_bearer

    app.dependency_overrides[require_api_bearer] = lambda: True
    yield app
    app.dependency_overrides.clear()


def test_full_file_transcribe_stress_eight_slots_eleven_jobs(stress_app, monkeypatch, tmp_path):
    """
    Realistic full-file stress: 11 jobs, lengths 3–66 s, max 8 concurrent (3 queue).
    Matches default clinical path (full-file upload).
    """
    import server.routes.asr as asr_routes

    _set_metrics_log_path(monkeypatch, tmp_path)
    monkeypatch.setenv("ASR_URL", "http://asr-stress:8095")
    monkeypatch.delenv("ASR_URL_FALLBACK", raising=False)
    monkeypatch.delenv("ASR_URLS", raising=False)
    monkeypatch.setenv("ASR_API_KEY", "notegenadmin")
    monkeypatch.setenv("ASR_NORMALIZE_TO_WAV", "0")
    _reset_asr_state(asr_routes)

    n = TOTAL_JOBS
    upstream_bodies = [json.dumps({"text": f"fullfile-{i} ok"}) for i in range(n)]
    factory = _StressFakeSessionFactory(upstream_bodies)
    monkeypatch.setattr(asr_routes.aiohttp, "ClientSession", factory)

    durations = _durations_linear(n)
    assert abs(durations[0] - MIN_SEC) < 0.01 and abs(durations[-1] - MAX_SEC) < 0.01

    sem = asyncio.Semaphore(MAX_CONCURRENT)
    in_flight = 0
    max_in_flight_seen = 0
    lock = asyncio.Lock()

    async def run_all():
        transport = ASGITransport(app=stress_app)
        # Full-file upstream timeout can be long (server allows large sock_read).
        timeout = httpx.Timeout(600.0)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test", timeout=timeout
        ) as ac:

            async def one(idx: int):
                nonlocal in_flight, max_in_flight_seen
                wav = _pcm_wav_bytes(durations[idx])
                async with sem:
                    async with lock:
                        in_flight += 1
                        max_in_flight_seen = max(max_in_flight_seen, in_flight)
                    try:
                        return await ac.post(
                            "/api/transcribe_diarized",
                            files={"audio": (f"session_{idx}.wav", wav, "audio/wav")},
                        )
                    finally:
                        async with lock:
                            in_flight -= 1

            return await asyncio.gather(*[one(i) for i in range(n)])

    responses = asyncio.run(run_all())

    assert max_in_flight_seen <= MAX_CONCURRENT
    assert len(responses) == n
    assert factory._plan == [], "each request should consume exactly one upstream body"
    for i, r in enumerate(responses):
        assert r.status_code == 200, f"idx={i} status={r.status_code} body={r.text[:300]}"
        assert "fullfile-" in r.text
