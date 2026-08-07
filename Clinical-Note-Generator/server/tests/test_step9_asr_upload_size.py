"""STEP 9e tests: ASR upload size enforcement (M-ASR).

Regression: transcribe_diarized() called `await audio.read()` with no size
cap, materialising arbitrarily large uploads fully into RAM. Now reads via
_read_audio_bounded() which rejects 413 once the configurable cap is exceeded
while streaming, and closes the upload handle.
"""
import pytest


class _FakeUpload:
    def __init__(self, data):
        self._data = data
        self._pos = 0
        self.closed = False

    async def read(self, n=-1):
        if self._pos >= len(self._data):
            return b""
        chunk = self._data[self._pos:self._pos + n]
        self._pos += len(chunk)
        return chunk

    async def close(self):
        self.closed = True

    def __iter__(self):
        raise NotImplementedError


@pytest.mark.anyio
async def test_read_audio_bounded_under_cap(monkeypatch):
    from fastapi import HTTPException
    from server.routes.asr import _read_audio_bounded
    import asyncio

    monkeypatch.setenv("ASR_MAX_UPLOAD_BYTES", "1000")
    up = _FakeUpload(b"x" * 900)
    out = await _read_audio_bounded(up, "t1")
    assert out == b"x" * 900
    assert up.closed is True  # upload handle closed after read


@pytest.mark.anyio
async def test_read_audio_bounded_rejects_oversize(monkeypatch):
    from fastapi import HTTPException
    from server.routes.asr import _read_audio_bounded

    monkeypatch.setenv("ASR_MAX_UPLOAD_BYTES", "1000")
    up = _FakeUpload(b"x" * 5000)
    with pytest.raises(HTTPException) as ei:
        await _read_audio_bounded(up, "t2")
    assert ei.value.status_code == 413
    assert "maximum upload size" in ei.value.detail["message"]
    assert up.closed is True
