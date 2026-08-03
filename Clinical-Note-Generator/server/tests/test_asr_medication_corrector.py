"""Unit tests for ASR Medication Corrector (the only medication-correction layer in production)."""
import sys
import tempfile
import unittest
from pathlib import Path

# Add server directory to path
SERVER_DIR = Path(__file__).resolve().parents[1]
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

from services.asr_medication_corrector import (
    ASRMeditationCorrector,
    correct_asr_medication_errors,
    get_corrector,
)


class TestASRMeditationCorrector(unittest.TestCase):
    """Test the dictionary-based ASR medication corrector."""
    
    def setUp(self):
        """Create a test dictionary for each test."""
        self.test_dict = {
            "trilogy": {
                "correct": "Trelegy",
                "context": ["inhaler", "COPD", "puff", "respiratory", "asthma"]
            },
            "trilegy": {
                "correct": "Trelegy",
                "context": ["inhaler", "COPD", "puff", "respiratory", "asthma"]
            }
        }
        
        # Create a temporary config file
        self.temp_dir = tempfile.mkdtemp()
        self.config_path = Path(self.temp_dir) / "test_dict.json"
        with open(self.config_path, "w") as f:
            import json
            json.dump(self.test_dict, f)
        
        # Create corrector with test config
        self.corrector = ASRMeditationCorrector(config_path=self.config_path)
    
    def tearDown(self):
        """Clean up temporary files."""
        import shutil
        shutil.rmtree(self.temp_dir)
    
    def test_correct_with_context(self):
        """Test correction when context keywords are present."""
        text = "Patient is on trilogy inhaler for COPD"
        corrected, count = self.corrector.correct(text)
        
        self.assertEqual(count, 1)
        self.assertIn("Trelegy", corrected)
        self.assertNotIn("trilogy", corrected.lower())
    
    def test_correct_without_context(self):
        """Test that correction does NOT happen without context."""
        text = "The trilogy of events was significant"
        corrected, count = self.corrector.correct(text)
        
        self.assertEqual(count, 0)
        self.assertIn("trilogy", corrected.lower())
        self.assertNotIn("Trelegy", corrected)
    
    def test_correct_case_insensitive(self):
        """Test case-insensitive matching."""
        text = "Patient is on Trilogy inhaler"
        corrected, count = self.corrector.correct(text)
        
        self.assertEqual(count, 1)
        self.assertIn("Trelegy", corrected)
    
    def test_correct_multiple_occurrences(self):
        """Test multiple corrections in the same text."""
        text = "Patient is on trilogy inhaler. Also takes trilegy for COPD"
        corrected, count = self.corrector.correct(text)
        
        self.assertEqual(count, 2)
        self.assertIn("Trelegy", corrected)
        self.assertNotIn("trilogy", corrected.lower())
        self.assertNotIn("trilegy", corrected.lower())
    
    def test_correct_context_window(self):
        """Test that context window works correctly."""
        # Context keyword is within ±5 words
        text = "Patient is on trilogy inhaler for COPD maintenance therapy"
        corrected, count = self.corrector.correct(text)
        self.assertEqual(count, 1)
        
        # Context keyword is too far away (more than 5 words)
        text = "Patient is on trilogy. This is a long sentence with no context keywords nearby at all"
        corrected, count = self.corrector.correct(text)
        self.assertEqual(count, 0)
    
    def test_correct_empty_text(self):
        """Test empty text handling."""
        corrected, count = self.corrector.correct("")
        self.assertEqual(count, 0)
        self.assertEqual(corrected, "")
    
    def test_correct_none_text(self):
        """Test None text handling."""
        # The corrector handles None by returning it as-is
        corrected, count = self.corrector.correct(None)
        self.assertEqual(count, 0)
        # None is returned as-is since the corrector checks 'if not text'
    
    def test_correct_no_dictionary(self):
        """Test with empty dictionary."""
        # Create corrector with empty config
        empty_config = Path(self.temp_dir) / "empty.json"
        with open(empty_config, "w") as f:
            import json
            json.dump({}, f)
        
        empty_corrector = ASRMeditationCorrector(config_path=empty_config)
        corrected, count = empty_corrector.correct("Patient is on trilogy inhaler")
        self.assertEqual(count, 0)
        self.assertIn("trilogy", corrected.lower())
    
    def test_correct_preserves_text(self):
        """Test that non-matching text is preserved exactly."""
        text = "Patient takes metformin 500 mg daily and lisinopril 10 mg"
        corrected, count = self.corrector.correct(text)
        
        self.assertEqual(count, 0)
        self.assertEqual(corrected, text)
    
    def test_correct_context_keywords_case_insensitive(self):
        """Test that context keywords are case-insensitive."""
        text = "Patient is on trilogy Inhaler for COPD"
        corrected, count = self.corrector.correct(text)
        
        self.assertEqual(count, 1)
        self.assertIn("Trelegy", corrected)


class TestSingletonFunctions(unittest.TestCase):
    """Test the singleton functions."""
    
    def test_correct_asr_medication_errors(self):
        """Test the top-level correction function."""
        # This uses the singleton corrector, which may not have our test dict
        # Just verify it doesn't crash
        text = "Patient is on trilogy inhaler"
        corrected, count = correct_asr_medication_errors(text)
        self.assertIsInstance(corrected, str)
        self.assertIsInstance(count, int)
    
    def test_get_corrector(self):
        """Test getting the corrector instance."""
        corrector = get_corrector()
        self.assertIsNotNone(corrector)
        self.assertIsInstance(corrector, ASRMeditationCorrector)


if __name__ == "__main__":
    unittest.main()
