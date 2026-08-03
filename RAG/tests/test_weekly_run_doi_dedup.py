"""Regression test: weekly_run.sh's Step 7 (DOI deduplication) -- the script
actually run by the production systemd timer -- must scope dedup by
(doc_id, doi), not by doi alone.

The standalone doi_dedup.py script was already fixed to scope by
(doc_id, doi): two unrelated documents that happen to share a doi-looking
metadata value are NOT the same document and must not have each other's
chunks deleted. weekly_run.sh inlined its own copy of this logic (rather
than calling doi_dedup.py) and, before this fix, that copy still grouped
by doi alone -- so any two unrelated documents sharing a doi value would
have all but one of their combined chunks deleted, in the script that
actually runs weekly in production.

This test extracts the literal Python source of Step 7 from weekly_run.sh
(so it's exercising the real deployed logic, not a hand-copied
approximation) and runs it against a fake in-memory chromadb collection.
"""
import os
import re
import sys
import types
import unittest

_RAG_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _extract_step7_source():
    with open(os.path.join(_RAG_DIR, "weekly_run.sh"), "r", encoding="utf-8") as f:
        content = f.read()
    start = content.index('log "Step 7')
    end = content.index('log "DOI deduplication completed"')
    block = content[start:end]
    m = re.search(r'python3 -c "\n(.*?)\n" 2', block, re.DOTALL)
    assert m, "could not locate Step 7's inline python -c block in weekly_run.sh"
    return m.group(1)


class _FakeCollection:
    def __init__(self, records):
        # records: list of (id, metadata) tuples, insertion order preserved
        self._records = list(records)

    def count(self):
        return len(self._records)

    def get(self, limit=None, offset=0, include=None):
        page = self._records[offset:offset + limit] if limit is not None else self._records[offset:]
        return {"ids": [r[0] for r in page], "metadatas": [r[1] for r in page]}

    def delete(self, ids):
        id_set = set(ids)
        self._records = [r for r in self._records if r[0] not in id_set]

    def remaining_ids(self):
        return [r[0] for r in self._records]


def _run_step7_against(collection):
    code = _extract_step7_source()

    class _FakeSettings:
        def __init__(self, **kwargs):
            pass

    class _FakePersistentClient:
        def __init__(self, path=None, settings=None):
            pass

        def get_or_create_collection(self, name=None):
            return collection

    fake_chromadb = types.ModuleType("chromadb")
    fake_chromadb.PersistentClient = _FakePersistentClient
    fake_config = types.ModuleType("chromadb.config")
    fake_config.Settings = _FakeSettings
    fake_chromadb.config = fake_config

    old_chromadb = sys.modules.get("chromadb")
    old_config = sys.modules.get("chromadb.config")
    sys.modules["chromadb"] = fake_chromadb
    sys.modules["chromadb.config"] = fake_config
    try:
        exec(compile(code, "weekly_run_step7", "exec"), {})
    finally:
        if old_chromadb is not None:
            sys.modules["chromadb"] = old_chromadb
        else:
            sys.modules.pop("chromadb", None)
        if old_config is not None:
            sys.modules["chromadb.config"] = old_config
        else:
            sys.modules.pop("chromadb.config", None)


class TestWeeklyRunDoiDedup(unittest.TestCase):
    def test_different_documents_sharing_a_doi_are_not_merged(self):
        col = _FakeCollection([
            ("docA_chunk_0", {"doc_id": "docA", "doi": "10.1234/shared"}),
            ("docA_chunk_1", {"doc_id": "docA", "doi": "10.1234/shared"}),
            ("docB_chunk_0", {"doc_id": "docB", "doi": "10.1234/shared"}),
        ])

        _run_step7_against(col)

        remaining = col.remaining_ids()
        # docA's own duplicate collapses to one chunk, but docB (a distinct
        # document that happens to share the doi value) must be untouched.
        self.assertIn("docB_chunk_0", remaining)
        doc_a_remaining = [i for i in remaining if i.startswith("docA_chunk_")]
        self.assertEqual(len(doc_a_remaining), 1)

    def test_records_without_doi_are_left_alone(self):
        col = _FakeCollection([
            ("docC_chunk_0", {"doc_id": "docC", "doi": ""}),
            ("docC_chunk_1", {"doc_id": "docC", "doi": "N/A"}),
            ("docC_chunk_2", {"doc_id": "docC"}),
        ])

        _run_step7_against(col)

        self.assertEqual(sorted(col.remaining_ids()), ["docC_chunk_0", "docC_chunk_1", "docC_chunk_2"])


if __name__ == "__main__":
    unittest.main()
