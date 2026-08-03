"""Regression test (P1-6): _QA_STATE and _QA_VISION_IMAGE were plain
unbounded dicts keyed by (user_id, session_id) with no eviction -- over a
long-running process they accumulate PHI-adjacent QA turns and raw
uploaded images forever. _BoundedSessionStore must cap entry count (LRU
eviction) and expire stale entries (TTL).
"""
from server.routes.qa_chat import _BoundedSessionStore


def test_lru_eviction_when_over_capacity():
    store = _BoundedSessionStore(max_entries=3, ttl_seconds=0)
    store.set(("u", "s1"), {"n": 1})
    store.set(("u", "s2"), {"n": 2})
    store.set(("u", "s3"), {"n": 3})
    assert len(store) == 3

    # Adding a 4th entry evicts the least-recently-used one (s1).
    store.set(("u", "s4"), {"n": 4})
    assert len(store) == 3
    assert store.get(("u", "s1")) is None
    assert store.get(("u", "s2")) == {"n": 2}
    assert store.get(("u", "s4")) == {"n": 4}


def test_get_refreshes_recency_so_it_is_not_the_next_eviction_target():
    store = _BoundedSessionStore(max_entries=2, ttl_seconds=0)
    store.set(("u", "s1"), {"n": 1})
    store.set(("u", "s2"), {"n": 2})

    # Touch s1 so it becomes the most-recently-used entry.
    store.get(("u", "s1"))

    # Adding a 3rd entry should now evict s2 (now the least-recently-used),
    # not s1.
    store.set(("u", "s3"), {"n": 3})
    assert store.get(("u", "s1")) == {"n": 1}
    assert store.get(("u", "s2")) is None
    assert store.get(("u", "s3")) == {"n": 3}


def test_ttl_expires_stale_entries(monkeypatch):
    import server.routes.qa_chat as qa_chat

    fake_now = [1_000_000.0]
    monkeypatch.setattr(qa_chat.time, "time", lambda: fake_now[0])

    store = _BoundedSessionStore(max_entries=100, ttl_seconds=60)
    store.set(("u", "s1"), {"n": 1})
    assert store.get(("u", "s1")) == {"n": 1}

    fake_now[0] += 61  # past the TTL
    assert store.get(("u", "s1")) is None


def test_pop_removes_entry():
    store = _BoundedSessionStore(max_entries=10, ttl_seconds=0)
    store.set(("u", "s1"), {"n": 1})
    store.pop(("u", "s1"))
    assert store.get(("u", "s1")) is None
    # Popping a key that was never present must not raise.
    store.pop(("u", "does-not-exist"))
