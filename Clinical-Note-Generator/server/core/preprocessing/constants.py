import re

DEFAULT_PREPROCESSING_CONFIG = {
    "enabled": False,
    "steps": {
        "remove_boilerplate": True,
        "collapse_repeated_headers": True,
        "remove_junk_artifacts": True,
        "deduplicate_blocks": True,
        "normalize_whitespace": True,
    },
    "truncation": {
        "prior_visits_budget_tokens": 1024,
        "labs_imaging_other_budget_tokens": 1024,
        "current_encounter_budget_tokens": 4096,
    },
}

BOILERPLATE_LINE_PATTERNS = [
    re.compile(r"^\s*(?:confidential|ast confidential|do not distribute)\b.*$", re.IGNORECASE),
    re.compile(r"^\s*(?:electronic health record|permanent record)\b.*$", re.IGNORECASE),
    re.compile(r"^\s*(?:generated|printed)\s*(?:on|at)?\b.*$", re.IGNORECASE),
    re.compile(r"^\s*other clinicians who have viewed this result.*$", re.IGNORECASE),
    re.compile(r"^\s*result name\s+results\s+units\s+reference range\s*$", re.IGNORECASE),
    re.compile(r"^\s*test performed at\b.*$", re.IGNORECASE),
    re.compile(r"^\s*transcribed by\b.*electronically signed by\b.*$", re.IGNORECASE),
    re.compile(r"^\s*patient:\s+.*\bmrn\b.*$", re.IGNORECASE),
    re.compile(r"^\s*do not write on this document\.?\s*print copy\s*$", re.IGNORECASE),
]

HEADER_CANDIDATE_PATTERNS = [
    re.compile(r"\b(?:page\s*\d+\s*(?:of|/)\s*\d+|faxcom|mrn|visit:)\b", re.IGNORECASE),
    re.compile(r"^\s*[A-Z][A-Z0-9\s,:/\-]{16,}\s*$"),
]

JUNK_LINE_PATTERNS = [
    re.compile(r"^\s*page\s*\d+\s*(?:of|/)\s*\d+\s*$", re.IGNORECASE),
    re.compile(r"^\s*\d+\s*(?:/|of)\s*\d+\s*$", re.IGNORECASE),
    re.compile(r"^\s*\d{1,2}:\d{2}(?::\d{2})?\s*(?:am|pm)?\s*$", re.IGNORECASE),
    re.compile(r"^\s*[\W_]{4,}\s*$"),
    re.compile(r"^\s*(?:\*{3,}|={3,}|-{3,}|_{3,})\s*$"),
]

DATE_STAMP_ONLY = re.compile(
    r"^\s*(?:\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}[-/]\d{1,2}[-/]\d{2,4})(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?(?:\s*(?:am|pm))?\s*$",
    re.IGNORECASE,
)

MULTISPACE_RE = re.compile(r"[ \t]{2,}")
MULTINEWLINE_RE = re.compile(r"\n{3,}")

MEDICAL_TERMS = {
    "mg",
    "mcg",
    "g",
    "kg",
    "ml",
    "l",
    "mmhg",
    "bpm",
    "spo2",
    "bp",
    "hr",
    "rr",
    "temp",
    "wbc",
    "hgb",
    "hba1c",
    "na",
    "k",
    "cl",
    "cr",
    "bun",
    "egfr",
    "ct",
    "mri",
    "xray",
    "ecg",
    "ekg",
}

DATE_PATTERNS = {
    "ymd": re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b"),
    "mdy": re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b"),
    "dmy_mon": re.compile(
        r"\b(\d{1,2})[- ](jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[- ](\d{4})\b",
        re.IGNORECASE,
    ),
    # Month-year without day (common in imaging/labs summaries): "Jan 2026", "Oct 2025"
    "mon_y": re.compile(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+(\d{4})\b", re.IGNORECASE),
}
