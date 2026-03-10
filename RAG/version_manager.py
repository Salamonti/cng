"""
version_manager.py

Versioning and staleness manager for the RAG medical corpus.

What it does
- Compares new cleaned docs to the current corpus using content hash and title similarity.
- Archives outdated versions into ./archive/.
- Deletes docs older than 5 years unless flagged as foundational.
- Maintains a persistent index at ./version_index.json.
- Prints a human-readable summary report.

Typical usage
    python version_manager.py --input clean_corpus/clean_YYYYMMDD_HHMMSS.json

Options
    --simulate               : Dry-run; print what would happen without changing files
    --foundational-field str : Metadata field that, when True, exempts a doc from age-based deletion (default: foundational)
    --similarity float       : Title similarity threshold for matching when id is missing (default: 0.97)
    --age-years int          : Delete threshold in years for non-foundational docs (default: 5)
    --max-updates int        : Cap the number of updates applied this run (0 = unlimited)

Input format expected
    A JSON array of records, each like:
      {
        "title": str,
        "source": str,
        "id": str | null,
        "date": str,  # e.g., YYYY-MM-DD or similar
        "text": str,
        ... optional fields ...,
        "foundational": bool   # optional; if True, never deleted for age
      }

Persistent layout
    ./current_corpus/            : one JSON file per current doc (keyed by stable key)
    ./archive/                   : timestamped JSON files for archived prior versions
    ./version_index.json         : registry mapping keys to metadata (hash, date, paths, etc.)

Key computation
    key = (source, id) if id
          else (source, normalized_title)  # normalized alnum/space lowercased

Similarity
    When id is missing, match against existing titles within the same source using difflib.SequenceMatcher; threshold configurable.

"""
from __future__ import annotations
import argparse
import datetime as dt
import hashlib
import json
import re
from dataclasses import dataclass, asdict
from difflib import SequenceMatcher
from pathlib import Path
from typing import Dict, Any, Tuple, Optional, List

CURRENT_DIR = Path("./current_corpus")
ARCHIVE_DIR = Path("./archive")
INDEX_PATH = Path("./version_index.json")

CURRENT_DIR.mkdir(parents=True, exist_ok=True)
ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)

# ---------------
# Utilities
# ---------------

def normalize_title(title: str) -> str:
    t = (title or "").lower()
    t = re.sub(r"[^a-z0-9]+", " ", t).strip()
    return t


def content_hash(item: Dict[str, Any]) -> str:
    # Hash core fields; adjust as needed
    core = "\n".join([
        str(item.get("title", "")),
        str(item.get("text", "")),
        str(item.get("date", "")),
        str(item.get("journal", "")),
    ])
    return hashlib.sha1(core.encode("utf-8", errors="ignore")).hexdigest()


def parse_date_any(s: str) -> dt.datetime:
    if not s:
        return dt.datetime(1970, 1, 1)
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d", "%Y-%m", "%Y"):
        try:
            if fmt == "%Y":
                return dt.datetime(int(s), 1, 1)
            return dt.datetime.strptime(s, fmt)
        except Exception:
            continue
    # strip non-digits
    digits = re.sub(r"[^0-9]", "", s)
    if len(digits) >= 8:
        try:
            return dt.datetime.strptime(digits[:8], "%Y%m%d")
        except Exception:
            pass
    return dt.datetime(1970, 1, 1)


def make_key(source: str, id_: Optional[str], title: str) -> Tuple[str, str]:
    src = (source or "").lower()
    if id_:
        return (src, str(id_))
    return (src, normalize_title(title))


# ---------------
# Index model
# ---------------

@dataclass
class IndexEntry:
    key_src: str
    key_id: str
    title: str
    date: str
    hash: str
    path: str             # path to current JSON file
    foundational: bool

    @property
    def key(self) -> Tuple[str, str]:
        return (self.key_src, self.key_id)


def load_index() -> Dict[str, IndexEntry]:
    if not INDEX_PATH.exists():
        return {}
    raw = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    out: Dict[str, IndexEntry] = {}
    for k, v in raw.items():
        out[k] = IndexEntry(**v)
    return out


def save_index(idx: Dict[str, IndexEntry]) -> None:
    data = {k: asdict(v) for k, v in idx.items()}
    INDEX_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def key_to_filename(key: Tuple[str, str]) -> str:
    src, kid = key
    safe = re.sub(r"[^a-zA-Z0-9_.-]", "_", f"{src}__{kid}")
    return f"{safe}.json"


# ---------------
# Core logic
# ---------------

def add_or_update_doc(item: Dict[str, Any], idx: Dict[str, IndexEntry], similarity: float, simulate: bool) -> str:
    """Return action: 'added' | 'updated' | 'unchanged'"""
    src = str(item.get("source", "")).lower()
    iid = item.get("id")
    title = str(item.get("title", ""))
    date = str(item.get("date", ""))
    h = content_hash(item)
    key = make_key(src, iid, title)
    kstr = f"{key[0]}|{key[1]}"

    # If no exact-key entry and id is missing, try to find a near-duplicate title within same source
    if kstr not in idx and not iid:
        for ok, ent in idx.items():
            if ent.key_src != src:
                continue
            if SequenceMatcher(None, normalize_title(title), normalize_title(ent.title)).ratio() >= similarity:
                # treat as same; use that entry's key
                key = (ent.key_src, ent.key_id)
                kstr = ok
                break

    existing = idx.get(kstr)
    if existing is None:
        # Add new
        out_path = CURRENT_DIR / key_to_filename(key)
        if not simulate:
            out_path.write_text(json.dumps(item, ensure_ascii=False, indent=2), encoding="utf-8")
        idx[kstr] = IndexEntry(key_src=key[0], key_id=key[1], title=title, date=date, hash=h, path=str(out_path), foundational=bool(item.get("foundational", False)))
        return "added"

    # Existing present: compare hash
    if existing.hash == h:
        return "unchanged"

    # Different: archive the old file, write the new one
    if not simulate:
        # Archive old
        old_path = Path(existing.path)
        if old_path.exists():
            stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
            arch_name = old_path.stem + f"__arch_{stamp}.json"
            arch_path = ARCHIVE_DIR / arch_name
            try:
                arch_path.write_text(old_path.read_text(encoding="utf-8"), encoding="utf-8")
            except Exception:
                pass
        # Write new current
        out_path = CURRENT_DIR / key_to_filename(key)
        out_path.write_text(json.dumps(item, ensure_ascii=False, indent=2), encoding="utf-8")

    # Update index metadata
    idx[kstr] = IndexEntry(key_src=key[0], key_id=key[1], title=title, date=date, hash=h, path=str(CURRENT_DIR / key_to_filename(key)), foundational=bool(item.get("foundational", False)))
    return "updated"


def delete_old_docs(idx: Dict[str, IndexEntry], age_years: int, foundational_field: str, simulate: bool) -> List[str]:
    """Delete docs older than threshold unless foundational; return list of keys deleted."""
    now = dt.datetime.now()
    deleted: List[str] = []
    for k, ent in list(idx.items()):
        # read current file to inspect potential foundational flag if present
        is_foundational = ent.foundational
        try:
            p = Path(ent.path)
            if p.exists():
                data = json.loads(p.read_text(encoding="utf-8"))
                is_foundational = bool(data.get(foundational_field, is_foundational))
        except Exception:
            pass

        if is_foundational:
            continue

        d = parse_date_any(ent.date)
        years = (now - d).days / 365.25
        if years >= age_years:
            if not simulate:
                # Permanently delete the current file
                try:
                    p = Path(ent.path)
                    if p.exists():
                        p.unlink()
                except Exception:
                    pass
            deleted.append(k)
            idx.pop(k, None)
    return deleted


# ---------------
# Main
# ---------------

def main():
    ap = argparse.ArgumentParser(description="Manage versions and staleness of the medical corpus")
    ap.add_argument("--input", required=True, help="Path to cleaned JSON file (array of records)")
    ap.add_argument("--simulate", action="store_true", help="Dry-run; do not modify files")
    ap.add_argument("--foundational-field", default="foundational", help="Field name that flags foundational docs")
    ap.add_argument("--similarity", type=float, default=0.97, help="Title similarity threshold when id is missing")
    ap.add_argument("--age-years", type=int, default=5, help="Delete docs older than this unless foundational")
    ap.add_argument("--max-updates", type=int, default=0, help="Cap number of add/update operations this run (0 = unlimited)")
    args = ap.parse_args()

    # Load new docs
    in_path = Path(args.input)
    if not in_path.exists():
        raise SystemExit(f"Input file not found: {in_path}")
    try:
        new_docs: List[Dict[str, Any]] = json.loads(in_path.read_text(encoding="utf-8"))
    except Exception as e:
        raise SystemExit(f"Failed to parse JSON: {e}")

    # Load index
    idx = load_index()

    # Apply new docs
    added = updated = unchanged = 0
    applied = 0
    for doc in new_docs:
        action = add_or_update_doc(doc, idx, similarity=args.similarity, simulate=args.simulate)
        if action == "added":
            added += 1
            applied += 1
        elif action == "updated":
            updated += 1
            applied += 1
        else:
            unchanged += 1

        if args.max_updates and applied >= args.max_updates:
            break

    # Delete old docs by age
    deleted_keys = delete_old_docs(idx, age_years=args.age_years, foundational_field=args.foundational_field, simulate=args.simulate)

    # Save index
    if not args.simulate:
        save_index(idx)

    # Report
    report = {
        "input": str(in_path),
        "simulate": args.simulate,
        "added": added,
        "updated": updated,
        "unchanged": unchanged,
        "deleted_by_age": len(deleted_keys),
        "current_total": len(idx),
        "index_path": str(INDEX_PATH),
        "current_dir": str(CURRENT_DIR),
        "archive_dir": str(ARCHIVE_DIR),
    }
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
