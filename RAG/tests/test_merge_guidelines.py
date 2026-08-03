"""Regression test: merge_guidelines() must keep the full-text-repaired copy
of a guideline, not whichever file happens to sort first alphabetically.

guidelines_gin.jsonl (no "text" field) and guidelines_gin_full.jsonl (same
titles, "text" populated by fetch_full_text.py) both match the
guidelines_*.jsonl glob. "guidelines_gin." sorts before "guidelines_gin_"
(ASCII '.' < '_'), so a first-title-wins dedup silently discards the
repaired record and keeps the original, abstract-only one -- the entire
full-text-repair pass has no effect on the merged corpus.
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from guideline_pipeline import merge_guidelines


class TestMergeGuidelines(unittest.TestCase):
    def test_full_text_copy_wins_over_earlier_sorting_original(self):
        with tempfile.TemporaryDirectory() as td:
            base = {"title": "Some Clinical Guideline", "source": "GIN"}
            full = {"title": "Some Clinical Guideline", "source": "GIN", "text": "A" * 500}

            with open(os.path.join(td, "guidelines_gin.jsonl"), "w", encoding="utf-8") as f:
                f.write(json.dumps(base) + "\n")
            with open(os.path.join(td, "guidelines_gin_full.jsonl"), "w", encoding="utf-8") as f:
                f.write(json.dumps(full) + "\n")

            merged = merge_guidelines(
                input_dir=td, output_file=os.path.join(td, "guidelines_merged.jsonl")
            )

            self.assertEqual(len(merged), 1)
            self.assertEqual(merged[0].get("text"), "A" * 500)

    def test_null_text_repair_attempt_does_not_overwrite_existing_content(self):
        with tempfile.TemporaryDirectory() as td:
            full = {"title": "Some Clinical Guideline", "source": "GIN", "text": "already has content"}
            failed_repair = {"title": "Some Clinical Guideline", "source": "GIN", "text": None}

            with open(os.path.join(td, "guidelines_aaa.jsonl"), "w", encoding="utf-8") as f:
                f.write(json.dumps(full) + "\n")
            with open(os.path.join(td, "guidelines_zzz.jsonl"), "w", encoding="utf-8") as f:
                f.write(json.dumps(failed_repair) + "\n")

            merged = merge_guidelines(
                input_dir=td, output_file=os.path.join(td, "guidelines_merged.jsonl")
            )

            self.assertEqual(len(merged), 1)
            self.assertEqual(merged[0].get("text"), "already has content")

    def test_distinct_titles_are_all_kept(self):
        with tempfile.TemporaryDirectory() as td:
            with open(os.path.join(td, "guidelines_a.jsonl"), "w", encoding="utf-8") as f:
                f.write(json.dumps({"title": "Guideline One", "source": "GIN"}) + "\n")
                f.write(json.dumps({"title": "Guideline Two", "source": "GIN"}) + "\n")

            merged = merge_guidelines(
                input_dir=td, output_file=os.path.join(td, "guidelines_merged.jsonl")
            )

            titles = {g["title"] for g in merged}
            self.assertEqual(titles, {"Guideline One", "Guideline Two"})


if __name__ == "__main__":
    unittest.main()
