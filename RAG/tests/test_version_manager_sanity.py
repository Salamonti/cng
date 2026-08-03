"""P3-3 regression: add_or_update_doc() used to accept ANY content-hash
change as a valid update -- a paywall stub, an error page, or a
truncated/empty re-scrape all hash differently from real guideline text
and would silently replace a good, previously-fetched guideline.
"""
from __future__ import annotations

from version_manager import IndexEntry, add_or_update_doc, content_hash


def _make_idx_entry(item, path="/fake/current/doc.json"):
    return IndexEntry(
        key_src=str(item.get("source", "")).lower(),
        key_id=str(item.get("id", "")),
        title=item.get("title", ""),
        date=item.get("date", ""),
        hash=content_hash(item),
        path=path,
        foundational=False,
    )


def test_genuine_content_update_is_accepted():
    original = {"source": "WHO", "id": "123", "title": "T", "text": "x" * 2000, "date": "2023-01-01"}
    idx = {"who|123": _make_idx_entry(original)}

    updated_item = dict(original, text="y" * 2500)  # different, but still substantial
    action = add_or_update_doc(updated_item, idx, similarity=0.9, simulate=True)

    assert action == "updated"


def test_paywall_stub_replacement_is_rejected():
    original = {"source": "WHO", "id": "123", "title": "T", "text": "x" * 2000, "date": "2023-01-01"}
    idx = {"who|123": _make_idx_entry(original)}

    stub_item = dict(original, text="Please subscribe to view this content.")
    action = add_or_update_doc(stub_item, idx, similarity=0.9, simulate=True)

    assert action == "rejected_low_quality"
    # The index must still reflect the ORIGINAL good content, not the stub.
    assert idx["who|123"].hash == content_hash(original)


def test_empty_text_replacement_is_rejected():
    original = {"source": "WHO", "id": "123", "title": "T", "text": "x" * 2000, "date": "2023-01-01"}
    idx = {"who|123": _make_idx_entry(original)}

    empty_item = dict(original, text="")
    action = add_or_update_doc(empty_item, idx, similarity=0.9, simulate=True)

    assert action == "rejected_low_quality"


def test_new_document_with_short_text_is_still_added():
    # The sanity floor only guards REPLACING existing good content -- a
    # genuinely new document (no prior version to protect) should still be
    # added even if short, matching the pre-existing "added" path.
    idx = {}
    short_item = {"source": "WHO", "id": "999", "title": "New", "text": "short", "date": "2024-01-01"}
    action = add_or_update_doc(short_item, idx, similarity=0.9, simulate=True)
    assert action == "added"
