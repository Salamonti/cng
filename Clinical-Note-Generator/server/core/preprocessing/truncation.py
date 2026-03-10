from __future__ import annotations

import logging
import math
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from .constants import DATE_PATTERNS, MEDICAL_TERMS

logger = logging.getLogger("cng.preprocessing.truncation")


class TokenBudgetTruncator:
    def __init__(self, cfg: Optional[Dict] = None):
        preprocessing = (cfg or {}).get("preprocessing") or {}
        trunc = preprocessing.get("truncation") or {}
        self.budgets = {
            "prior_visits": int(trunc.get("prior_visits_budget_tokens", 1024)),
            "labs_imaging_other": int(trunc.get("labs_imaging_other_budget_tokens", 1024)),
            "current_encounter": int(trunc.get("current_encounter_budget_tokens", 4096)),
        }

    @staticmethod
    def estimate_tokens(text: str) -> int:
        words = re.findall(r"\S+", text or "")
        return int(math.ceil(len(words) * 1.3))

    @staticmethod
    def _debug_enabled() -> bool:
        return os.environ.get("CNG_TRUNCATION_DEBUG", "0").strip().lower() in {"1", "true", "yes", "on"}

    def truncate_section(self, text: str, section: str) -> str:
        if not text:
            return ""

        key = (section or "").strip().lower()
        budget = self.budgets.get(key)
        if budget is None or budget <= 0:
            return text

        original_tokens = self.estimate_tokens(text)
        debug = self._debug_enabled()

        if original_tokens <= budget:
            if debug:
                logger.info(
                    "[TRUNC DEBUG] section=%s | tokens=%d <= budget=%d | NO TRUNCATION",
                    key, original_tokens, budget,
                )
            return text

        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
        if not paragraphs:
            return text

        scored: List[Tuple[int, int, str]] = []
        for idx, para in enumerate(paragraphs):
            scored.append((self._score_paragraph(para), idx, para))

        if debug:
            logger.info(
                "[TRUNC DEBUG] section=%s | tokens=%d > budget=%d | %d paragraphs to score",
                key, original_tokens, budget, len(paragraphs),
            )

        selected: List[Tuple[int, str]] = []
        dropped: List[Dict[str, Any]] = []
        tokens_used = 0
        for score, idx, para in sorted(scored, key=lambda x: x[0], reverse=True):
            para_tokens = self.estimate_tokens(para)
            preview = para[:80].replace("\n", " ")
            if tokens_used + para_tokens <= budget:
                selected.append((idx, para))
                tokens_used += para_tokens
                if debug:
                    logger.info(
                        "[TRUNC DEBUG]   KEPT  idx=%d score=%d tokens=%d preview='%s'",
                        idx, score, para_tokens, preview,
                    )
            else:
                dropped.append({"idx": idx, "score": score, "tokens": para_tokens, "preview": preview})
                if debug:
                    logger.info(
                        "[TRUNC DEBUG]   DROP  idx=%d score=%d tokens=%d preview='%s'",
                        idx, score, para_tokens, preview,
                    )

        if not selected:
            best = max(scored, key=lambda x: x[0])[2]
            return self._clip_text_to_budget(best, budget)

        selected.sort(key=lambda x: x[0])
        truncated = "\n\n".join(para for _, para in selected)

        removed_ratio = 1.0 - (len(truncated) / max(1, len(text)))
        if removed_ratio > 0.8 and original_tokens < (budget * 2):
            if debug:
                logger.info(
                    "[TRUNC DEBUG]   SAFETY OVERRIDE: removed_ratio=%.2f, returning original",
                    removed_ratio,
                )
            return text

        if debug:
            after_tokens = self.estimate_tokens(truncated)
            logger.info(
                "[TRUNC DEBUG] section=%s | RESULT: %d→%d tokens (%.1f%% reduction) | kept=%d dropped=%d",
                key, original_tokens, after_tokens,
                (1 - after_tokens / max(1, original_tokens)) * 100,
                len(selected), len(dropped),
            )

        return truncated

    def _clip_text_to_budget(self, text: str, budget: int) -> str:
        if self.estimate_tokens(text) <= budget:
            return text
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        if not lines:
            return text
        picked: List[str] = []
        used = 0
        for line in lines:
            line_tokens = self.estimate_tokens(line)
            if used + line_tokens > budget:
                break
            picked.append(line)
            used += line_tokens
        return "\n".join(picked).strip() or lines[0]

    def _score_paragraph(self, para: str) -> int:
        latest = self._latest_date_ordinal(para)
        has_date = 1 if latest > 0 else 0
        numeric_hits = len(re.findall(r"\b\d+(?:\.\d+)?\b", para))
        units_hits = len(re.findall(r"\b(?:mg|mcg|g|kg|mL|ml|L|mmHg|bpm|%|IU|U/L|mmol/L|g/dL)\b", para, re.IGNORECASE))
        med_term_hits = sum(1 for tok in re.findall(r"\b[\w/]+\b", para.lower()) if tok in MEDICAL_TERMS)

        base = (has_date * 1_000_000_000) + latest
        signal = numeric_hits * 15 + units_hits * 25 + med_term_hits * 10

        low_info_penalty = 0
        words = re.findall(r"\b\w+\b", para)
        if len(words) < 6 and numeric_hits == 0 and med_term_hits == 0:
            low_info_penalty = 80

        return base + signal - low_info_penalty

    def _latest_date_ordinal(self, text: str) -> int:
        latest: Optional[datetime] = None

        for match in DATE_PATTERNS["ymd"].finditer(text):
            y, m, d = map(int, match.groups())
            latest = self._max_date(latest, y, m, d)

        for match in DATE_PATTERNS["mdy"].finditer(text):
            m, d, y = map(int, match.groups())
            latest = self._max_date(latest, y, m, d)

        month_map = {
            "jan": 1,
            "feb": 2,
            "mar": 3,
            "apr": 4,
            "may": 5,
            "jun": 6,
            "jul": 7,
            "aug": 8,
            "sep": 9,
            "oct": 10,
            "nov": 11,
            "dec": 12,
        }
        for match in DATE_PATTERNS["dmy_mon"].finditer(text):
            d = int(match.group(1))
            m = month_map[match.group(2).lower()[:3]]
            y = int(match.group(3))
            latest = self._max_date(latest, y, m, d)

        # Month-year (no day): treat as the 1st of the month for recency scoring.
        for match in DATE_PATTERNS["mon_y"].finditer(text):
            m = month_map[match.group(1).lower()[:3]]
            y = int(match.group(2))
            latest = self._max_date(latest, y, m, 1)

        return latest.toordinal() if latest else 0

    @staticmethod
    def _max_date(current: Optional[datetime], year: int, month: int, day: int) -> Optional[datetime]:
        try:
            cand = datetime(year=year, month=month, day=day)
        except ValueError:
            return current
        if current is None or cand > current:
            return cand
        return current
