# guidelines_fetcher.py
# guidelines_fetcher.py
# Full-text fetcher for open-access clinical practice guidelines
# - Crawls each society index you listed
# - Prefers downloadable guideline PDFs if present; otherwise extracts full HTML text
# - Writes one JSON object per guideline with the complete text content
# - Optional: emits .txt files ready for your chunker (Source/Section/last_updated headers)
#
# Usage:
#   python guidelines_fetcher.py --emit-txt
#   python guidelines_fetcher.py --limit-per-source 40 --timeout 45
#
# Outputs:
#   ./raw_docs/guidelines_YYYYmmdd_HHMMSS.jsonl
#   ./fetch_log.jsonl
#   ./sample_corpus/*.txt (if --emit-txt)

import sys  # noqa: F401
import os
import re
import json
import time
import hashlib
import argparse
import datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse
import requests
import logging
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup
from typing import Dict, Any, List, Tuple, Set

from log_utils import append_recent_log

# Project config (optional)
try:
    from sources_config import get_config  # type: ignore
except Exception:
    def get_config() -> Dict[str, Any]:
        return {"filters": {"include": {"global": {"keywords": []}, "by_domain": {}}, "exclude": {"global": {"keywords": []}}}}

RAW_DIR = Path("./raw_docs")
RAW_DIR.mkdir(parents=True, exist_ok=True)
LOG_PATH = Path("fetch_log.jsonl")

# Optional PDF extraction backends (try what is installed)
_PDF_BACKENDS = {}
try:
    import PyPDF2
    _PDF_BACKENDS["pypdf2"] = True
    logging.getLogger("PyPDF2").setLevel(logging.ERROR)
except Exception:
    pass

try:
    # pdfminer.six
    from pdfminer.high_level import extract_text as pdfminer_extract_text
    _PDF_BACKENDS["pdfminer"] = True
except Exception:
    pass

DEFAULT_UA = os.getenv("GUIDELINES_UA", "RAGPipelineGuidelineFetcher/2.0 (+contact@example.local)")
PDF_MAX_BYTES_DEFAULT = 8 * 1024 * 1024  # 8 MiB
PDF_MAX_BYTES = int(os.getenv("GUIDELINES_PDF_MAX_BYTES", str(PDF_MAX_BYTES_DEFAULT)))
USE_PDFMINER = os.getenv("GUIDELINES_USE_PDFMINER", "0").lower() not in ("0", "false", "no", "")

def _make_session() -> requests.Session:
    s = requests.Session()
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
        "User-Agent": DEFAULT_UA,
        "Accept": "text/html,application/xhtml+xml,application/pdf",
    })
    return s

SESSION = _make_session()
logging.getLogger("pdfminer").setLevel(logging.ERROR)
logging.getLogger("pdfminer").setLevel(logging.ERROR)

def sleep_ms(ms):
    time.sleep(ms/1000.0)

def sha1(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:16]

# Date detection is best-effort; we keep as-is but no longer truncate text
DATE_PAT = re.compile(r"(\b\d{4}[-/]\d{1,2}[-/]\d{1,2}\b|\b\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4}\b|\b[A-Za-z]{3,9}\s+\d{1,2},\s*\d{4}\b|\b\d{4}\b)")

def find_date(text: str):
    m = DATE_PAT.search(text or "")
    return m.group(0) if m else ""

def get(url, timeout=30, stream=False):
    try:
        r = SESSION.get(url, timeout=timeout, allow_redirects=True, stream=stream)
        if r.status_code == 200:
            return r
    except Exception:
        return None
    return None

def absolute_links(base, links):
    out = []
    for a in links:
        href = a.get("href") or ""
        if not href or href.startswith("#"):
            continue
        absu = urljoin(base, href)
        text = a.get_text(" ", strip=True)
        out.append((absu, text))
    # de-dup by URL
    seen = set()
    uniq = []
    for u, t in out:
        if u not in seen:
            seen.add(u)
            uniq.append((u, t))
    return uniq

GUIDE_KEYWORDS = [
    "guideline", "practice-guideline", "clinical-guideline",
    "statement", "position-statement", "recommendation", "consensus",
    "standard", "care pathway", "policy", "guidance"
]

def looks_like_guideline(url: str, text: str):
    url_l = url.lower()
    t_l = (text or "").lower()
    if any(k in url_l for k in GUIDE_KEYWORDS):
        return True
    if any(k in t_l for k in GUIDE_KEYWORDS):
        return True
    # Also catch typical PDF naming
    if url_l.endswith(".pdf") and any(k in url_l for k in ["guideline", "statement", "consensus", "recommend"]):
        return True
    return False

def scrape_index_generic(start_url: str, domain_filter: str = "", link_filter=None, timeout=30):
    r = get(start_url, timeout=timeout)
    if not r or "text/html" not in r.headers.get("Content-Type",""):
        return []
    html = r.text
    soup = BeautifulSoup(html, "html.parser")
    anchors = soup.find_all("a")
    pairs = absolute_links(start_url, anchors)
    out = []
    for u, t in pairs:
        if domain_filter and urlparse(u).netloc and domain_filter not in urlparse(u).netloc:
            continue
        if link_filter and not link_filter(u, t):
            continue
        if looks_like_guideline(u, t):
            out.append(u)
    return out

def extract_html_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    # Drop common non-content containers
    for sel in ["nav", "header", "footer", "form", "aside", "script", "style"]:
        for tag in soup.find_all(sel):
            tag.decompose()
    # Prefer main/article if present
    main = soup.find("main") or soup.find("article") or soup.find("div", attrs={"role": "main"}) or soup
    blocks = []
    for el in main.find_all(["h1","h2","h3","h4","h5","h6","p","li","blockquote","pre","code","table","th","td"]):
        txt = el.get_text(" ", strip=True)
        if txt:
            blocks.append(txt)
    # Merge conservatively to keep sections readable
    return "\n".join(blocks)

def extract_pdf_text_bytes(pdf_bytes: bytes) -> str:
    """Extract PDF text while tolerating malformed font tables."""
    errors: List[str] = []
    # Prefer PyPDF2 for robustness/speed
    if _PDF_BACKENDS.get("pypdf2"):
        try:
            from io import BytesIO
            reader = PyPDF2.PdfReader(BytesIO(pdf_bytes))
            pages = []
            for p in reader.pages:
                try:
                    pages.append(p.extract_text() or "")
                except Exception as exc:
                    errors.append(f"PyPDF2 page extraction failed: {exc}")
                    pages.append("")
            text = "\n".join(pages)
            if text.strip():
                return text
        except Exception as exc:
            errors.append(f"PyPDF2 failed: {exc}")
    # Optionally try pdfminer if enabled
    if USE_PDFMINER and _PDF_BACKENDS.get("pdfminer"):
        try:
            from io import BytesIO
            text = pdfminer_extract_text(BytesIO(pdf_bytes)) or ""
            if text.strip():
                return text
        except Exception as exc:
            errors.append(f"pdfminer failed: {exc}")
    if errors:
        print(
            f"Warning: unable to extract text from guideline PDF; skipped. Details: {'; '.join(errors)}"
        )
    return ""

def fetch_fulltext_from_url(url: str, timeout=45, prefer_pdf: bool = False, pdf_max_bytes: int = PDF_MAX_BYTES):
    r = get(url, timeout=timeout, stream=True)
    if not r:
        return {"content_type": "", "text": "", "html": ""}
    ctype = r.headers.get("Content-Type","").split(";")[0].strip().lower()
    if ctype == "application/pdf" or url.lower().endswith(".pdf"):
        # Respect size cap
        try:
            content_len = int(r.headers.get("Content-Length", "0") or "0")
        except Exception:
            content_len = 0
        if content_len and content_len > pdf_max_bytes:
            return {"content_type": "pdf", "text": "", "html": ""}
        total = 0
        chunks = []
        for chunk in r.iter_content(1024 * 64):
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > pdf_max_bytes:
                break
        pdf_bytes = b"".join(chunks)
        text = extract_pdf_text_bytes(pdf_bytes)
        return {"content_type": "pdf", "text": text, "html": ""}
    # HTML path
    if "text/html" in ctype or ctype == "":
        html = r.text if hasattr(r, "text") else r.content.decode("utf-8", errors="ignore")
        text = extract_html_text(html)
        # If requested, fetch linked PDF (with size cap)
        if prefer_pdf:
            try:
                soup = BeautifulSoup(html, "html.parser")
                pdf_link = None
                for a in soup.find_all("a"):
                    href = a.get("href") or ""
                    if href.lower().endswith(".pdf"):
                        pdf_link = urljoin(url, href)
                        break
                if pdf_link:
                    rp = get(pdf_link, timeout=timeout, stream=True)
                    if rp and rp.status_code == 200:
                        ctp = rp.headers.get("Content-Type","").split(";")[0].strip().lower()
                        if ctp == "application/pdf" or pdf_link.lower().endswith(".pdf"):
                            try:
                                content_len = int(rp.headers.get("Content-Length", "0") or "0")
                            except Exception:
                                content_len = 0
                            if not content_len or content_len <= pdf_max_bytes:
                                total = 0
                                chunks = []
                                for chunk in rp.iter_content(1024 * 64):
                                    if not chunk:
                                        break
                                    chunks.append(chunk)
                                    total += len(chunk)
                                    if total > pdf_max_bytes:
                                        break
                                pdf_bytes = b"".join(chunks)
                                pdf_text = extract_pdf_text_bytes(pdf_bytes)
                                if pdf_text and len(pdf_text) > len(text):
                                    return {"content_type": "pdf", "text": pdf_text, "html": ""}
            except Exception:
                pass
        return {"content_type": "html", "text": text, "html": html}
    return {"content_type": ctype, "text": "", "html": ""}

def extract_title_and_date(html: str, fallback_url: str = ""):
    if not html:
        return "", ""
    soup = BeautifulSoup(html, "html.parser")
    title = soup.find("h1") or soup.find("title")
    title_text = (title.get_text(" ", strip=True) if title else "")[:500]
    meta_date = ""
    for key in ["last-modified","article:published_time","article:modified_time","dc.date","dc.date.modified","dc.date.issued","date"]:
        m = soup.find("meta", attrs={"name": key}) or soup.find("meta", attrs={"property": key})
        if m and (m.get("content") or "").strip():
            meta_date = m["content"].strip()
            break
    if not meta_date:
        meta_date = find_date(html)
    return title_text, meta_date

# sources list omitted in this snippet for brevity
SOURCES: List[Dict[str, str]] = [
    {"name": "ACP",       "index": "https://www.acponline.org/clinical-information/clinical-guidelines-recommendations", "domain": "acponline.org"},
    {"name": "IDSA",      "index": "https://www.idsociety.org/practice-guideline/",                                      "domain": "idsociety.org"},
    {"name": "ACC/AHA",   "index": "https://www.acc.org/guidelines",                                                      "domain": "acc.org"},
    {"name": "NICE",      "index": "https://www.nice.org.uk/guidance/published",                                          "domain": "nice.org.uk"},
    {"name": "ADA",       "index": "https://diabetesjournals.org/care/issue",                                             "domain": "diabetesjournals.org"},
    {"name": "KDIGO",     "index": "https://kdigo.org/guidelines/",                                                       "domain": "kdigo.org"},
    {"name": "ATS",       "index": "https://www.thoracic.org/statements/index.php?archive=0",                            "domain": "thoracic.org"},
    {"name": "CHEST",     "index": "https://journal.chestnet.org/guidelines",                                             "domain": "chestnet.org"},
    {"name": "ASCO",      "index": "https://www.asco.org/guidelines",                                                     "domain": "asco.org"},
    {"name": "AAN",       "index": "https://www.aan.com/Guidelines/home",                                                 "domain": "aan.com"},
    {"name": "AGA",       "index": "https://gastro.org/clinical-guidance/",                                               "domain": "gastro.org"},
    {"name": "ACG",       "index": "https://gi.org/clinical-guidelines/",                                                 "domain": "gi.org"},
    {"name": "ASH",       "index": "https://www.hematology.org/education/clinicians/guidelines-and-quality-care",         "domain": "hematology.org"},
    {"name": "ESMO",      "index": "https://www.esmo.org/guidelines",                                                     "domain": "esmo.org"},
    {"name": "WHO",       "index": "https://www.who.int/publications/who-guidelines",                                     "domain": "who.int"},
]

def crawl_source(src, max_links=80, timeout=45, depth=2, prefer_pdf: bool = False, pdf_max_bytes: int = PDF_MAX_BYTES):
    name = src["name"]
    index = src["index"]
    dom = src.get("domain", "")
    if "requires login" in name.lower() or "skipped" in name.lower():
        return [], {"skipped": True, "reason": "login required"}
    urls = scrape_index_generic(index, domain_filter=dom, timeout=timeout)
    # normalize, dedupe, cap
    urls = [u for u in urls if urlparse(u).netloc and (dom in urlparse(u).netloc)]
    seen = set()
    uniq = []
    for u in urls:
        key = re.sub(r"[?#].*$","",u)
        if key not in seen:
            seen.add(key)
            uniq.append(u)
    # exclude the index page itself
    base_norm = re.sub(r"[?#].*$", "", index).rstrip("/")
    uniq = [u for u in uniq if re.sub(r"[?#].*$", "", u).rstrip("/") != base_norm]
    urls = uniq[:max_links]
    # If very few found, attempt a shallow BFS within domain to discover more
    if len(urls) < max_links // 2:
        urls = urls + collect_guideline_links(index, domain=dom, timeout=timeout, max_links=max_links, depth=depth)
        # dedupe again
        seen2 = set()
        uniq2 = []
        for u in urls:
            key2 = re.sub(r"[?#].*$","",u)
            if key2 not in seen2:
                seen2.add(key2)
                uniq2.append(u)
        urls = uniq2[:max_links]

    items: List[Dict[str, Any]] = []
    for i, u in enumerate(urls):
        sleep_ms(250)
        ft = fetch_fulltext_from_url(u, timeout=timeout, prefer_pdf=prefer_pdf, pdf_max_bytes=pdf_max_bytes)
        text = ft.get("text","")
        html = ft.get("html","")
        if not text and not html:
            continue

        title_text, last_updated = ("", "")
        if html:
            t, d = extract_title_and_date(html, fallback_url=u)
            title_text, last_updated = t, d

        if not title_text and text:
            for line in text.splitlines():
                s = line.strip()
                if s:
                    title_text = s[:500]
                    break

        item: Dict[str, Any] = {
            "id": sha1(u),
            "link": u,
            "source": "guidelines",
            "society": name,
            "section": "Guideline",
            "title": title_text,
            "date": last_updated,
            "content_type": ft.get("content_type",""),
            "text": text if text else extract_html_text(html),
        }
        items.append(item)
    stats = {"count": len(items), "index": index}
    return items, stats

def collect_guideline_links(start_url: str, domain: str, timeout: int, max_links: int, depth: int = 2) -> List[str]:
    found: List[str] = []
    visited: Set[str] = set()
    q: List[Tuple[str, int]] = [(start_url, depth)]

    def should_follow(u: str, t: str) -> bool:
        hint = any(k in (u.lower() + " " + t.lower()) for k in [
            "guideline", "guidance", "statement", "recommend", "consensus", "practice"
        ])
        return hint and (domain in (urlparse(u).netloc or ""))

    while q and len(found) < max_links:
        url, d = q.pop(0)
        norm = re.sub(r"[?#].*$", "", url)
        if norm in visited:
            continue
        visited.add(norm)

        r = get(url, timeout=timeout)
        if not r or "text/html" not in r.headers.get("Content-Type",""):
            continue
        soup = BeautifulSoup(r.text, "html.parser")
        anchors = soup.find_all("a")
        for u, t in absolute_links(url, anchors):
            if domain and domain not in (urlparse(u).netloc or ""):
                continue
            if looks_like_guideline(u, t):
                if u not in found:
                    found.append(u)
                    if len(found) >= max_links:
                        break
            elif d > 0 and should_follow(u, t):
                q.append((u, d - 1))
    return found[:max_links]

def _extract_year(s: str) -> int:
    try:
        m = re.search(r"(19|20)\d{2}", s or "")
        return int(m.group(0)) if m else 0
    except Exception:
        return 0

def filter_by_year(rows: List[Dict[str, Any]], years: int) -> List[Dict[str, Any]]:
    if years <= 0:
        return rows
    cutoff = datetime.datetime.now().year - years
    out: List[Dict[str, Any]] = []
    for r in rows:
        yr = _extract_year(str(r.get("date") or r.get("year") or ""))
        if yr == 0 or yr >= cutoff:
            out.append(r)
    return out

def _parse_date_any(s: str) -> datetime.datetime:
    s = s or ""
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%b %d, %Y", "%B %d, %Y", "%Y-%m", "%Y"):
        try:
            if fmt == "%Y":
                return datetime.datetime(int(s), 1, 1)
            return datetime.datetime.strptime(s, fmt)
        except Exception:
            continue
    import re as _re
    digits = _re.sub(r"[^0-9]", "", s)
    if len(digits) >= 8:
        try:
            return datetime.datetime.strptime(digits[:8], "%Y%m%d")
        except Exception:
            pass
    return datetime.datetime(1970, 1, 1)

def filter_by_days(rows: List[Dict[str, Any]], days: int) -> List[Dict[str, Any]]:
    if days <= 0:
        return rows
    cutoff = datetime.datetime.now() - datetime.timedelta(days=days)
    out: List[Dict[str, Any]] = []
    for r in rows:
        dt = _parse_date_any(str(r.get("date") or r.get("year") or ""))
        if dt.year == 1970:
            out.append(r)
        elif dt >= cutoff:
            out.append(r)
    return out

def _pediatric_re() -> re.Pattern:
    return re.compile(
        r"\b(child|children|pediatric|paediatric|adolescent|infant|neonate|newborn|toddler|"
        r"preschool|school[ -]?age(?:d)?|boy|girl|boys|girls|youth|teen(?:ager)?|"
        r"under\s*16|ages?\s*0-15)\b",
        flags=re.IGNORECASE,
    )

def filter_rows(rows: List[Dict[str, Any]], cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    excl = [k.lower() for k in (cfg.get("filters", {}).get("exclude", {}).get("global", {}).get("keywords", []) or [])]
    ped = _pediatric_re()
    out: List[Dict[str, Any]] = []
    seen_ids = set()
    for r in rows:
        blob = " ".join([str(r.get(k, "")) for k in ("title", "society", "link")]).lower()
        if any(k in blob for k in excl):
            continue
        if ped.search(blob):
            continue
        rid = str(r.get("id") or "")
        if not rid or rid in seen_ids:
            continue
        seen_ids.add(rid)
        out.append(r)
    return out

def write_json_array(path: Path, rows: List[Dict[str, Any]]):
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")

def maybe_emit_txt(rows, outdir: Path):
    outdir.mkdir(parents=True, exist_ok=True)
    for r in rows:
        header = f"Source: {r.get('society','')};\nSection: {r.get('title','')}\nlast_updated: {r.get('date','')}\n"
        body = r.get("text","")
        txt = header + "\n" + body
        src_for_name = (r.get('society') or r.get('source') or 'guidelines').replace(' ','_')
        fname = f"{src_for_name}_{r['id']}.txt"
        (outdir / fname).write_text(txt, encoding="utf-8")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--emit-txt", action="store_true", help="Also write sample_corpus/*.txt with headers for chunker")
    ap.add_argument("--out", default="./raw_docs", help="Output directory for JSON (array)")
    ap.add_argument("--limit-per-source", type=int, default=80, help="Max links per source index")
    ap.add_argument("--timeout", type=int, default=45, help="Per-request timeout seconds")
    ap.add_argument("--years", type=int, default=0, help="Keep guideline pages within last N years (0 = no filter)")
    ap.add_argument("--days", type=int, default=30, help="Keep guideline pages within last N days (overrides years if > 0)")
    ap.add_argument("--depth", type=int, default=2, help="Link-follow depth within domain (BFS)")
    ap.add_argument("--fetch-pdf", action="store_true", help="Also fetch linked PDFs (size-capped)")
    ap.add_argument("--pdf-max-mb", type=int, default=max(1, PDF_MAX_BYTES // (1024*1024)), help="Max PDF size to parse (MB)")
    args = ap.parse_args()

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)
    cfg = get_config()
    started = datetime.datetime.now().isoformat()
    batches: List[Dict[str, Any]] = []
    all_rows: List[Dict[str, Any]] = []

    for src in SOURCES:
        rows, stats = crawl_source(
            src,
            max_links=args.limit_per_source,
            timeout=args.timeout,
            depth=args.depth,
            prefer_pdf=args.fetch_pdf,
            pdf_max_bytes=int(args.pdf_max_mb * 1024 * 1024),
        )
        all_rows.extend(rows)

    # Filter + dedupe consistently
    all_rows = filter_rows(all_rows, cfg)
    if args.days and args.days > 0:
        all_rows = filter_by_days(all_rows, days=args.days)
    else:
        all_rows = filter_by_year(all_rows, years=args.years)

    out_path = outdir / f"guidelines_{ts}.json"
    write_json_array(out_path, all_rows)

    if args.emit_txt:
        maybe_emit_txt(all_rows, Path("./sample_corpus"))

    # Append standard log entry similar to other fetchers
    finished = datetime.datetime.now().isoformat()
    res = {
        "started": started,
        "mode": "crawl",
        "batches": [
            {"source": "guidelines", "count": len(all_rows), "file": str(out_path)}
        ],
        "status": "ok",
        "finished": finished,
        "pdf_backends": list(_PDF_BACKENDS.keys()),
    }
    append_recent_log(res, LOG_PATH, max_age_days=7)

    print(json.dumps(res, indent=2))

if __name__ == "__main__":
    main()
