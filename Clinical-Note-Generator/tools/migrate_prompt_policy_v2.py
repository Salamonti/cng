"""Atomically migrate DreamCision's built-in note prompts to policy v2."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from server.core.prompt.policy_v2 import (
    OTHER_NOTE_PROMPTS,
    PROMPT_POLICY_VERSION,
    STANDARD_NOTE_PROMPTS,
    UNIVERSAL_NOTE_SYSTEM_PROMPT,
)


def _merge_builtins(existing: object, builtins: Dict[str, str]) -> Dict[str, Any]:
    merged = dict(existing) if isinstance(existing, dict) else {}
    merged.update(builtins)
    return merged


def migrate(config: Dict[str, Any]) -> Dict[str, Any]:
    updated = dict(config)
    updated["prompt_policy_version"] = PROMPT_POLICY_VERSION
    updated["default_note_system_prompt"] = UNIVERSAL_NOTE_SYSTEM_PROMPT.strip()
    updated.pop("default_note_system_prompt_other", None)
    updated["default_note_user_prompts"] = _merge_builtins(
        updated.get("default_note_user_prompts"), STANDARD_NOTE_PROMPTS
    )
    updated["default_note_user_prompts_other"] = _merge_builtins(
        updated.get("default_note_user_prompts_other"), OTHER_NOTE_PROMPTS
    )

    updated["default_note_max_tokens"] = 2048
    updated["default_note_temperature"] = 0.0
    updated["default_repeat_penalty"] = 1.0
    updated["default_frequency_penalty"] = 0.0
    updated["default_presence_penalty"] = 0.0
    updated["default_top_p"] = 0.92
    updated["default_top_k"] = 20
    updated["default_min_p"] = 0.06
    updated["note_generation_validate_before_stream"] = True
    updated["note_generation_retry_on_guard"] = True
    return updated


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "config" / "config.json",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    config_path = args.config.resolve()
    current = json.loads(config_path.read_text(encoding="utf-8"))
    updated = migrate(current)
    if args.dry_run:
        print(json.dumps(updated, indent=2, ensure_ascii=True))
        return 0

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    backup_path = config_path.with_name(f"{config_path.name}.pre-prompt-v2-{timestamp}")
    shutil.copy2(config_path, backup_path)

    temp_path = config_path.with_name(f".{config_path.name}.prompt-v2.tmp")
    temp_path.write_text(
        json.dumps(updated, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    os.replace(temp_path, config_path)
    print(f"Migrated {config_path}")
    print(f"Backup: {backup_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
