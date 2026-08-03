#!/usr/bin/env python3
"""
Fetch recent clinical guidelines across all major specialties from PubMed.

Strategy:
1. Search PubMed for "guideline" articles across all major specialties
2. Extract full text from source URLs (not PubMed — PubMed has CAPTCHA)
3. Chunk, embed, and upsert to ChromaDB

Usage:
  python3 fetch_cross_specialty_guidelines.py
"""
import json
import os
import re
import time
from typing import Dict, List, Optional

import requests
import xml.etree.ElementTree as ET
import yaml

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

REQUEST_DELAY = 2.0

# PubMed API key for rate-limited fetching (3 req/sec)
def _load_pubmed_api_key():
    """Load PubMed API key from settings.yaml."""
    try:
        settings_path = os.path.join(os.path.dirname(__file__), "settings.yaml")
        with open(settings_path, "r") as f:
            settings = yaml.safe_load(f)
        return settings.get("pubmed_api_key", "")
    except Exception:
        return ""
PUBMED_API_KEY = _load_pubmed_api_key()


PUBMED_API_KEY = _load_pubmed_api_key()

# Major clinical specialties and their guideline keywords
SPECIALTIES = {
    "cardiology": ["cardiology", "cardiovascular", "heart failure", "hypertension", "arrhythmia", "coronary", "myocardial infarction"],
    "pulmonology": ["pulmonary", "respiratory", "asthma", "COPD", "pulmonary embolism", "interstitial lung disease"],
    "neurology": ["neurology", "stroke", "epilepsy", "multiple sclerosis", "dementia", "Alzheimer"],
    "oncology": ["oncology", "cancer", "tumor", "chemotherapy", "radiotherapy", "breast cancer", "lung cancer"],
    "endocrinology": ["endocrinology", "diabetes", "thyroid", "adrenal", "parathyroid", "osteoporosis"],
    "gastroenterology": ["gastroenterology", "hepatology", "liver", "inflammatory bowel disease", "IBD", "celiac"],
    "infectious_disease": ["infectious disease", "antimicrobial", "antibiotic", "HIV", "Hepatitis", "tuberculosis"],
    "hematology": ["hematology", "anemia", "coagulation", "bleeding", "thrombosis", "lymphoma"],
    "rheumatology": ["rheumatology", "arthritis", "lupus", "scleroderma", "vasculitis", "gout"],
    "urology": ["urology", "kidney", "bladder", "prostate", "urinary", "renal"],
    "psychiatry": ["psychiatry", "depression", "anxiety", "schizophrenia", "bipolar", "PTSD"],
    "dermatology": ["dermatology", "skin", "eczema", "psoriasis", "melanoma", "acne"],
    "ophthalmology": ["ophthalmology", "eye", "glaucoma", "cataract", "retina", "macular"],
    "otolaryngology": ["otolaryngology", "ENT", "ear", "nose", "throat", "sinus", "hearing"],
    "radiology": ["radiology", "imaging", "MRI", "CT", "ultrasound", "mammography"],
    "emergency_medicine": ["emergency medicine", "trauma", "resuscitation", "acute care"],
    "surgery": ["surgery", "surgical", "operative", "laparoscopic", "minimally invasive"],
    "pediatrics": ["pediatrics", "childhood", "neonatal", "infant", "adolescent"],
    "geriatrics": ["geriatrics", "elderly", "aging", "frailty", "dementia"],
    "critical_care": ["critical care", "ICU", "mechanical ventilation", "sepsis", "shock"],
}

def search_pubmed_guidelines(specialty: str, keywords: List[str], max_results: int = 50) -> List[Dict]:
    """Search PubMed for recent guidelines in a specialty using API key."""
    print(f"Searching PubMed for {specialty} guidelines...")
    
    # Build query: guideline + specialty keywords + recent years
    kw_part = " OR ".join([f'"{kw}"' for kw in keywords])
    query = f"({kw_part}) AND (guideline[Publication Type] OR 'practice guideline'[Title/Abstract] OR consensus[Title/Abstract]) AND (2020:3000[pdate])"
    
    # Search PubMed with API key
    try:
        params = {
            "db": "pubmed",
            "term": query,
            "retmax": max_results,
            "retmode": "json",
            "sort": "relevance",
        }
        if PUBMED_API_KEY:
            params["api_key"] = PUBMED_API_KEY
        
        print(f"  Query: {query[:100]}...")
        print(f"  API key present: {bool(PUBMED_API_KEY)}")
        
        resp = requests.get(
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
            params=params,
            headers=HEADERS,
            timeout=30
        )
        print(f"  Status: {resp.status_code}")
        print(f"  Response length: {len(resp.text)}")
        if resp.status_code != 200:
            print(f"  Error response: {resp.text[:200]}")
        resp.raise_for_status()
        data = resp.json()
        
        pmids = data.get("esearchresult", {}).get("idlist", [])
        print(f"  Found {len(pmids)} guidelines")
        
        # Rate limit: wait between requests
        time.sleep(0.33)  # ~3 req/sec with API key
        
        # Fetch full records using batch EFetch (XML mode)
        guidelines = []
        if pmids:
            # Batch EFetch: 20 PMIDs per request
            batch_size = 20
            for i in range(0, len(pmids), batch_size):
                batch = pmids[i:i+batch_size]
                ids = ",".join(batch)
                
                fetch_params = {
                    "db": "pubmed",
                    "id": ids,
                    "retmode": "xml",
                    "rettype": "abstract",
                }
                if PUBMED_API_KEY:
                    fetch_params["api_key"] = PUBMED_API_KEY
                
                resp = requests.get(
                    "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi",
                    params=fetch_params,
                    headers=HEADERS,
                    timeout=30
                )
                resp.raise_for_status()
                
                # Parse XML response
                root = ET.fromstring(resp.text)
                
                for article in root.findall(".//PubmedArticle"):
                    pmid_el = article.find(".//PMID")
                    title_el = article.find(".//ArticleTitle")
                    abstract_el = article.find(".//AbstractText")
                    journal_el = article.find(".//Journal/Title")
                    pub_date_el = article.find(".//Journal/JournalIssue/PubDate")
                    
                    pmid = pmid_el.text if pmid_el is not None else ""
                    title = title_el.text if title_el is not None else ""
                    abstract = abstract_el.text if abstract_el is not None else ""
                    journal = journal_el.text if journal_el is not None else ""
                    pub_date_el = article.find(".//Journal/JournalIssue/PubDate/Year")
                    pub_date = pub_date_el.text if pub_date_el is not None else ""
                    
                    # Year is already extracted from PubDate/Year
                    year = pub_date
                    
                    guidelines.append({
                        "pmid": pmid,
                        "title": title,
                        "abstract": abstract,
                        "journal": journal,
                        "year": year,
                        "source": "PubMed",
                        "specialty": specialty,
                        "source_url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                    })
                
                # Rate limit between batches
                time.sleep(0.33)
        
        return guidelines
        
    except Exception as e:
        print(f"  Error: {e}")
        return []


def fetch_full_text(url: str) -> Optional[str]:
    """Fetch full text from a URL using trafilatura."""
    import trafilatura
    try:
        resp = requests.get(url, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        text = trafilatura.extract(resp.text, url=url, include_comments=False, include_tables=True)
        if text and len(text.strip()) > 200:
            return text[:200000]
        return None
    except Exception:
        return None


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--specialty", default="all", help="Specialty to fetch (or 'all')")
    parser.add_argument("--output", default="./raw_docs/cross_specialty_guidelines.jsonl", help="Output JSONL file")
    parser.add_argument("--max", type=int, default=20, help="Max guidelines per specialty")
    args = parser.parse_args()
    
    all_guidelines = []
    
    if args.specialty == "all":
        specialties = SPECIALTIES
    else:
        specialties = {args.specialty: SPECIALTIES.get(args.specialty, [])}
    
    for specialty, keywords in specialties.items():
        guidelines = search_pubmed_guidelines(specialty, keywords, max_results=args.max)
        all_guidelines.extend(guidelines)
        print(f"  {specialty}: {len(guidelines)} guidelines")
    
    # Save to JSONL
    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        for g in all_guidelines:
            f.write(json.dumps(g, ensure_ascii=False) + "\n")
    
    print(f"\nTotal: {len(all_guidelines)} guidelines")
    print(f"Saved to {args.output}")


if __name__ == "__main__":
    main()