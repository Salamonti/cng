# summarize_recent_updates.py
import argparse
import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Any, List

import requests

RAG_ROOT = Path(r"C:\RAG")
FETCH_LOG = RAG_ROOT / "fetch_log.jsonl"
CACHE_PATH = RAG_ROOT / "recent_updates.json"
LLAMA_URL = "http://127.0.0.1:8081/completion"
MAX_DOCS_DEFAULT = 200
MAX_TEXT_CHARS = 2000
SUMMARY_MAX_TOKENS = 200
SUMMARY_TEMPERATURE = 0.2


class LLMError(Exception):
    pass


def looks_like_letter(title: str) -> bool:
    lower = title.strip().lower()
    if not lower:
        return False
    if "letter to the editor" in lower:
        return True
    if lower.startswith("letter to "):
        return True
    if lower.startswith("letter:"):
        return True
    if lower.startswith("reply to letter"):
        return True
    return False


def load_fetch_entries(path: Path) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    if not path.exists():
        return entries
    with path.open("r", encoding="utf-8") as f:
        for raw_line in f:
            raw_line = raw_line.strip()
            if not raw_line:
                continue
            try:
                entries.append(json.loads(raw_line))
            except json.JSONDecodeError:
                continue
    return entries


def choose_weekly_entry(entries: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not entries:
        raise RuntimeError("No fetch_log entries available")
    # Prefer entries with explicit days >= 7, else newest entry
    candidates = []
    for entry in entries:
        days = entry.get("days")
        if isinstance(days, int) and days >= 7:
            candidates.append(entry)
    if candidates:
        return sorted(candidates, key=lambda e: e.get("started", ""))[-1]
    return entries[-1]


def load_cache(cache_path: Path) -> Dict[str, Any]:
    if cache_path.exists():
        try:
            return json.loads(cache_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"generated_at": None, "run_started": None, "documents": []}


def save_cache(cache_path: Path, data: Dict[str, Any]) -> None:
    cache_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def flatten_doc_text(doc: Dict[str, Any]) -> str:
    preferred_keys = [
        "text",
        "abstract",
        "summary",
        "description",
        "content",
        "body",
        "sections",
        "details",
    ]
    parts: List[str] = []
    for key in preferred_keys:
        val = doc.get(key)
        if isinstance(val, str) and val.strip():
            parts.append(val.strip())
        elif isinstance(val, list):
            parts.extend([str(item) for item in val if isinstance(item, str)])
    if not parts:
        # Fallback to concatenating other string fields (excluding ids/links)
        for key, val in doc.items():
            if key in {"id", "link", "source", "title", "date"}:
                continue
            if isinstance(val, str) and val.strip():
                parts.append(val.strip())
    combined = "\n".join(parts)
    return combined[:MAX_TEXT_CHARS]


def build_prompt(title: str, content: str) -> str:
    instructions = (
        "You are a medical literature analyst. Write a concise two-sentence summary "
        "(max 80 words) highlighting the key findings, population, and clinical takeaway. "
        "If information is missing, note that explicitly."
    )
    return f"{instructions}\n\nTitle: {title}\n\nContent:\n{content}\n\nSummary:"


def call_llm(prompt: str) -> str:
    payload = {
        "prompt": prompt,
        "temperature": SUMMARY_TEMPERATURE,
        "n_predict": SUMMARY_MAX_TOKENS,
        "stop": ["\n\n", "Title:"],
        "stream": False,
        "trim_stop": True,
    }
    try:
        response = requests.post(LLAMA_URL, json=payload, timeout=60)
        response.raise_for_status()
        data = response.json()
    except Exception as exc:
        raise LLMError(str(exc)) from exc

    if isinstance(data, dict):
        if isinstance(data.get("content"), str):
            return data["content"].strip()
        choices = data.get("choices")
        if isinstance(choices, list) and choices:
            choice = choices[0]
            if isinstance(choice, dict):
                text = choice.get("text") or choice.get("content")
                if isinstance(text, str):
                    return text.strip()
    raise LLMError("Unexpected llama-server response")


def summarise_documents(entry: Dict[str, Any], cache: Dict[str, Any], max_docs: int) -> Dict[str, Any]:
    cached_docs = {doc.get("doc_key"): doc for doc in cache.get("documents", [])}
    summaries: List[Dict[str, Any]] = []
    processed = 0
    failures = 0

    for batch in entry.get("batches", []):
        file_path = batch.get("file")
        if not file_path:
            continue
        doc_path = (RAG_ROOT / file_path).resolve()
        if not doc_path.exists():
            print(f"Warning: missing file {doc_path}")
            continue
        docs: List[Dict[str, Any]]
        if doc_path.suffix.lower() == ".jsonl":
            docs = []
            with doc_path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        docs.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        else:
            try:
                docs = json.loads(doc_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                print(f"Warning: could not parse {doc_path}")
                continue

        for doc in docs:
            if processed >= max_docs:
                break
            title = str(doc.get("title") or "Untitled document").strip()
            if looks_like_letter(title):
                continue
            source = str(doc.get("source") or batch.get("source") or "unknown")
            doc_id = str(doc.get("id") or doc.get("link") or doc.get("title") or (processed + 1))
            link = doc.get("link")
            doc_key = f"{source}:{doc_id}"
            if doc_key in cached_docs and cached_docs[doc_key].get("summary"):
                summaries.append(cached_docs[doc_key])
                processed += 1
                continue
            content = flatten_doc_text(doc)
            prompt = build_prompt(title, content)
            try:
                summary_text = call_llm(prompt)
            except LLMError as exc:
                failures += 1
                summary_text = f"Summary unavailable ({exc})."
            summaries.append({
                "doc_key": doc_key,
                "source": source,
                "title": title,
                "link": link,
                "summary": summary_text,
            })
            processed += 1
        if processed >= max_docs:
            break
import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Any, List

import requests

RAG_ROOT = Path(r"C:\RAG")
FETCH_LOG = RAG_ROOT / "fetch_log.jsonl"
CACHE_PATH = RAG_ROOT / "recent_updates.json"
LLAMA_URL = "http://127.0.0.1:8081/completion"
MAX_DOCS_DEFAULT = 200
MAX_TEXT_CHARS = 2000
SUMMARY_MAX_TOKENS = 200
SUMMARY_TEMPERATURE = 0.2


class LLMError(Exception):
    pass


def looks_like_letter(title: str) -> bool:
    lower = title.strip().lower()
    if not lower:
        return False
    if "letter to the editor" in lower:
        return True
    if lower.startswith("letter to "):
        return True
    if lower.startswith("letter:"):
        return True
    if lower.startswith("reply to letter"):
        return True
    return False


def load_fetch_entries(path: Path) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    if not path.exists():
        return entries
    with path.open("r", encoding="utf-8") as f:
        for raw_line in f:
            raw_line = raw_line.strip()
            if not raw_line:
                continue
            try:
                entries.append(json.loads(raw_line))
            except json.JSONDecodeError:
                continue
    return entries


def choose_weekly_entry(entries: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not entries:
        raise RuntimeError("No fetch_log entries available")
    # Prefer entries with explicit days >= 7, else newest entry
    candidates = []
    for entry in entries:
        days = entry.get("days")
        if isinstance(days, int) and days >= 7:
            candidates.append(entry)
    if candidates:
        return sorted(candidates, key=lambda e: e.get("started", ""))[-1]
    return entries[-1]


def load_cache(cache_path: Path) -> Dict[str, Any]:
    if cache_path.exists():
        try:
            return json.loads(cache_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"generated_at": None, "run_started": None, "documents": []}


def save_cache(cache_path: Path, data: Dict[str, Any]) -> None:
    cache_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def flatten_doc_text(doc: Dict[str, Any]) -> str:
    preferred_keys = [
        "text",
        "abstract",
        "summary",
        "description",
        "content",
        "body",
        "sections",
        "details",
    ]
    parts: List[str] = []
    for key in preferred_keys:
        val = doc.get(key)
        if isinstance(val, str) and val.strip():
            parts.append(val.strip())
        elif isinstance(val, list):
            parts.extend([str(item) for item in val if isinstance(item, str)])
    if not parts:
        # Fallback to concatenating other string fields (excluding ids/links)
        for key, val in doc.items():
            if key in {"id", "link", "source", "title", "date"}:
                continue
            if isinstance(val, str) and val.strip():
                parts.append(val.strip())
    combined = "\n".join(parts)
    return combined[:MAX_TEXT_CHARS]


def build_prompt(title: str, content: str) -> str:
    instructions = (
        "You are a medical literature analyst. Write a concise two-sentence summary "
        "(max 80 words) highlighting the key findings, population, and clinical takeaway. "
        "If information is missing, note that explicitly."
    )
    return f"{instructions}\n\nTitle: {title}\n\nContent:\n{content}\n\nSummary:"


def call_llm(prompt: str) -> str:
    payload = {
        "prompt": prompt,
        "temperature": SUMMARY_TEMPERATURE,
        "n_predict": SUMMARY_MAX_TOKENS,
        "stop": ["\n\n", "Title:"],
        "stream": False,
        "trim_stop": True,
    }
    try:
        response = requests.post(LLAMA_URL, json=payload, timeout=60)
        response.raise_for_status()
        data = response.json()
    except Exception as exc:
        raise LLMError(str(exc)) from exc

    if isinstance(data, dict):
        if isinstance(data.get("content"), str):
            return data["content"].strip()
        choices = data.get("choices")
        if isinstance(choices, list) and choices:
            choice = choices[0]
            if isinstance(choice, dict):
                text = choice.get("text") or choice.get("content")
                if isinstance(text, str):
                    return text.strip()
    raise LLMError("Unexpected llama-server response")


def summarise_documents(entry: Dict[str, Any], cache: Dict[str, Any], max_docs: int) -> Dict[str, Any]:
    cached_docs = {doc.get("doc_key"): doc for doc in cache.get("documents", [])}
    summaries: List[Dict[str, Any]] = []
    processed = 0
    failures = 0

    for batch in entry.get("batches", []):
        file_path = batch.get("file")
        if not file_path:
            continue
        doc_path = (RAG_ROOT / file_path).resolve()
        if not doc_path.exists():
            print(f"Warning: missing file {doc_path}")
            continue
        docs: List[Dict[str, Any]]
        if doc_path.suffix.lower() == ".jsonl":
            docs = []
            with doc_path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        docs.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        else:
            try:
                docs = json.loads(doc_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                print(f"Warning: could not parse {doc_path}")
                continue

        for doc in docs:
            if processed >= max_docs:
                break

            title = str(doc.get("title") or "Untitled document").strip()
            if looks_like_letter(title):
                continue

            source = str(doc.get("source") or batch.get("source") or "unknown")
            doc_id = str(doc.get("id") or doc.get("link") or doc.get("title") or (processed + 1))
            link = doc.get("link")
            doc_key = f"{source}:{doc_id}"

            cached = cached_docs.get(doc_key)
            if cached and cached.get("summary"):
                summaries.append(cached)
                processed += 1
                continue

            content = flatten_doc_text(doc)
            prompt = build_prompt(title, content)
            try:
                summary_text = call_llm(prompt)
            except LLMError as exc:
                failures += 1
                summary_text = f"Summary unavailable ({exc})."

            summaries.append({
                "doc_key": doc_key,
                "source": source,
                "title": title,
                "link": link,
                "summary": summary_text,
            })
            processed += 1
        if processed >= max_docs:
            break

    print(f"Summaries generated: {processed} (failures: {failures})")

    return {
        "generated_at": datetime.now().isoformat(),
        "run_started": entry.get("started"),
        "documents": summaries,
    }


def main(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(description="Generate weekly RAG document summaries")
    parser.add_argument("--max-docs", type=int, default=MAX_DOCS_DEFAULT, help="Limit number of documents to summarise (per run)")
    args = parser.parse_args(argv)

    entries = load_fetch_entries(FETCH_LOG)
    if not entries:
        print("No fetch_log entries found; aborting")
        return 1

    target_entry = choose_weekly_entry(entries)
    cache = load_cache(CACHE_PATH)

    # If cache is fresh (<7 days) and run matches, reuse
    generated_at = cache.get("generated_at")
    run_started = cache.get("run_started")
    if generated_at and run_started == target_entry.get("started"):
        try:
            ts = datetime.fromisoformat(generated_at)
            if ts >= datetime.now() - timedelta(days=7):
                print("Cached summaries are up to date; nothing to do")
                return 0
        except ValueError:
            pass

    data = summarise_documents(target_entry, cache, args.max_docs)
    save_cache(CACHE_PATH, data)
    print(f"Saved {len(data['documents'])} summaries to {CACHE_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
