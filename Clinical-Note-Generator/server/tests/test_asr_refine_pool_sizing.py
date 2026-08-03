"""P2-7 regression: asr_refine's thread pool must be configurable and must
default to something larger than the old hardcoded 4 -- with ~50 doctors,
several concurrent diarize-refine calls (each blocking a FastAPI worker
thread on future.result() for up to ASR_REFINE_TIMEOUT_SEC) is a realistic
burst, not an edge case, and a too-small pool serializes calls behind it
even when FastAPI's own worker pool has threads free.
"""
import importlib

from server.core import asr_refine


def _reimport_with_env(monkeypatch, value=None):
    if value is None:
        monkeypatch.delenv("ASR_REFINE_MAX_WORKERS", raising=False)
    else:
        monkeypatch.setenv("ASR_REFINE_MAX_WORKERS", value)
    return importlib.reload(asr_refine)


def test_default_pool_size_is_larger_than_the_old_hardcoded_four(monkeypatch):
    mod = _reimport_with_env(monkeypatch)
    try:
        assert mod.ASR_REFINE_MAX_WORKERS > 4
        assert mod._thread_pool._max_workers == mod.ASR_REFINE_MAX_WORKERS
    finally:
        _reimport_with_env(monkeypatch)  # restore a clean default-config module for later tests


def test_pool_size_is_configurable_via_env_var(monkeypatch):
    mod = _reimport_with_env(monkeypatch, "20")
    try:
        assert mod.ASR_REFINE_MAX_WORKERS == 20
        assert mod._thread_pool._max_workers == 20
    finally:
        _reimport_with_env(monkeypatch)


def test_pool_size_env_var_cannot_go_below_one(monkeypatch):
    mod = _reimport_with_env(monkeypatch, "0")
    try:
        assert mod.ASR_REFINE_MAX_WORKERS == 1
    finally:
        _reimport_with_env(monkeypatch)
