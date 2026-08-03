import threading

from server.core.stores.ttl_store import TTLStore


def test_ttl_store_put_get():
    store = TTLStore(ttl_seconds=60)
    store.put("a", {"v": 1})
    assert store.get("a") == {"v": 1}


def test_ttl_store_evict_on_access(monkeypatch):
    from server.core.stores import ttl_store as ttl_mod

    now = [1000.0]
    monkeypatch.setattr(ttl_mod.time, "time", lambda: now[0])

    store = TTLStore(ttl_seconds=10)
    store.put("a", "x")
    assert store.get("a") == "x"

    now[0] = 1011.0
    assert store.get("a") is None
    assert "a" not in store


def test_ttl_store_evict_expired(monkeypatch):
    from server.core.stores import ttl_store as ttl_mod

    now = [2000.0]
    monkeypatch.setattr(ttl_mod.time, "time", lambda: now[0])

    store = TTLStore(ttl_seconds=10)
    store.put("a", 1)
    store.put("b", 2)

    now[0] = 2011.0
    removed = store.evict_expired()
    assert removed == 2
    assert len(store) == 0


def test_ttl_store_thread_safety():
    store = TTLStore(ttl_seconds=60)

    def worker(offset: int):
        for i in range(500):
            key = f"k-{offset}-{i}"
            store.put(key, i)
            assert store.get(key) == i

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(store) == 4000
