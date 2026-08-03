"""
ASR Medication Corrector — Layer 1 of hybrid medication correction.

Curated dictionary of known ASR transcription errors with context guards.
Runs programmatically on raw ASR transcripts before the LLM sees them.
Zero hallucination risk — each correction is a hard-coded, auditable mapping.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Dict, List, Tuple

logger = logging.getLogger(__name__)

# Context window: number of words before/after the matched word to check for context keywords
CONTEXT_WINDOW = 5

# Default config path
_CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "asr_medication_dictionary.json"


class ASRMeditationCorrector:
    """
    Corrects known ASR medication transcription errors using a curated dictionary.
    
    Each entry maps a wrong word to the correct word, with context keywords
    that must be present within a window to trigger the correction.
    """
    
    def __init__(self, config_path: Path | None = None) -> None:
        self._config_path = config_path or _CONFIG_PATH
        self._dictionary: Dict[str, Dict] = {}
        self._load()
    
    def _load(self) -> None:
        """Load the dictionary from JSON config file."""
        if not self._config_path.exists():
            logger.warning("ASR medication dictionary not found: %s", self._config_path)
            self._dictionary = {}
            return
        
        try:
            with open(self._config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            # Filter out metadata keys (start with _)
            self._dictionary = {
                k.lower(): v for k, v in data.items()
                if not k.startswith("_") and isinstance(v, dict)
            }
            
            logger.info("Loaded %d ASR medication correction entries from %s",
                       len(self._dictionary), self._config_path)
        except Exception as e:
            logger.error("Failed to load ASR medication dictionary: %s", e)
            self._dictionary = {}
    
    def reload(self) -> None:
        """Reload the dictionary from disk (for hot updates)."""
        self._load()
    
    def correct(self, text: str) -> Tuple[str, int]:
        """
        Correct ASR medication errors in the given text.
        
        Args:
            text: Raw ASR transcript text
            
        Returns:
            Tuple of (corrected_text, correction_count)
        """
        if not text or not self._dictionary:
            return text or "", 0
        
        corrections = 0
        result = text
        
        # Process each dictionary entry
        for wrong_word, entry in self._dictionary.items():
            correct_word = entry.get("correct", "")
            context_keywords = entry.get("context", [])
            
            if not correct_word or not context_keywords:
                continue
            
            # Find all occurrences of the wrong word (case-insensitive)
            pattern = re.compile(r'\b' + re.escape(wrong_word) + r'\b', re.IGNORECASE)
            matches = list(pattern.finditer(result))
            
            for match in reversed(matches):  # Reverse to preserve positions
                start, end = match.start(), match.end()
                
                # Extract context window (±CONTEXT_WINDOW words)
                context = self._get_context(result, start, end, CONTEXT_WINDOW)
                
                # Check if any context keyword is present
                if self._has_context(context, context_keywords):
                    # Apply correction
                    result = result[:start] + correct_word + result[end:]
                    corrections += 1
                    logger.debug(
                        "Corrected '%s' -> '%s' (context: %s)",
                        wrong_word, correct_word, context[:100]
                    )
        
        return result, corrections
    
    def _get_context(self, text: str, start: int, end: int, window: int) -> str:
        """Extract context window around the matched word."""
        # Find word boundaries
        words = text.split()
        word_positions = []
        pos = 0
        for word in words:
            idx = text.find(word, pos)
            if idx != -1:
                word_positions.append((idx, idx + len(word)))
                pos = idx + len(word)
        
        # Find the matched word's index
        matched_idx = None
        for i, (ws, we) in enumerate(word_positions):
            if ws <= start and we >= end:
                matched_idx = i
                break
        
        if matched_idx is None:
            return ""
        
        # Extract context window
        start_idx = max(0, matched_idx - window)
        end_idx = min(len(words), matched_idx + window + 1)
        
        return " ".join(words[start_idx:end_idx])
    
    def _has_context(self, context: str, keywords: List[str]) -> bool:
        """Check if any context keyword is present in the context window."""
        context_lower = context.lower()
        return any(kw.lower() in context_lower for kw in keywords)


# Singleton instance
_corrector: ASRMeditationCorrector = ASRMeditationCorrector()


def correct_asr_medication_errors(text: str) -> Tuple[str, int]:
    """
    Correct ASR medication errors using the curated dictionary.
    
    Args:
        text: Raw ASR transcript text
        
    Returns:
        Tuple of (corrected_text, correction_count)
    """
    return _corrector.correct(text)


def get_corrector() -> ASRMeditationCorrector:
    """Get the singleton corrector instance (for testing/reloading)."""
    return _corrector


def reload_dictionary() -> None:
    """Reload the dictionary from disk."""
    _corrector.reload()
