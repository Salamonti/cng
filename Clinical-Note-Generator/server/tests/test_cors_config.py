def test_cors_allow_all_disables_credentials(monkeypatch):
    monkeypatch.setenv("CORS_ALLOW_ALL", "1")
    from server.core.cors_config import build_cors_params

    origins, creds, rx = build_cors_params()
    assert origins == ["*"]
    assert creds is False
    assert rx is None


def test_cors_default_uses_regex_and_credentials(monkeypatch):
    monkeypatch.delenv("CORS_ALLOW_ALL", raising=False)
    monkeypatch.delenv("CORS_ALLOWED_ORIGINS", raising=False)
    monkeypatch.delenv("CORS_ALLOW_ORIGIN_REGEX", raising=False)
    from server.core.cors_config import build_cors_params

    origins, creds, rx = build_cors_params()
    assert origins == []
    assert creds is True
    assert rx and "ieissa" in rx


def test_cors_middleware_kwargs_shape(monkeypatch):
    monkeypatch.delenv("CORS_ALLOW_ALL", raising=False)
    from server.core.cors_config import cors_middleware_kwargs

    kw = cors_middleware_kwargs()
    assert "allow_origins" in kw and "expose_headers" in kw
    assert kw["expose_headers"] == ["X-Generation-Id", "X-ASR-Trace-Id"]
