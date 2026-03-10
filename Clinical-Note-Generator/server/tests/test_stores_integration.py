from server.core.stores.generation_store import (
    _generation_cache,
    _generation_meta,
    _consult_comment_store,
    _order_request_store,
)


def _clear_all():
    _generation_cache.clear()
    _generation_meta.clear()
    _consult_comment_store.clear()
    _order_request_store.clear()


def test_generation_store_retention(monkeypatch):
    from server.core.stores import ttl_store as ttl_mod

    _clear_all()
    now = [1000.0]
    monkeypatch.setattr(ttl_mod.time, "time", lambda: now[0])

    _generation_cache["g1"] = {"prompt": "p", "output": "o"}
    assert _generation_cache.get("g1") == {"prompt": "p", "output": "o"}


def test_meta_store_ttl(monkeypatch):
    from server.core.stores import ttl_store as ttl_mod

    _clear_all()
    now = [1000.0]
    monkeypatch.setattr(ttl_mod.time, "time", lambda: now[0])

    _generation_meta["g2"] = {"refs": []}
    now[0] = 1000.0 + 86401.0
    assert _generation_meta.get("g2") is None


def test_consult_store_ttl(monkeypatch):
    from server.core.stores import ttl_store as ttl_mod

    _clear_all()
    now = [1000.0]
    monkeypatch.setattr(ttl_mod.time, "time", lambda: now[0])

    _consult_comment_store["g3"] = {"status": "done"}
    now[0] = 1000.0 + 86401.0
    assert _consult_comment_store.get("g3") is None


def test_order_store_ttl(monkeypatch):
    from server.core.stores import ttl_store as ttl_mod

    _clear_all()
    now = [1000.0]
    monkeypatch.setattr(ttl_mod.time, "time", lambda: now[0])

    _order_request_store["g4"] = {"status": "done", "items": []}
    now[0] = 1000.0 + 86401.0
    assert _order_request_store.get("g4") is None
