# server/core/baseline.py
from typing import Any, Dict


def get_baseline_workspace() -> Dict[str, Any]:
    return {
        "settings": {
            "theme": "light",
            "language": "en",
        },
        "documents": [],
        "draft": None,
        "extras": {},
    }
