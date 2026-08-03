"""P3-3 regression: run_fetch() used to fetch all four sources inline
inside one dict literal under a single try/except -- one source's
transient failure raised before the dict literal finished evaluating, so
save_batch() never ran for ANY source, discarding every other source's
already-successful results for the whole run.
"""
from __future__ import annotations

from unittest.mock import patch

import fetch_sources


def test_one_source_failing_does_not_discard_the_others(monkeypatch):
    monkeypatch.setattr(fetch_sources, "get_config", lambda: {"domains": ["cardiology"]})
    monkeypatch.setattr(fetch_sources, "fetch_pubmed", lambda days, domains, cfg: [{"id": "p1"}, {"id": "p2"}])
    monkeypatch.setattr(fetch_sources, "fetch_clinicaltrials", lambda days, domains, cfg: (_ for _ in ()).throw(RuntimeError("timeout")))
    monkeypatch.setattr(fetch_sources, "fetch_openfda", lambda days, domains, cfg: [{"id": "f1"}])
    monkeypatch.setattr(fetch_sources, "fetch_drugbank", lambda days, domains, cfg: [])
    monkeypatch.setattr(fetch_sources, "save_batch", lambda sid, items: f"/fake/{sid}.json" if items else None)
    monkeypatch.setattr(fetch_sources, "append_log", lambda entry: None)

    results = fetch_sources.run_fetch(["cardiology"], days=7)

    assert results["status"] == "partial_error"
    by_source = {b["source"]: b for b in results["batches"]}
    # The failing source is recorded with its error, not silently dropped.
    assert by_source["clinicaltrials"]["count"] == 0
    assert "error" in by_source["clinicaltrials"]
    # The other three sources' results were NOT discarded by that failure.
    assert by_source["pubmed"]["count"] == 2
    assert by_source["openfda"]["count"] == 1
    assert by_source["drugbank"]["count"] == 0


def test_all_sources_succeeding_reports_ok(monkeypatch):
    monkeypatch.setattr(fetch_sources, "get_config", lambda: {"domains": ["cardiology"]})
    monkeypatch.setattr(fetch_sources, "fetch_pubmed", lambda days, domains, cfg: [{"id": "p1"}])
    monkeypatch.setattr(fetch_sources, "fetch_clinicaltrials", lambda days, domains, cfg: [])
    monkeypatch.setattr(fetch_sources, "fetch_openfda", lambda days, domains, cfg: [])
    monkeypatch.setattr(fetch_sources, "fetch_drugbank", lambda days, domains, cfg: [])
    monkeypatch.setattr(fetch_sources, "save_batch", lambda sid, items: None)
    monkeypatch.setattr(fetch_sources, "append_log", lambda entry: None)

    results = fetch_sources.run_fetch(["cardiology"], days=7)

    assert results["status"] == "ok"
    assert len(results["batches"]) == 4
