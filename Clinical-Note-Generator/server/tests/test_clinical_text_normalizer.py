import os
import sys
import tempfile
import unittest
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parents[1]
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

from services import clinical_text_normalizer as ctn


class TestClinicalTextNormalizer(unittest.TestCase):
    def setUp(self):
        self.old_terms = os.environ.get("RXNORM_TERMS_FILE")
        self.old_dir = os.environ.get("RXNORM_DIR")
        # reset singleton cache
        ctn._RXNORM._loaded = False
        ctn._RXNORM._terms = []

    def tearDown(self):
        if self.old_terms is None:
            os.environ.pop("RXNORM_TERMS_FILE", None)
        else:
            os.environ["RXNORM_TERMS_FILE"] = self.old_terms
        if self.old_dir is None:
            os.environ.pop("RXNORM_DIR", None)
        else:
            os.environ["RXNORM_DIR"] = self.old_dir
        ctn._RXNORM._loaded = False
        ctn._RXNORM._terms = []

    def test_numeric_unit_normalization(self):
        text = "Start prednisone five milligrams daily and vitamin D one hundred micrograms daily."
        out, count = ctn.normalize_numeric_units(text)
        self.assertIn("5 mg", out)
        self.assertIn("100 mcg", out)
        self.assertGreaterEqual(count, 2)

    def test_spacing_and_unit_case_normalization(self):
        text = "Insulin 10units nightly; saline 250ml bolus"
        out, _count = ctn.normalize_numeric_units(text)
        self.assertIn("10 units", out)
        self.assertIn("250 mL", out)

    def test_rxnorm_med_line_canonicalization(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "RXNCONSO.RRF")
            rows = [
                "|".join(["" for _ in range(11)] + ["RXNORM", "IN", "", "Metformin", ""]),
                "|".join(["" for _ in range(11)] + ["RXNORM", "IN", "", "Prednisone", ""]),
            ]
            with open(p, "w", encoding="utf-8") as f:
                f.write("\n".join(rows))

            os.environ["RXNORM_DIR"] = td
            os.environ.pop("RXNORM_TERMS_FILE", None)
            ctn._RXNORM._loaded = False
            ctn._RXNORM._terms = []

            text = "- metfornin 500 mg PO BID\n- Prednisone 5 mg PO daily"
            out, replacements = ctn.canonicalize_medication_lines(text, min_confidence=0.85)
            self.assertIn("Metformin 500 mg", out)
            self.assertIn("Prednisone 5 mg", out)
            self.assertGreaterEqual(replacements, 1)


if __name__ == "__main__":
    unittest.main()
