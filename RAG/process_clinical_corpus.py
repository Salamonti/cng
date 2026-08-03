# process_clinical_corpus.py
#!/usr/bin/env python3
"""
process_clinical_corpus.py

Purpose
- Replace the separate cleaner, fixer, and enricher with a single, deterministic
  step that prepares only decision-useful documents for RAG.
- Works on mixed inputs fetched by your existing fetch_sources.py (PubMed PMIDs,
  ClinicalTrials NCTs). It can also independently fetch missing abstracts/results.

What it guarantees
1) PubMed
   - Pulls full abstract via EFetch.
   - Keeps only items whose abstract contains results/conclusions heuristics.
   - Prioritizes evidence-bearing publication types (RCTs, systematic reviews,
     meta-analyses, guidelines). Subject reviews are allowed if they state a
     conclusion.
   - If OA in PubMed Central, can pull full text (optional --fulltext) and set
     text to the OA body (fallback to abstract if OA not available).

2) ClinicalTrials.gov
   - Keeps only trials with posted results (if any) or sufficiently detailed
     descriptions that include result-like statements (rare). By default, drops
     protocol-only records (no results). Assembles a readable text including
     outcomes and key fields.

3) Output
   - Writes JSONL to --out with text populated and rich metadata.
   - Chunks are not produced here; run chunking_pipeline.py afterwards.

Usage
  python process_clinical_corpus.py --in ./clean_corpus/mixed.fixed.jsonl \
    --out ./clean_corpus/mixed.processed.jsonl --fulltext

  Flags
    --no-trials        Skip ClinicalTrials.gov handling entirely
    --no-pubmed        Skip PubMed handling entirely
    --fulltext         Try to fetch PMC full text when available and licensed

Dependencies
  pip install requests lxml
  (lxml is used to safely parse XML from EFetch/PMC when available.)

Notes
- This script expects records from fetch_sources.py with at least: id, source,
  title, link, and possibly preliminary text. It will re-fetch authoritative
  content from PubMed/CTGov as needed.
"""
from __future__ import annotations
import argparse
import json
import re
import sys  # noqa: F401
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple

import requests
from lxml import etree # type: ignore

# ----------------------------
# General helpers
# ----------------------------

def read_records(path: Path) -> Iterator[Dict[str, Any]]:
    if path.suffix.lower() == ".jsonl":
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                yield json.loads(line)
    elif path.suffix.lower() == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            for o in data:
                yield o
        elif isinstance(data, dict):
            yield data
        else:
            raise SystemExit(f"Unsupported JSON structure in {path}")
    else:
        raise SystemExit(f"Unsupported extension: {path.suffix}")


def ensure_parent_dir(out_path: Path):
    out_path.parent.mkdir(parents=True, exist_ok=True)


def write_jsonl(items: Iterable[Dict[str, Any]], out_path: Path):
    ensure_parent_dir(out_path)
    with out_path.open("w", encoding="utf-8") as f:
        for obj in items:
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def wc(s: str) -> int:
    return len(re.findall(r"\b\w+\b", s or ""))


def compact_ws(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())

# ----------------------------
# Heuristics
# ----------------------------

RESULT_CUES = re.compile(
    r"\b(results?|conclusion|we (?:found|observed)|odds ratio|hazard ratio|95%\s*ci|p\s*[<≤]\s*0?\.0*\d|significant|primary endpoint|effect size|risk ratio|relative risk)\b",
    flags=re.IGNORECASE,
)

METHODS_ONLY_CUES = re.compile(
    r"\b(protocol|study design|methods?|trial design|feasibility study|pilot (?:study|trial)|we (?:aim|aimed)|objective[s]?\b|background)\b",
    flags=re.IGNORECASE,
)

PREFERRED_PUBTYPES = {
    "Randomized Controlled Trial",
    "Clinical Trial",
    "Systematic Review",
    "Meta-Analysis",
    "Practice Guideline",
    "Guideline",
    "Review",
}

# ----------------------------
# PubMed utilities
# ----------------------------

EFETCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
ELINK = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/elink.fcgi"
PMC_OA_BASE = "https://www.ncbi.nlm.nih.gov/pmc/oai/oai.cgi?verb=GetRecord&metadataPrefix=pmc&identifier=pmcid:"
PMC_OAI = "https://www.ncbi.nlm.nih.gov/pmc/oai/oai.cgi"
NCBI_TOOL = os.getenv("NCBI_TOOL", "rag-pipeline")
NCBI_EMAIL = os.getenv("NCBI_EMAIL", "eissa.islam@gmail.com")


def fetch_pubmed_xml(pmid: str) -> Optional[etree._Element]:
    try:
        r = requests.get(EFETCH, params={"db": "pubmed", "retmode": "xml", "id": pmid, "tool": NCBI_TOOL, "email": NCBI_EMAIL}, timeout=25)
        if r.status_code != 200:
            return None
        return etree.fromstring(r.content)
    except Exception:
        return None


def parse_pubmed_article(root: etree._Element) -> Dict[str, Any]:
    ns = {"ns": "http://www.ncbi.nlm.nih.gov/pubmed"}
    # PubMed XML varies; target key elements defensively
    article = root.find(".//ns:PubmedArticle", namespaces=ns)
    if article is None:
        article = root.find(".//PubmedArticle")
    data: Dict[str, Any] = {}

    # Title
    title_el = article.find(".//ArticleTitle") if article is not None else None
    data["title"] = compact_ws("" if title_el is None else "".join(title_el.itertext()))

    # Abstract
    abst_els = article.findall(".//Abstract/AbstractText") if article is not None else []
    abst_parts: List[str] = []
    for el in abst_els:
        label = el.get("Label") or el.get("NlmCategory") or ""
        txt = compact_ws("".join(el.itertext()))
        if label:
            abst_parts.append(f"{label}: {txt}")
        else:
            abst_parts.append(txt)
    data["abstract"] = "\n\n".join([p for p in abst_parts if p])

    # Journal, year
    journal_el = article.find(".//Journal/Title") if article is not None else None
    data["journal"] = compact_ws(journal_el.text if journal_el is not None else "")
    year_el = article.find(".//JournalIssue/PubDate/Year") if article is not None else None
    data["year"] = compact_ws(year_el.text if year_el is not None else "")

    # Publication types
    pubtypes = [compact_ws("".join(pt.itertext())) for pt in (article.findall(".//PublicationType") if article is not None else [])]
    data["pubtypes"] = [pt for pt in pubtypes if pt]
    return data


def elink_pmcid(pmid: str) -> Optional[str]:
    try:
        r = requests.get(ELINK, params={"dbfrom": "pubmed", "linkname": "pubmed_pmc", "id": pmid, "retmode": "xml", "tool": NCBI_TOOL, "email": NCBI_EMAIL}, timeout=20)
        if r.status_code != 200:
            return None
        root = etree.fromstring(r.content)
        # Find <LinkName>pubmed_pmc</LinkName> ... <Id>PMCxxxxx</Id>
        pmc_ids = root.findall(".//LinkSetDb[LinkName='pubmed_pmc']/Link/Id")
        for el in pmc_ids:
            return el.text  # e.g., PMC1234567
        return None
    except Exception:
        return None


def fetch_pmc_fulltext(pmcid: str) -> Optional[str]:
    try:
        # Use OAI-PMH to fetch JATS XML for OA articles
        pmcid_clean = pmcid.replace("PMC", "").strip()
        r = requests.get(PMC_OA_BASE + pmcid_clean, timeout=30)
        if r.status_code != 200:
            return None
        root = etree.fromstring(r.content)
        # Extract the body text from JATS
        body = root.find(".//{*}article/{*}body")
        if body is None:
            return None
        text_parts: List[str] = []
        for el in body.iter():
            if el.text and el.tag.endswith(("p", "sec")):
                text_parts.append(compact_ws(el.text))
        text = "\n\n".join([t for t in text_parts if t])
        return text if wc(text) > 100 else None
    except Exception:
        return None


def pmcid_to_pmid(pmcid: str) -> Optional[str]:
    """Map a PMCID to PMID using NCBI ELink (pmc -> pubmed)."""
    try:
        pmcid_clean = pmcid.upper().replace("PMC", "").strip()
        r = requests.get(ELINK, params={
            "dbfrom": "pmc",
            "linkname": "pmc_pubmed",
            "id": pmcid_clean,
            "retmode": "xml",
            "tool": NCBI_TOOL,
            "email": NCBI_EMAIL,
        }, timeout=20)
        if r.status_code != 200:
            return None
        root = etree.fromstring(r.content)
        ids = root.findall(".//LinkSetDb[LinkName='pmc_pubmed']/Link/Id")
        for el in ids:
            return (el.text or "").strip() or None
        return None
    except Exception:
        return None


def fetch_pmc_fulltext_by_pmcid(pmcid: str) -> Optional[str]:
    """Fetch PMC JATS full text via OAI-PMH GetRecord by PMCID and return concatenated body text."""
    try:
        pmcid_norm = pmcid.upper()
        if not pmcid_norm.startswith("PMC"):
            pmcid_norm = f"PMC{pmcid_norm}"
        params = {
            "verb": "GetRecord",
            "metadataPrefix": "pmc",
            "identifier": f"oai:pubmedcentral.nih.gov:{pmcid_norm}",
        }
        r = requests.get(PMC_OAI, params={**params, "tool": NCBI_TOOL, "email": NCBI_EMAIL}, timeout=30)
        if r.status_code != 200:
            return None
        root = etree.fromstring(r.content)
        body = root.find(".//{*}article/{*}body")
        if body is None:
            return None
        parts: List[str] = []
        for el in body.iter():
            if el.tag.endswith(("p", "sec")) and el.text:
                parts.append(compact_ws(el.text))
        text = "\n\n".join([t for t in parts if t])
        return text if wc(text) > 100 else None
    except Exception:
        return None

# ----------------------------
# ClinicalTrials.gov utilities
# ----------------------------

CTGOV_V2 = "https://clinicaltrials.gov/api/v2/studies/{nct}"


def fetch_ctgov(nct_id: str) -> Optional[Dict[str, Any]]:
    try:
        r = requests.get(CTGOV_V2.format(nct=nct_id), timeout=25)
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:
        return None


def assemble_ctgov_text(payload: Dict[str, Any]) -> Tuple[str, Dict[str, Any], bool]:
    try:
        study = (payload.get("studies") or [{}])[0]
        prot = study.get("protocolSection", {})
        desc = prot.get("descriptionModule", {})
        desi = prot.get("designModule", {})
        stat = prot.get("statusModule", {})
        conds = prot.get("conditionsModule", {})
        armsi = prot.get("armsInterventionsModule", {})
        results = study.get("resultsSection", {})

        title = desc.get("officialTitle") or desc.get("briefTitle") or ""
        brief = desc.get("briefSummary") or ""
        detailed = desc.get("detailedDescription") or ""
        phases = "; ".join(desi.get("phases", []) or [])
        stype = desi.get("studyType") or ""
        enroll = (desi.get("enrollmentInfo", {}) or {}).get("count", "")
        overall_status = stat.get("overallStatus") or ""
        start = (stat.get("startDateStruct", {}) or {}).get("date", "")
        complete = (stat.get("primaryCompletionDateStruct", {}) or {}).get("date", "")
        conditions = "; ".join(conds.get("conditions", []) or [])
        intervs = "; ".join([x.get("name", "") for x in (armsi.get("interventions", []) or []) if x.get("name")])

        has_results = bool(results)

        sections: List[Tuple[str, str]] = [
            ("Title", title),
            ("Summary", brief),
            ("Detailed Description", detailed),
        ]
        # If results exist, include top-line text if present
        primary_outcomes = []
        prim = (prot.get("outcomesModule", {}) or {}).get("primaryOutcomes", [])
        for p in prim:
            name = p.get("measure", "")
            desc = p.get("description", "")
            primary_outcomes.append(" - ".join([x for x in [name, desc] if x]))
        if primary_outcomes:
            sections.append(("Primary Outcomes", "\n".join(primary_outcomes)))

        key = "\n".join([
            f"Phase: {phases}",
            f"Study Type: {stype}",
            f"Enrollment: {enroll}",
            f"Status: {overall_status}",
            f"Start: {start}",
            f"Completion: {complete}",
            f"Conditions: {conditions}",
            f"Interventions: {intervs}",
        ])
        sections.append(("Key Fields", key))

        text = "\n\n".join([f"{t}\n{compact_ws(b)}" for t, b in sections if compact_ws(b)])
        meta = {
            "phase": phases,
            "study_type": stype,
            "enrollment": enroll,
            "overall_status": overall_status,
            "start_date": start,
            "completion_date": complete,
            "conditions": conditions,
            "interventions": intervs,
        }
        return text, meta, has_results
    except Exception:
        return "", {}, False

# ----------------------------
# Main processing
# ----------------------------

@dataclass
class Rec:
    raw: Dict[str, Any]

    @property
    def source(self) -> str:
        return str(self.raw.get("source") or self.raw.get("metadata", {}).get("source") or "").lower()

    @property
    def id(self) -> str:
        return str(self.raw.get("id") or "")


def accept_pubmed_abstract(text: str, pubtypes: List[str]) -> bool:
    if not text or wc(text) < 50:
        return False
    if RESULT_CUES.search(text):
        return True
    # If no result cues, but it is a guideline or meta-analysis, require Conclusion-like cues
    ptset = set(pubtypes)
    if ptset & {"Practice Guideline", "Guideline", "Systematic Review", "Meta-Analysis"}:
        return bool(re.search(r"\bconclusion[s]?\b|\bwe (?:recommend|suggest)\b", text, flags=re.IGNORECASE))
    return False


def accept_results_like_text(text: str) -> bool:
    """Heuristic acceptance for PMC full text when pubtypes are unknown."""
    if not text or wc(text) < 80:
        return False
    if RESULT_CUES.search(text):
        return True
    return bool(re.search(r"\bconclusion[s]?\b|\bwe (?:recommend|suggest)\b", text, flags=re.IGNORECASE))


def accept_guideline_text(text: str) -> bool:
    """Lightweight check that a guideline document contains recommendation-like cues."""
    if not text or wc(text) < 80:
        return False
    # Reject likely hub/index pages: many "Full text" mentions but no actionable cues
    fulltext_hits = len(re.findall(r"\bfull\s*text\b", text, flags=re.IGNORECASE))
    actionable = re.search(r"\b(we\s+recommend|we\s+suggest|should|recommend(?:ation)?s?\b[^:])", text, flags=re.IGNORECASE)
    if fulltext_hits >= 3 and not actionable:
        return False
    # Do not accept pages that merely say "guideline" without recommendations
    return bool(actionable)


def handle_pubmed(rec: Rec, fulltext: bool) -> Optional[Dict[str, Any]]:
    pmid = rec.id
    root = fetch_pubmed_xml(pmid)
    if root is None:
        return None
    data = parse_pubmed_article(root)
    abstract = data.get("abstract", "")
    title = data.get("title", rec.raw.get("title", ""))
    pubtypes = data.get("pubtypes", [])

    if not accept_pubmed_abstract(abstract, pubtypes):
        return None

    text = f"Title\n{title}\n\nAbstract\n{abstract}".strip()

    # Attempt PMC full text if requested
    if fulltext:
        pmcid = elink_pmcid(pmid)
        if pmcid:
            ft = fetch_pmc_fulltext(pmcid)
            if ft and wc(ft) > wc(abstract):
                text = f"Title\n{title}\n\nFull Text\n{ft}"

    out = {
        "id": pmid,
        "source": "pubmed",
        "title": title,
        "link": rec.raw.get("link") or f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
        "journal": data.get("journal", rec.raw.get("journal", "")),
        "year": data.get("year", rec.raw.get("year", "")),
        "text": text,
        "metadata": {
            **(rec.raw.get("metadata") or {}),
            "evidence_level": "published_article",
            "pubtypes": pubtypes,
            "has_results": True,
        },
    }
    return out


def handle_pmc(rec: Rec, fulltext: bool = True) -> Optional[Dict[str, Any]]:
    """Build a record for a PMC item. Prefer OA full text; else map to PMID and use abstract."""
    pmc = rec.id.upper()
    if not pmc.startswith("PMC"):
        pmc = f"PMC{pmc}"

    title = rec.raw.get("title") or ""
    link = rec.raw.get("link") or f"https://www.ncbi.nlm.nih.gov/pmc/articles/{pmc}"

    # Try full text via OAI-PMH first if requested
    if fulltext:
        ft = fetch_pmc_fulltext_by_pmcid(pmc)
        if ft and accept_results_like_text(ft):
            return {
                "id": pmc,
                "source": "pmc",
                "title": title,
                "link": link,
                "text": f"Title\n{title}\n\nFull Text\n{ft}".strip(),
                "metadata": {
                    **(rec.raw.get("metadata") or {}),
                    "evidence_level": "published_article",
                    "has_results": True,
                    "pmcid": pmc,
                },
            }

    # Fallback: map PMCID -> PMID and use PubMed abstract if acceptable
    pmid = pmcid_to_pmid(pmc)
    if pmid:
        root = fetch_pubmed_xml(pmid)
        if root is not None:
            data = parse_pubmed_article(root)
            abstract = data.get("abstract", "")
            ptitle = data.get("title", title)
            pubtypes = data.get("pubtypes", [])
            if accept_pubmed_abstract(abstract, pubtypes):
                return {
                    "id": pmc,
                    "source": "pmc",
                    "title": ptitle,
                    "link": link,
                    "journal": data.get("journal", rec.raw.get("journal", "")),
                    "year": data.get("year", rec.raw.get("year", "")),
                    "text": f"Title\n{ptitle}\n\nAbstract\n{abstract}".strip(),
                    "metadata": {
                        **(rec.raw.get("metadata") or {}),
                        "evidence_level": "published_article",
                        "has_results": True,
                        "pmcid": pmc,
                        "pmid": pmid,
                    },
                }
    return None


def handle_guideline(rec: Rec) -> Optional[Dict[str, Any]]:
    """Accept guideline items harvested from society websites if they contain recommendation cues."""
    text = (rec.raw.get("text") or "").strip()
    if not accept_guideline_text(text):
        return None
    title = rec.raw.get("title") or ""
    link = rec.raw.get("link") or rec.raw.get("url") or ""
    year = rec.raw.get("date") or ""
    society = rec.raw.get("society") or ""
    return {
        "id": rec.id,
        "source": "guidelines",
        "title": title,
        "link": link,
        "year": year,
        "text": text,
        "metadata": {
            **(rec.raw.get("metadata") or {}),
            "evidence_level": "guideline",
            "has_results": True,
            "society": society,
        },
    }
def handle_ctgov(rec: Rec) -> Optional[Dict[str, Any]]:
    nct = rec.id.upper()
    payload = fetch_ctgov(nct)
    if payload is None:
        return None
    text, extra_meta, has_results = assemble_ctgov_text(payload)
    if not has_results:
        return None  # drop protocol-only entries by default
    if wc(text) < 80:
        return None
    out = {
        "id": nct,
        "source": "clinicaltrials",
        "title": rec.raw.get("title") or "",
        "link": rec.raw.get("link") or f"https://clinicaltrials.gov/study/{nct}",
        "text": text,
        "metadata": {
            **(rec.raw.get("metadata") or {}),
            **extra_meta,
            "evidence_level": "registry_with_results",
            "has_results": True,
        },
    }
    return out


def process_stream(records: Iterator[Dict[str, Any]], allow_pubmed: bool, allow_trials: bool, fulltext: bool) -> Iterator[Dict[str, Any]]:
    for raw in records:
        r = Rec(raw)
        if r.source.startswith("pubmed") and allow_pubmed:
            out = handle_pubmed(r, fulltext=fulltext)
            if out:
                yield out
        elif r.source.startswith("pmc"):
            out = handle_pmc(r, fulltext=fulltext)
            if out:
                yield out
        elif r.source.startswith("guideline"):
            out = handle_guideline(r)
            if out:
                yield out
        elif ("clinicaltrials" in r.source or r.id.upper().startswith("NCT")) and allow_trials:
            out = handle_ctgov(r)
            if out:
                yield out
        else:
            # ignore other sources for now
            continue

# ----------------------------
# Entry point
# ----------------------------

def main():
    ap = argparse.ArgumentParser(description="Clean + filter + enrich PubMed/CTGov into decision-useful corpus")
    ap.add_argument("--in", dest="inp", required=True, help="Input JSONL/JSON from fetch step")
    ap.add_argument("--out", dest="out", required=True, help="Output JSONL path")
    ap.add_argument("--no-trials", action="store_true", help="Skip ClinicalTrials.gov")
    ap.add_argument("--no-pubmed", action="store_true", help="Skip PubMed")
    ap.add_argument("--fulltext", action="store_true", help="Try to pull PMC full text when available")
    args = ap.parse_args()

    inp = Path(args.inp)
    outp = Path(args.out)

    records = read_records(inp)
    items = process_stream(records, allow_pubmed=not args.no_pubmed, allow_trials=not args.no_trials, fulltext=args.fulltext)
    write_jsonl(items, outp)

if __name__ == "__main__":
    main()
