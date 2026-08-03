import time
import tracemalloc

from server.core.stores.ttl_store import TTLStore


def test_ttl_perf(monkeypatch):
    from server.core.stores import ttl_store as ttl_mod

    now = [1000.0]
    monkeypatch.setattr(ttl_mod.time, "time", lambda: now[0])

    store = TTLStore(ttl_seconds=86400)

    tracemalloc.start()
    for i in range(10_000):
        store.put(f"k{i}", {"v": i})
    current_before, peak_before = tracemalloc.get_traced_memory()

    now[0] += 86401.0
    t0 = time.perf_counter()
    removed = store.evict_expired()
    dt = time.perf_counter() - t0
    current_after, _peak_after = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    assert removed == 10_000
    assert len(store) == 0
    # Performance guardrail: sweep should stay in practical linear time for 10k entries.
    assert dt < 1.0
    # Memory should not continue growing after eviction for this simulation.
    assert current_after <= peak_before
    assert current_before >= 0
