# fetch_sources.py
"""
fetch_sources.py

Automated weekly retrieval of new medical documents for the RAG pipeline.
- Pulls from PubMed, ClinicalTrials.gov, OpenFDA, and DrugBank (stub if no key).
- Applies inclusion/exclusion filters from sources_config.py.
- Saves items into ./raw_docs/ as JSON files (1 file per source per run) with metadata.
- Appends a structured line to fetch_log.jsonl for each run and per-source results.
- Can be run ad-hoc or as a long-running scheduled job using the `schedule` library.

Run once (ad-hoc):
    python fetch_sources.py --domains cardiology,pulmonology --days 7

Run as a weekly scheduler (keeps process alive and runs every Monday 08:00):
    python fetch_sources.py --weekly

Notes:
- For DrugBank, if DRUGBANK_API_KEY is not set, the fetcher uses a mock stub.
- PubMed and ClinicalTrials.gov do not require keys for basic usage; consider adding email/tool params.
- OpenFDA does not require a key for low-volume requests.

"""
from __future__ import annotations
import os
import json
import time
import argparse
import datetime as dt
from typing import Dict, List, Any, Optional
from pathlib import Path

import requests
from urllib.parse import quote

# local project modules
from sources_config import get_config
from log_utils import append_recent_log

RAW_DIR = Path("./raw_docs")
RAW_DIR.mkdir(parents=True, exist_ok=True)
LOG_PATH = Path("fetch_log.jsonl")

# ----------------------------
# Filter helpers
# ----------------------------

def _text_passes_filters(text: str, cfg: Dict, domains: List[str]) -> bool:
    text_l = text.lower()

    include = cfg["filters"]["include"]
    excl = cfg["filters"]["exclude"]["global"]["keywords"]

    # Exclusion first: block if any excluded keyword present
    for bad in excl:
        if bad.lower() in text_l:
            return False

    # Global include: at least one keyword or MeSH-ish token
    glob_kw = [k.lower() for k in include["global"]["keywords"]]
    glob_mesh = [m.lower() for m in include["global"]["mesh"]]
    inc_hit = any(k in text_l for k in glob_kw) or any(m in text_l for m in glob_mesh)

    # Domain include: if domains provided, require at least one hit across chosen domains
    dom_hit = False
    for d in domains:
        dinfo = include["by_domain"].get(d, {})
        dkw = [k.lower() for k in dinfo.get("keywords", [])]
        dmesh = [m.lower() for m in dinfo.get("mesh", [])]
        if any(k in text_l for k in dkw) or any(m in text_l for m in dmesh):
            dom_hit = True
            break

    # If user specified domains, require both a global hit and a domain hit
    if domains:
        return inc_hit and dom_hit
    # Otherwise just require global include
    return inc_hit


def _looks_like_letter(title: str) -> bool:
    """Heuristic filter to drop pubmed letters/editorials."""
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


# ----------------------------
# Source fetchers
# ----------------------------

def fetch_pubmed(days: int, domains: List[str], cfg: Dict) -> List[Dict[str, Any]]:
    base = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
    since = (dt.date.today() - dt.timedelta(days=days)).strftime("%Y/%m/%d")

    # Broad query — pull recent clinical articles, filter downstream
    # Include clinical study types explicitly
    term = (
        f"(english[lang]) AND "
        f"(\"{since}\"[Date - Publication] : \"3000\"[Date - Publication]) AND "
        f"((clinical trial[pt] OR randomized controlled trial[pt] OR practice guideline[pt] "
        f"OR systematic review[pt] OR meta-analysis[pt] OR observational study[pt] "
        f"OR cohort study[pt] OR case-control[pt] OR cross-sectional[pt] "
        f"OR prospective[tiab] OR retrospective[tiab] OR treatment[tiab] "
        f"OR management[tiab] OR therapy[tiab] OR diagnosis[tiab] OR prognosis[tiab] "
        f"OR outcome[tiab] OR intervention[tiab] OR randomized[tiab] OR controlled[tiab]) "
        f"NOT (review[pt] OR editorial[pt] OR letter[pt] OR comment[pt] OR case report[pt]))"
    )

    esearch = f"{base}/esearch.fcgi?db=pubmed&retmode=json&retmax=500&term={quote(term)}&tool=rag-pipeline&email=eissa.islam@gmail.com"
    r = requests.get(esearch, timeout=30)
    r.raise_for_status()
    data = r.json()
    ids = data.get("esearchresult", {}).get("idlist", [])
    if not ids:
        return []

    # Batch ESummary and EFetch — NCBI has URL length limits (~8KB)
    # Process in batches of 50 IDs
    BATCH_SIZE = 50
    all_summaries: Dict[str, Any] = {}
    all_abstracts: Dict[str, str] = {}

    for i in range(0, len(ids), BATCH_SIZE):
        batch_ids = ids[i:i+BATCH_SIZE]
        id_param = ",".join(batch_ids)

        # ESummary batch
        esum = f"{base}/esummary.fcgi?db=pubmed&retmode=json&id={id_param}&tool=rag-pipeline&email=eissa.islam@gmail.com"
        try:
            rs = requests.get(esum, timeout=60)
            rs.raise_for_status()
            summ = rs.json().get("result", {})
            all_summaries.update(summ)
        except Exception:
            pass

        # EFetch batch for abstracts
        efetch_url = f"{base}/efetch.fcgi?db=pubmed&retmode=xml&id={id_param}&tool=rag-pipeline&email=eissa.islam@gmail.com"
        try:
            rf = requests.get(efetch_url, timeout=120)
            rf.raise_for_status()
            from xml.etree import ElementTree as ET
            root = ET.fromstring(rf.text)
            for article in root.findall(".//PubmedArticle"):
                pmid_elem = article.find(".//PMID")
                pmid = pmid_elem.text if pmid_elem is not None else None
                # Get full abstract — handle sub-elements (Background, Methods, Results, Conclusion)
                abst_elem = article.find(".//Abstract")
                abst_text = ""
                if abst_elem is not None:
                    abst_text = "".join(abst_elem.itertext()).strip()
                if pmid and abst_text:
                    all_abstracts[pmid] = abst_text
        except Exception:
            pass

    items: List[Dict[str, Any]] = []
    for pid, obj in all_summaries.items():
        if pid == "uids":
            continue
        title = obj.get("title", "").strip()
        if _looks_like_letter(title):
            continue
        journal = obj.get("fulljournalname", "")
        pubdate = obj.get("pubdate", "")
        abstr = all_abstracts.get(pid, "")
        text_for_filter = f"{title} {journal} {abstr}"
        if not _text_passes_filters(text_for_filter, cfg, domains):
            continue
        items.append({
            "title": title,
            "source": "pubmed",
            "id": pid,
            "date": pubdate,
            "journal": journal,
            "link": f"https://pubmed.ncbi.nlm.nih.gov/{pid}/",
            "text": abstr,
        })
    return items


def fetch_clinicaltrials(days: int, domains: List[str], cfg: Dict) -> List[Dict[str, Any]]:
    since = (dt.date.today() - dt.timedelta(days=days)).isoformat()  # YYYY-MM-DD

    # Use classic-style search expression embedded in v2 query.term.
    term = f"AREA[StudyFirstPostDate]RANGE[{since},MAX]"

    base_url = "https://clinicaltrials.gov/api/v2/studies"
    params = {
        "format": "json",
        "pageSize": 100,
        "query.term": term,
    }

    items: List[Dict[str, Any]] = []
    page_token: Optional[str] = None

    while True:
        if page_token:
            params["pageToken"] = page_token
        r = requests.get(base_url, params=params, timeout=60)
        if r.status_code != 200:
            raise RuntimeError(f"CTG v2 HTTP {r.status_code} {r.text[:300]}")
        data = r.json()
        studies = data.get("studies", []) or []

        for s in studies:
            ps = s.get("protocolSection", {}) or {}
            ident = ps.get("identificationModule", {}) or {}
            status = ps.get("statusModule", {}) or {}
            conds = (ps.get("conditionsModule", {}) or {}).get("conditions", []) or []

            title = ident.get("officialTitle") or ident.get("briefTitle") or ""
            nct = ident.get("nctId")
            posted = status.get("studyFirstPostDate") or ""

            # Extract description for full-text content
            desc_module = (ps.get("descriptionModule", {}) or {})
            desc = (desc_module.get("detailedDescription") or "").strip()
            # Also grab brief summary if available
            brief = (desc_module.get("briefSummary") or "").strip()
            description = (desc + "\n\n" + brief).strip()

            text_for_filter = f"{title} {'; '.join(conds)} {description}"
            if not _text_passes_filters(text_for_filter, cfg, domains):
                continue
            items.append({
                "title": title,
                "source": "clinicaltrials",
                "id": nct,
                "date": posted,
                "conditions": conds,
                "link": f"https://clinicaltrials.gov/study/{nct}",
                "text": description,
            })

        page_token = data.get("nextPageToken")
        if not page_token:
            break

    return items

def fetch_openfda(days: int, domains: List[str], cfg: Dict) -> List[Dict[str, Any]]:
    # Pull recent drug label updates as a signal for labeling/safety changes
    # Note: OpenFDA search syntax uses date ranges in YYYYMMDD
    start = (dt.date.today() - dt.timedelta(days=days)).strftime("%Y%m%d")
    url = (
        "https://api.fda.gov/drug/label.json?"
        f"search=effective_time:[{start}+TO+30000101]&limit=100"
    )
    try:
        r = requests.get(url, timeout=60)
        r.raise_for_status()
        data = r.json()
    except Exception:
        return []
    results = data.get("results", [])
    items: List[Dict[str, Any]] = []
    for obj in results:
        title = ", ".join(obj.get("openfda", {}).get("brand_name", []) or obj.get("openfda", {}).get("generic_name", []) or ["Drug Label Update"])  # noqa: E501
        text_for_filter = title
        if not _text_passes_filters(text_for_filter, cfg, domains):
            continue
        items.append({
            "title": title,
            "source": "openfda",
            "id": obj.get("id"),
            "date": obj.get("effective_time"),
            "link": None,
        })
    return items


def fetch_drugbank(days: int, domains: List[str], cfg: Dict) -> List[Dict[str, Any]]:
    # Real DrugBank calls require an API key; use stub if not present
    api_key = os.getenv("DRUGBANK_API_KEY", "")
    if not api_key:
        # Return a small mocked list to keep the pipeline consistent
        demo = [{
            "title": "DrugBank stub item",
            "source": "drugbank",
            "id": "DB-EXAMPLE-1",
            "date": dt.date.today().isoformat(),
            "link": "https://go.drugbank.com/",
        }]
        return [x for x in demo if _text_passes_filters(x["title"], cfg, domains)]

    # If a key exists, place your real fetch here (placeholder)
    # Example pattern:
    # headers = {"Authorization": f"Bearer {api_key}"}
    # r = requests.get("https://api.drugbank.com/v1/some/endpoint", headers=headers, timeout=60)
    # r.raise_for_status(); data = r.json(); ...
    return []


# ----------------------------
# Orchestration
# ----------------------------

def save_batch(source_id: str, items: List[Dict[str, Any]]) -> Optional[Path]:
    if not items:
        return None
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = RAW_DIR / f"{source_id}_{stamp}.json"
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)
    return out_path


def append_log(entry: Dict[str, Any]) -> None:
    append_recent_log(entry, LOG_PATH, max_age_days=7)


def run_fetch(domains: List[str], days: int) -> Dict[str, Any]:
    cfg = get_config()

    # Normalize and validate requested domains
    valid = set(cfg["domains"])
    domains = [d for d in domains if d in valid]

    start = dt.datetime.now().isoformat()
    results: Dict[str, Any] = {"started": start, "domains": domains, "days": days, "batches": []}

    # P3-3: this used to fetch all four sources inline inside one dict
    # literal under a single try/except -- one source's transient failure
    # (a timeout, an API change) raised before the dict literal finished
    # evaluating, so save_batch() never ran for ANY source, discarding
    # every other source's already-successful results for the whole run.
    # Each source now gets its own try/except so the others' results still
    # get saved; per-source errors are recorded rather than losing
    # everything.
    fetchers = {
        "pubmed": lambda: fetch_pubmed(days, domains, cfg),
        "clinicaltrials": lambda: fetch_clinicaltrials(days, domains, cfg),
        "openfda": lambda: fetch_openfda(days, domains, cfg),
        "drugbank": lambda: fetch_drugbank(days, domains, cfg),
    }
    any_error = False
    for sid, fetch in fetchers.items():
        try:
            items = fetch()
            path = save_batch(sid, items)
            results["batches"].append({
                "source": sid,
                "count": len(items),
                "file": str(path) if path else "",
            })
        except Exception as e:
            any_error = True
            results["batches"].append({
                "source": sid,
                "count": 0,
                "file": "",
                "error": str(e),
            })
    results["status"] = "partial_error" if any_error else "ok"
    results["finished"] = dt.datetime.now().isoformat()
    append_log(results)

    return results


def schedule_weekly(domains: List[str]) -> None:
    import schedule  # optional dependency; only needed for --weekly long-running mode

    # Default weekly job every Monday 08:00
    schedule.every().monday.at("08:00").do(run_fetch, domains=domains, days=7)
    print("Scheduler active. Weekly job set for Monday 08:00. Press Ctrl+C to stop.")
    while True:
        schedule.run_pending()
        time.sleep(1)


# ----------------------------
# CLI
# ----------------------------

def main():
    p = argparse.ArgumentParser(description="Fetch new medical documents into ./raw_docs/")
    p.add_argument("--domains", type=str, default="", help="Comma-separated domains to focus (see sources_config.py)")
    p.add_argument("--days", type=int, default=7, help="Lookback window in days for new items")
    p.add_argument("--weekly", action="store_true", help="Run as a weekly scheduled job (long-running)")
    args = p.parse_args()

    domains = [d.strip() for d in args.domains.split(",") if d.strip()]

    if args.weekly:
        schedule_weekly(domains)
    else:
        res = run_fetch(domains=domains, days=args.days)
        print(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
