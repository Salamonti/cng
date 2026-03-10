# C:\RAG\pmc_fetcher.py
"""
pmc_fetcher.py

PMC harvest for Internal Medicine diagnostics & management
- Mode 1: Selective Deposit Collections (publisher-based sets via OAI-PMH)
- Mode 2: PMC Open Access Subset (OA Web Service API)

Filters
- Focus on Internal Medicine (diagnostics/management) using your sources_config include keywords
- Exclude pediatric populations <16y using MeSH- and keyword-style filters
- Restrict to last 5 years by default

Outputs
- Writes batch JSON into ./raw_docs/ with source="pmc" and per-item metadata
- Appends to fetch_log.jsonl (same format as fetch_sources.py)

Docs
- PMC OA Web Service API (lists downloadable OA resources). Suitable for date-window sync. https://www.ncbi.nlm.nih.gov/pmc/tools/oa-service/
- Maintain OA Subset guidance (date-based sync via OA Web Service). https://www.ncbi.nlm.nih.gov/pmc/tools/maintain-oa-subset/
- PMC OAI-PMH Service (metadata & some fulltext). https://www.ncbi.nlm.nih.gov/pmc/tools/oai/

CLI examples
    python pmc_fetcher.py --oa-subset --years 5
    python pmc_fetcher.py --selective --years 5 --publishers "BMJ Publishing Group, Oxford University Press"

"""
from __future__ import annotations
import argparse
import datetime as dt
import json
import re
from pathlib import Path
from typing import Dict, Any, List, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from log_utils import append_recent_log

# Local helpers
try:
    from sources_config import get_config  # to reuse include filters
except Exception:
    def get_config() -> Dict[str, Any]:
        return {"filters": {"include": {"global": {"keywords": []}, "by_domain": {}}}}

RAW_DIR = Path("./raw_docs")
RAW_DIR.mkdir(parents=True, exist_ok=True)
LOG_PATH = Path("fetch_log.jsonl")

# Identify ourselves for NCBI services
TOOL_NAME = "rag-pipeline"
CONTACT_EMAIL = "eissa.islam@gmail.com"


def _make_session() -> requests.Session:
    s = requests.Session()
    # retry/backoff on transient errors
    retry = Retry(
        total=3,
        backoff_factor=0.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    s.mount("http://", adapter)
    s.mount("https://", adapter)
    s.headers.update({
        "User-Agent": f"RAG-PMC-Fetcher/1.0 (+{CONTACT_EMAIL})",
    })
    return s


SESSION = _make_session()

# ----------------------
# IM include / pediatrics exclude
# ----------------------

def _collect_im_keywords(cfg: Dict[str, Any]) -> List[str]:
    inc = cfg.get("filters", {}).get("include", {})
    glob = [k.lower() for k in inc.get("global", {}).get("keywords", [])]
    bydom = inc.get("by_domain", {})
    domk = []
    for d in ("cardiology","pulmonology","endocrinology","infectious_diseases","nephrology","gastroenterology","hematology","oncology","neurology","rheumatology","geriatrics","critical_care","primary_care","pharmacology","diagnostics"):
        domk += [k.lower() for k in bydom.get(d, {}).get("keywords", [])]
    # add some generic diagnostic/management terms
    domk += ["diagnosis","diagnostic","management","treatment","therapy","guideline","recommendation","protocol","algorithm"]
    return list({*glob, *domk})

PEDIATRIC_RE = re.compile(
    r"\b(child|children|pediatric|paediatric|adolescent|infant|neonate|newborn|toddler|"
    r"preschool|school[ -]?age(?:d)?|boy|girl|boys|girls|youth|teen(?:ager)?|"
    r"under\s*16|ages?\s*0-15)\b",
    flags=re.IGNORECASE,
)


def _looks_im_relevant(text: str, im_terms: List[str]) -> bool:
    t = text.lower()
    # must hit at least one IM term
    return any(k in t for k in im_terms)


def _is_pediatric(text: str) -> bool:
    return bool(PEDIATRIC_RE.search(text))


# ----------------------
# OA Web Service (OA subset)
# ----------------------

OA_ENDPOINT = "https://www.ncbi.nlm.nih.gov/pmc/utils/oa/oa.fcgi"


def fetch_oa_subset(since_iso: str, max_items: int = 1000) -> List[Dict[str, Any]]:
    """Use PMC OA Web Service to list OA packages since `since_iso` (YYYY-MM-DD).
    Default response is XML; parse minimal attributes (pmcid, license, updated, citation).
    """
    params = {
        "from": since_iso,
        "links": "pmcid",
        "tool": TOOL_NAME,
        "email": CONTACT_EMAIL,
    }
    r = SESSION.get(OA_ENDPOINT, params=params, timeout=120)
    r.raise_for_status()
    txt = r.text

    items: List[Dict[str, Any]] = []
    for m in re.finditer(r"<record\s+([^>]*)>(.*?)</record>", txt, re.DOTALL):
        attrs = m.group(1)
        body = m.group(2)
        idm = re.search(r"\bid=\"(PMC\d+)\"", attrs)
        if not idm:
            continue
        pid = idm.group(1)
        lic = (re.search(r"\blicense=\"([^\"]*)\"", attrs) or [None, ""])[1]  # type: ignore[index]
        cit = (re.search(r"\bcitation=\"([^\"]*)\"", attrs) or [None, ""])[1]  # type: ignore[index]
        up = (re.search(r"<link[^>]*\bupdated=\"([^\"]+)\"", body) or [None, ""])[1]  # type: ignore[index]
        items.append({
            "title": "",
            "citation": cit,
            "source": "pmc",
            "id": pid,
            "date": up,
            "journal": "",
            "license": lic,
            "link": f"https://www.ncbi.nlm.nih.gov/pmc/articles/{pid}/",
        })
        if len(items) >= max_items:
            break
    return items


# ----------------------
# OAI-PMH for Selective Deposit
# ----------------------

OAI_BASE = "https://www.ncbi.nlm.nih.gov/pmc/oai/oai.cgi"


def _list_sets() -> List[Dict[str, str]]:
    r = SESSION.get(OAI_BASE, params={"verb": "ListSets", "tool": TOOL_NAME, "email": CONTACT_EMAIL}, timeout=60)
    r.raise_for_status()
    # crude parse: capture <setSpec> and <setName>
    specs = re.findall(r"<setSpec>(.*?)</setSpec>", r.text)
    names = re.findall(r"<setName>(.*?)</setName>", r.text)
    out = []
    for i, sp in enumerate(specs):
        nm = names[i] if i < len(names) else sp
        out.append({"spec": sp, "name": nm})
    return out


def _match_publisher_sets(publishers: List[str]) -> List[str]:
    sets = _list_sets()
    wants = [p.strip().lower() for p in publishers]
    matched = []
    for s in sets:
        nm = s["name"].lower()
        if any(p in nm for p in wants):
            matched.append(s["spec"])
    return matched


def harvest_selective(publishers: List[str], years: int, page_max: int = 500) -> List[Dict[str, Any]]:
    since = (dt.date.today() - dt.timedelta(days=365 * years)).isoformat()
    sets = _match_publisher_sets(publishers) if publishers else []
    if not sets:
        # fall back to all sets (broad)
        sets = []

    def list_records(set_spec: Optional[str], token: Optional[str]) -> requests.Response:
        if token:
            return SESSION.get(OAI_BASE, params={"verb": "ListRecords", "resumptionToken": token, "tool": TOOL_NAME, "email": CONTACT_EMAIL}, timeout=120)
        params = {"verb": "ListRecords", "from": since, "metadataPrefix": "pmc", "tool": TOOL_NAME, "email": CONTACT_EMAIL}
        if set_spec:
            params["set"] = set_spec
        return SESSION.get(OAI_BASE, params=params, timeout=120)

    items: List[Dict[str, Any]] = []

    target_sets = sets if sets else [None]
    for sset in target_sets:
        token = None
        pages = 0
        while True:
            r = list_records(sset, token)
            r.raise_for_status()
            txt = r.text
            # records contain <record> ... <identifier>oai:pubmedcentral.nih.gov:PMCxxxx</identifier>
            for rec in re.findall(r"<record>(.*?)</record>", txt, re.DOTALL):
                # extract pmcid, title, journal, date if present in PMC metadata
                pmcid = None
                m = re.search(r"<identifier>oai:pubmedcentral.nih.gov:(PMC\d+)</identifier>", rec)
                if m:
                    pmcid = m.group(1)
                title = (re.search(r"<article-title>(.*?)</article-title>", rec) or re.search(r"<title>(.*?)</title>", rec))
                journal = re.search(r"<journal-title>(.*?)</journal-title>", rec)
                date = re.search(r"<pub-date.*?>.*?<year>(\d{4})</year>.*?</pub-date>", rec, re.DOTALL)
                # normalize id and link
                pmcid_norm = pmcid if (pmcid and pmcid.upper().startswith("PMC")) else (f"PMC{pmcid}" if pmcid else "")
                items.append({
                    "title": (title.group(1) if title else ""),
                    "source": "pmc",
                    "id": pmcid_norm or "",
                    "date": (date.group(1) if date else ""),
                    "journal": (journal.group(1) if journal else ""),
                    "link": f"https://www.ncbi.nlm.nih.gov/pmc/articles/{pmcid_norm}/" if pmcid_norm else None,
                    "publisher_set": sset,
                })
            # next token
            t = re.search(r"<resumptionToken[^>]*>(.*?)</resumptionToken>", txt)
            token = t.group(1) if t and t.group(1).strip() else None
            pages += 1
            if not token or pages >= page_max:
                break
    return items


# ----------------------
# Filtering and save
# ----------------------

def filter_im(items: List[Dict[str, Any]], cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    terms = _collect_im_keywords(cfg)
    excl = [k.lower() for k in (cfg.get("filters", {}).get("exclude", {}).get("global", {}).get("keywords", []) or [])]
    kept: List[Dict[str, Any]] = []
    for it in items:
        blob = " ".join(str(it.get(k, "")) for k in ("title", "journal", "link", "citation"))
        bl = blob.lower()
        # Always respect excludes and pediatric screen
        if any(k in bl for k in excl):
            continue
        if _is_pediatric(blob):
            continue
        # Require include-term hit only if we have descriptive text (e.g., a title)
        has_descriptive = bool(str(it.get("title", "")).strip())
        if has_descriptive and not _looks_im_relevant(blob, terms):
            continue
        kept.append(it)
    return kept


def dedupe_by_pmcid(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_id: Dict[str, Dict[str, Any]] = {}
    for it in items:
        pid = str(it.get("id") or "")
        if not pid:
            continue
        if pid.upper().startswith("PMC"):
            key = pid.upper()
        else:
            key = f"PMC{pid}".upper()
            it = {**it, "id": key}
            if not it.get("link"):
                it["link"] = f"https://www.ncbi.nlm.nih.gov/pmc/articles/{key}/"
        if key not in by_id:
            by_id[key] = it
    return list(by_id.values())


def save_batch(tag: str, items: List[Dict[str, Any]]) -> Optional[Path]:
    if not items:
        return None
    # normalize/dedupe by PMCID
    items = dedupe_by_pmcid(items)
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    out = RAW_DIR / f"pmc_{tag}_{stamp}.json"
    out.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def append_log(entry: Dict[str, Any]) -> None:
    append_recent_log(entry, LOG_PATH, max_age_days=7)


# ----------------------
# CLI
# ----------------------

def main():
    ap = argparse.ArgumentParser(description="Harvest PMC Selective Deposit and OA Subset for IM diagnostics/management")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--oa-subset", action="store_true", help="Use OA Web Service (Open Access Subset)")
    g.add_argument("--selective", action="store_true", help="Use OAI-PMH and Selective Deposit sets")

    ap.add_argument("--years", type=int, default=5, help="Lookback window in years (default 5)")
    ap.add_argument("--days", type=int, default=0, help="Lookback window in days (OA subset; overrides years if > 0)")
    ap.add_argument("--publishers", type=str, default="", help="Comma-separated publisher names to match sets (Selective mode)")
    ap.add_argument("--max", type=int, default=2000, help="Cap items saved (per mode)")

    args = ap.parse_args()

    cfg = get_config()

    res = {"started": dt.datetime.now().isoformat(), "mode": "oa" if args.oa_subset else "selective", "years": args.years, "batches": []}
    try:
        if args.oa_subset:
            if args.days and args.days > 0:
                since = (dt.date.today() - dt.timedelta(days=args.days)).isoformat()
            else:
                since = (dt.date.today() - dt.timedelta(days=365 * args.years)).isoformat()
            raw = fetch_oa_subset(since_iso=since, max_items=args.max)
            filt = filter_im(raw, cfg)
            p = save_batch("oa", filt)
            res["batches"].append({"source": "pmc_oa", "raw": len(raw), "kept": len(filt), "file": str(p) if p else ""})
        else:
            pubs = [p.strip() for p in args.publishers.split(",") if p.strip()]
            raw = harvest_selective(publishers=pubs, years=args.years)
            filt = filter_im(raw, cfg)
            if args.max and len(filt) > args.max:
                filt = filt[: args.max]
            p = save_batch("selective", filt)
            res["batches"].append({"source": "pmc_selective", "raw": len(raw), "kept": len(filt), "file": str(p) if p else ""})
        res["status"] = "ok"
    except Exception as e:
        res["status"] = "error"
        res["error"] = str(e)
    finally:
        res["finished"] = dt.datetime.now().isoformat()
        append_log(res)
    print(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
