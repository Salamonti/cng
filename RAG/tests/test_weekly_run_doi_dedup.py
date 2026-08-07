"""Regression test: the DOI-dedup step actually run by production
(scripts/weekly_run.sh -> doi_dedup.py) must scope dedup by (doc_id, doi) --
NOT by doi alone.

Historical bug this guards: grouping deleted by doi alone so that two
UNRELATED documents that happen to share a doi-looking metadata value had
all but one of their combined chunks deleted. The fix scopes by
(doc_id, doi): only duplicate chunks of the SAME document are removed.

The deployed weekly_run.sh no longer inlines a python -c block; it invokes
the standalone doi_dedup.py via run_tool_optional. We therefore test that
real doi_dedup.py file (chosen by the production script), statically for
the grouping key and behaviorally by running it against a fake in-memory
chromadb collection.
"""
import os
import re
import sys
import types

import pytest

_RAG_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEEKLY_RUN = os.path.join(_RAG_DIR, "scripts", "weekly_run.sh")
DOI_DEDUP = os.path.join(_RAG_DIR, "doi_dedup.py")

# ---------------------------------------------------------------------------
# Fake chromadb so we can execute the real doi_dedup.py in isolation.
# ---------------------------------------------------------------------------

_SHARED: "dict[str, object]" = {}


class _FakeSettings:
    def __init__(self, **kwargs):
        pass


class _FakeCollection:
    def __init__(self, records):
        # records: list of (id, metadata)
        self._records = list(records)
        self.deleted = []

    def count(self):
        return len(self._records)

    def get(self, offset=0, limit=1000, include=None):
        sl = self._records[offset:offset + limit]
        return {"ids": [i for i, _ in sl], "metadatas": [m for _, m in sl]}

    def delete(self, ids=None):
        ids = list(ids or [])
        self.deleted.extend(ids)
        self._records = [r for r in self._records if r[0] not in ids]


class _FakeClient:
    def __init__(self, *args, **kwargs):
        pass

    def get_or_create_collection(self, name=None):
        return _SHARED["collection"]


def _install_fake_chromadb(records):
    col = _FakeCollection(records)
    _SHARED["collection"] = col
    chromadb = types.ModuleType("chromadb")
    chromadb.PersistentClient = _FakeClient
    config = types.ModuleType("chromadb.config")
    config.Settings = _FakeSettings
    sys.modules["chromadb"] = chromadb
    sys.modules["chromadb.config"] = config
    return col


def _read_doi_dedup_source():
    with open(DOI_DEDUP, "r", encoding="utf-8") as f:
        return f.read()


@pytest.fixture(autouse=True)
def _cleanup_fake_chromadb():
    yield
    sys.modules.pop("chromadb", None)
    sys.modules.pop("chromadb.config", None)


# ---------------------------------------------------------------------------


def test_weekly_run_invokes_doi_dedup_py():
    """The script the production timer runs must call doi_dedup.py."""
    with open(WEEKLY_RUN, "r", encoding="utf-8") as f:
        content = f.read()
    assert "doi_dedup" in content, "weekly_run.sh must invoke the doi-dedup step"


def test_groups_by_doc_id_and_doi_not_doi_alone():
    src = _read_doi_dedup_source()
    # Grouping MUST be keyed on (doc_id, doi)...
    assert re.search(r"doi_groups\s*\[\s*\([^)]*doc_id[^)]*doi[^)]*\)", src) or "(doc_id, doi)" in src, (
        "doi_dedup.py must group by (doc_id, doi)"
    )
    # ...and MUST NOT be the historical buggy grouping by doi alone.
    for line in src.splitlines():
        assert not re.search(r"doi_groups\[\s*doi\s*\]", line), (
            "doi_dedup.py must NOT group by doi alone"
        )


def _same_doc_and_distinct_doc_records():
    return [
        # Two chunks of doc A sharing a DOI (real duplicate chunks -> dedupe)
        ("A_chunk_0", {"doc_id": "A", "doi": "10.1/aaa"}),
        ("A_chunk_1", {"doc_id": "A", "doi": "10.1/aaa"}),
        # A DIFFERENT document B that coincidentally has the SAME doi value ->
        # must NOT have its chunks cross-merged with A
        ("B_chunk_0", {"doc_id": "B", "doi": "10.1/aaa"}),
        ("B_chunk_1", {"doc_id": "B", "doi": "10.1/aaa"}),
        # Single chunk, unique doi -> untouched
        ("C_chunk_0", {"doc_id": "C", "doi": "10.2/bbb"}),
        # No doi / placeholder doi -> untouched
        ("D_chunk_0", {"doc_id": "D"}),
        ("E_chunk_0", {"doc_id": "E", "doi": "N/A"}),
    ]


def test_different_documents_sharing_a_doi_are_not_merged():
    col = _install_fake_chromadb(_same_doc_and_distinct_doc_records())
    exec(compile(_read_doi_dedup_source(), "doi_dedup.py", "exec"), {})
    # Only same-(doc_id, doi) duplicates deleted: A_chunk_1 and B_chunk_1.
    assert set(col.deleted) == {"A_chunk_1", "B_chunk_1"}
    remaining = {i for i, _ in col._records}
    # One chunk from each of A and B must survive, even though they share a doi.
    assert "A_chunk_0" in remaining
    assert "B_chunk_0" in remaining


def test_records_without_doi_are_left_alone():
    col = _install_fake_chromadb(_same_doc_and_distinct_doc_records())
    exec(compile(_read_doi_dedup_source(), "doi_dedup.py", "exec"), {})
    assert "D_chunk_0" not in col.deleted
    assert "E_chunk_0" not in col.deleted
    assert "C_chunk_0" not in col.deleted
