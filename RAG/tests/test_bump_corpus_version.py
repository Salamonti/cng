"""P2-5 regression: corpus_version must actually get written after a weekly
rebuild, and settings.yaml's comments/formatting must survive the edit --
retriever.py and query_api.py key their result caches on this value, and
before this fix nothing in the whole pipeline ever wrote it, so those
caches never invalidated on a real corpus update.
"""
import importlib.util
import sys
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "bump_corpus_version", Path(__file__).resolve().parents[1] / "bump_corpus_version.py"
)
_MODULE = importlib.util.module_from_spec(_SPEC)


def _run_bump(settings_path: Path) -> int:
    # Reload against the given settings.yaml path each time (module-level
    # SETTINGS_PATH is resolved once at import, so point it at our tmp file).
    _MODULE.SETTINGS_PATH = settings_path
    return _MODULE.main()


def _load_module():
    sys.modules["bump_corpus_version"] = _MODULE
    _SPEC.loader.exec_module(_MODULE)
    return _MODULE


def test_bump_increments_version_and_preserves_everything_else(tmp_path):
    settings = tmp_path / "settings.yaml"
    settings.write_text(
        "# a comment that must survive\n"
        "persist_directory: \"./chroma_store\"\n"
        "\n"
        "corpus_version: 1\n"
        "chunk_size_chars: 1800  # inline comment\n",
        encoding="utf-8",
    )
    mod = _load_module()
    exit_code = _run_bump(settings)

    assert exit_code == 0
    new_text = settings.read_text(encoding="utf-8")
    assert "corpus_version: 2" in new_text
    assert "# a comment that must survive" in new_text
    assert "chunk_size_chars: 1800  # inline comment" in new_text
    assert new_text.count("\n") == 5  # unchanged line count


def test_bump_errors_cleanly_when_key_missing(tmp_path):
    settings = tmp_path / "settings.yaml"
    settings.write_text("persist_directory: \"./chroma_store\"\n", encoding="utf-8")
    mod = _load_module()
    exit_code = _run_bump(settings)

    assert exit_code == 1
