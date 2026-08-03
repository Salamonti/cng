from store import iter_collection_pages


class _FakeCol:
    def __init__(self, pages):
        self._pages = list(pages)
        self.calls = []

    def get(self, **kwargs):
        self.calls.append(kwargs)
        if not self._pages:
            return {"ids": []}
        return self._pages.pop(0)


def test_iter_collection_pages_walks_offsets():
    col = _FakeCol([
        {"ids": ["a", "b"], "documents": ["A", "B"]},
        {"ids": ["c"], "documents": ["C"]},
    ])
    ids = []
    for page in iter_collection_pages(col, include=["documents"], page_size=2):
        ids.extend(page["ids"])
    assert ids == ["a", "b", "c"]
    assert col.calls[0] == {"limit": 2, "offset": 0, "include": ["documents"]}
    assert col.calls[1] == {"limit": 2, "offset": 2, "include": ["documents"]}
