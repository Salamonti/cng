#!/usr/bin/env python3
"""
metadata_enricher.py - Extract and enrich metadata for ChromaDB chunks.

Usage:
  python3 metadata_enricher.py                    # Dry run
  python3 metadata_enricher.py --enrich           # Enrich and upsert
"""
import re
import sys
import os
import time
from collections import defaultdict

sys.path.insert(0, os.path.dirname(__file__))

import yaml
import chromadb
from chromadb import Settings

SOURCE_SPECIALTY_MAP = {
    "acc/aha": "cardiology", "acc": "cardiology", "aha": "cardiology",
    "esc": "cardiology", "ccs": "cardiology", "hypertension canada": "cardiology",
    "gold": "pulmonology", "gina": "pulmonology", "ats": "pulmonology",
    "ers": "pulmonology", "chest": "pulmonology", "thoracic": "pulmonology",
    "gin": "gastroenterology", "acg": "gastroenterology", "cag": "gastroenterology",
    "aaos": "orthopedics", "ean": "neurology", "aan": "neurology",
    "who": "general", "nice": "general",
    "asco": "oncology", "esmo": "oncology", "nccn": "oncology",
    "idsa": "infectious_disease", "cdc": "infectious_disease",
    "ada": "endocrinology", "endocrine society": "endocrinology",
    "acr": "rheumatology", "eular": "rheumatology",
    "aap": "pediatrics", "cps": "pediatrics", "apa": "psychiatry",
    "kdigo": "nephrology", "auanet": "urology", "aua": "urology",
    "car": "radiology", "caep": "emergency_medicine",
    "sccm": "critical_care", "asa": "anesthesiology",
    "aafp": "primary_care", "cfpc": "primary_care",
    "acog": "obgyn", "sogc": "obgyn",
}

SPECIALTY_KEYWORDS = {
    "cardiology": ["cardiac", "heart", "myocardial", "coronary", "angina", "arrhythmia",
        "atrial fibrillation", "heart failure", "valvular", "cardiomyopathy",
        "hypertension", "cardiovascular", "atherosclerosis", "dyslipidemia",
        "cholesterol", "cardiologist", "cardiology", "ejection fraction",
        "angioplasty", "stent", "revascularization", "pacemaker", "defibrillator"],
    "pulmonology": ["pulmonary", "lung", "COPD", "asthma", "ILD", "interstitial lung",
        "fibrosis", "pneumonia", "respiratory", "bronchial", "bronchiectasis",
        "emphysema", "pulmonary embolism", "sleep apnea", "bronchoscopy",
        "spirometry", "hypoxemia", "ARDS", "pleural", "pneumothorax",
        "tuberculosis", "cystic fibrosis", "pulmonary hypertension"],
    "gastroenterology": ["gastrointestinal", "GI", "liver", "hepatic", "cirrhosis",
        "hepatitis", "IBD", "inflammatory bowel", "Crohn", "ulcerative colitis",
        "colon", "colorectal", "colonoscopy", "endoscopy", "gastroscopy",
        "GERD", "reflux", "esophageal", "gastric", "peptic ulcer",
        "pancreatic", "pancreatitis", "biliary", "gallbladder", "celiac",
        "malabsorption", "gastroenterologist"],
    "orthopedics": ["orthopedic", "bone", "joint", "fracture", "arthritis",
        "osteoarthritis", "meniscus", "ACL", "rotator cuff", "tendon",
        "ligament", "osteoporosis", "spine", "lumbar", "cervical",
        "arthroplasty", "total joint", "replacement", "scoliosis",
        "degenerative disc", "herniated disc", "carpal tunnel", "musculoskeletal"],
    "neurology": ["neurologic", "neurological", "neurology", "neurologist",
        "stroke", "CVA", "TIA", "seizure", "epilepsy", "migraine",
        "headache", "dementia", "Alzheimer", "Parkinson", "ALS",
        "multiple sclerosis", "MS", "neuropathy", "brain", "cerebral",
        "spinal cord", "neurodegenerative", "encephalopathy", "meningitis"],
    "endocrinology": ["diabetes", "insulin", "thyroid", "hypothyroid",
        "hyperthyroid", "parathyroid", "adrenal", "pituitary", "hormone",
        "endocrine", "glucose", "HbA1c", "glycemic", "obesity",
        "metabolic syndrome", "PCOS", "osteoporosis", "calcium",
        "vitamin D", "growth hormone", "cushing", "addison", "endocrinologist"],
    "infectious_disease": ["infection", "antibiotic", "antimicrobial", "sepsis",
        "bacterial", "viral", "fungal", "parasitic", "HIV", "AIDS",
        "hepatitis", "tuberculosis", "malaria", "influenza", "pneumonia",
        "UTI", "MRSA", "Clostridium", "resistant", "prophylaxis",
        "vaccination", "immunization", "bacteremia", "infectious disease", "IDSA"],
    "rheumatology": ["rheumatoid arthritis", "RA", "lupus", "SLE",
        "systemic lupus", "scleroderma", "vasculitis", "gout",
        "ankylosing spondylitis", "fibromyalgia", "autoimmune",
        "collagen vascular", "rheumatologic", "inflammatory arthritis",
        "ANA", "anti-CCP", "DMARD", "rheumatologist"],
    "psychiatry": ["depression", "anxiety", "bipolar", "schizophrenia",
        "psychosis", "PTSD", "ADHD", "OCD", "obsessive compulsive",
        "eating disorder", "anorexia", "bulimia", "substance abuse",
        "addiction", "suicide", "suicidal", "antidepressant", "antipsychotic",
        "mood disorder", "psychiatric", "mental health"],
    "pediatrics": ["pediatric", "child", "infant", "neonate", "newborn",
        "adolescent", "developmental", "vaccination", "growth chart",
        "failure to thrive", "congenital", "birth defect", "APGAR",
        "well-child", "childhood", "juvenile", "pediatric dosing"],
    "nephrology": ["kidney", "renal", "nephropathy", "CKD",
        "chronic kidney disease", "dialysis", "hemodialysis", "peritoneal",
        "glomerulonephritis", "proteinuria", "nephrotic", "AKI",
        "acute kidney injury", "transplant", "urinalysis",
        "creatinine", "eGFR", "uremia", "nephrologist"],
    "urology": ["urinary", "bladder", "prostate", "kidney stone",
        "nephrolithiasis", "BPH", "benign prostatic", "urinary tract",
        "catheter", "incontinence", "overactive bladder",
        "renal cell carcinoma", "urothelial", "urologic", "urologist"],
    "radiology": ["imaging", "CT scan", "MRI", "ultrasound", "X-ray",
        "radiograph", "mammography", "PET scan", "nuclear medicine",
        "fluoroscopy", "contrast", "radiological", "diagnostic imaging",
        "interventional radiology", "radiation dose", "DICOM", "PACS", "radiologist"],
    "emergency_medicine": ["emergency", "ED", "ER", "acute care", "trauma",
        "resuscitation", "cardiac arrest", "ABC", "airway", "triage",
        "emergency department", "prehospital", "paramedic", "ALS",
        "BLS", "advanced life support", "acute chest pain", "sepsis protocol"],
    "critical_care": ["ICU", "intensive care", "mechanical ventilation",
        "ventilator", "sedation", "vasopressor", "inotrope",
        "hemodynamic", "CRRT", "ECMO", "extracorporeal",
        "tracheostomy", "central line", "arterial line",
        "SIRS", "qSOFA", "SOFA score", "septic shock", "critical care"],
    "obgyn": ["pregnancy", "obstetric", "obstetrics", "gynecologic",
        "gynecology", "prenatal", "postpartum", "gestational",
        "preeclampsia", "ectopic", "miscarriage", "cesarean",
        "labor", "delivery", "fetal", "placenta", "ovarian",
        "endometriosis", "fibroid", "uterine", "menopause", "contraception", "OBGYN"],
    "dermatology": ["skin", "dermatitis", "eczema", "psoriasis", "acne",
        "melanoma", "basal cell", "squamous cell", "contact dermatitis",
        "urticaria", "hives", "alopecia", "vitiligo", "rosacea",
        "tinea", "cellulitis", "erythema", "dermatologic", "cutaneous", "dermatologist"],
    "ophthalmology": ["eye", "vision", "retina", "macular", "glaucoma",
        "cataract", "diabetic retinopathy", "optic nerve", "cornea",
        "conjunctivitis", "dry eye", "strabismus", "amblyopia",
        "intraocular", "ophthalmic", "visual acuity", "fundus", "LASIK", "ophthalmologist"],
    "primary_care": ["primary care", "family medicine", "general practice",
        "wellness", "preventive", "health maintenance", "annual physical",
        "vital signs", "health risk assessment", "chronic disease management"],
}

def build_doc_id_to_year_map():
    """Build mapping from doc_id to year by scanning chunk files."""
    import glob
    import json
    import os
    doc_id_to_year = {}
    chunks_dir = os.path.join(os.path.dirname(__file__), "chunks")
    if not os.path.exists(chunks_dir):
        return doc_id_to_year
    
    for f in glob.glob(os.path.join(chunks_dir, "*.jsonl")):
        basename = os.path.basename(f)
        # Extract year from filename: guidelines_20260408_165607.processed.chunks.jsonl
        year_match = re.search(r'guidelines_(20\d{4})', basename)
        if not year_match:
            continue
        year = year_match.group(1)[:4]  # Just the year part (e.g., "2026")
        
        # Read first line to get doc_id
        try:
            with open(f, 'r') as fh:
                first_line = fh.readline().strip()
                if first_line:
                    data = json.loads(first_line)
                    meta = data.get("metadata", {})
                    doc_id = meta.get("doc_id", "")
                    if doc_id:
                        doc_id_to_year[doc_id] = year
        except Exception:
            pass
    
    return doc_id_to_year


# Build the mapping once at module level
_DOC_ID_TO_YEAR = build_doc_id_to_year_map()

DOI_PATTERN = re.compile(r'10[.]\d{4,9}/[-._;()/:A-Z0-9]+', re.IGNORECASE)

GRADE_PATTERNS = [
    # Strong recommendations
    (r'(?:strong|grade[ ]*a|level[ ]*a|high[- ]?quality)[ ]*(?:recommendation|evidence)', 'strong', 'high'),
    (r'(?:strong|grade[ ]*a)[ ]*recommendation.*?(?:moderate|moderately)', 'strong', 'moderate'),
    (r'(?:strong|grade[ ]*a)[ ]*recommendation.*?(?:low|very[ ]*low)', 'strong', 'low'),
    (r'we\s+recommend\s+(?:that|against|for|in|to)', 'strong', 'moderate'),
    (r'recommendation\s+strength\s*:\s*strong', 'strong', 'moderate'),
    (r'recommend\s+(?:against|for|that|in|to)', 'strong', 'moderate'),
    # Conditional/weak recommendations
    (r'(?:conditional|weak|grade[ ]*b|level[ ]*b)[ ]*(?:recommendation|evidence).*?(?:high|strong)', 'conditional', 'high'),
    (r'(?:conditional|weak|grade[ ]*b|level[ ]*b)[ ]*(?:recommendation|evidence)', 'conditional', 'moderate'),
    (r'we\s+suggest\s+(?:that|against|for|in|to)', 'conditional', 'moderate'),
    (r'recommendation\s+strength\s*:\s*(?:conditional|weak)', 'conditional', 'moderate'),
    (r'suggest\s+(?:that|against|for|in|to)', 'conditional', 'moderate'),
    # Low quality evidence
    (r'(?:grade[ ]*c|level[ ]*c|very[ ]*low)', 'conditional', 'low'),
    (r'low[- ]?quality\s+evidence', 'conditional', 'low'),
    # Expert opinion
    (r'(?:expert[ ]*opinion|consensus[ ]*opinion|good[ ]*practice[ ]*statement|grade[ ]*d)', 'expert_opinion', 'very_low'),
    # Evidence level indicators
    (r'evidence\s+level\s*:\s*[iI]{1,3}', 'strong', 'high'),
    (r'evidence\s+level\s*:\s*[iI]{1,3}[abc]', 'strong', 'moderate'),
    (r'evidence\s+level\s*:\s*[iI]{1,3}[abc][123]', 'conditional', 'moderate'),
    # General evidence quality indicators
    (r'moderate[- ]?quality\s+evidence', 'conditional', 'moderate'),
    (r'high[- ]?quality\s+evidence', 'strong', 'high'),
    (r'very\s+low[- ]?quality\s+evidence', 'conditional', 'low'),
    # Recommendation grade indicators
    (r'grade\s*[ABCDEF]', 'conditional', 'moderate'),
    (r'level\s*[IVX]{1,3}', 'conditional', 'moderate'),
]


def infer_specialty_from_source(source):
    if not source:
        return None
    src_lower = source.lower().strip()
    for key, specialty in SOURCE_SPECIALTY_MAP.items():
        if key in src_lower or src_lower in key:
            return specialty
    return None


def infer_specialty_from_text(title, text):
    combined = (title + " " + text[:2000]).lower()
    scores = defaultdict(int)
    for specialty, keywords in SPECIALTY_KEYWORDS.items():
        for kw in keywords:
            if kw in combined:
                scores[specialty] += 1
    if not scores:
        return None
    best = max(scores.items(), key=lambda x: x[1])
    if best[1] >= 2:
        return best[0]
    return None


def is_valid_year(val):
    """Check if a value is a valid 4-digit year (2000-2030)."""
    if not val:
        return False
    val = str(val).strip()
    # Must be exactly 4 digits
    if len(val) != 4:
        return False
    if not val.isdigit():
        return False
    year = int(val)
    return 2000 <= year <= 2030


def extract_year(title, text, doc_id=""):
    # First try title
    match = re.search(r'(20[0-9]{3})', title)
    if match:
        year = match.group(1)
        if is_valid_year(year):
            return year
    # Then try doc_id mapping from filenames
    if doc_id and doc_id in _DOC_ID_TO_YEAR:
        candidate = _DOC_ID_TO_YEAR[doc_id]
        if is_valid_year(candidate):
            return candidate
    # Then try doc_id directly
    match = re.search(r'(20[0-9]{3})', doc_id)
    if match:
        year = match.group(1)
        if is_valid_year(year):
            return year
    # Look for publication year patterns in text
    # "Published: 2023", "Copyright © 2022", "© 2021"
    pub_match = re.search(r'(?:published|copyright|©|date|year|edition|version)[:\s]+(20[0-9]{3})', text[:1000], re.IGNORECASE)
    if pub_match:
        year = pub_match.group(1)
        if is_valid_year(year):
            return year
    # Look for guideline year in title/text: "2023 Guidelines", "Guidelines 2022"
    guide_match = re.search(r'(20[0-9]{3})\s+(?:guideline|recommendation|consensus|statement|update|report|position)', text[:500], re.IGNORECASE)
    if guide_match:
        year = guide_match.group(1)
        if is_valid_year(year):
            return year
    # Finally try text — but only if it looks like a standalone year
    match = re.search(r'(?:^|\s)(20[0-9]{3})(?:\s|$|[,;])', text[:500])
    if match:
        year = match.group(1)
        if is_valid_year(year):
            return year
    return None


def extract_doi(text):
    match = DOI_PATTERN.search(text)
    return match.group(0) if match else None


def extract_grade(text):
    text_lower = text.lower()
    for pattern, strength, quality in GRADE_PATTERNS:
        if re.search(pattern, text_lower):
            return strength, quality
    return None, None


def enrich_chunk(text, metadata):
    enriched = dict(metadata) if metadata else {}
    source = str(enriched.get("source") or enriched.get("society") or "").strip()
    title = str(enriched.get("title") or "").strip()
    existing_specialty = str(enriched.get("specialty") or "").strip()
    existing_year = str(enriched.get("year") or enriched.get("guideline_year") or enriched.get("timestamp") or enriched.get("date") or "").strip()
    doc_id = str(enriched.get("doc_id") or "").strip()

    if not existing_specialty:
        specialty = infer_specialty_from_source(source)
        if not specialty:
            specialty = infer_specialty_from_text(title, text)
        if not specialty:
            specialty = "general"
        enriched["specialty"] = specialty

    # Validate existing year — reject invalid values (ISO timestamps, garbage numbers)
    if not existing_year or not is_valid_year(existing_year):
        # Clear invalid year values — ChromaDB upsert keeps old values if key is missing,
        # so we must set to "N/A" to clear
        if existing_year and not is_valid_year(existing_year):
            enriched["year"] = "N/A"
        year = extract_year(title, text, doc_id)
        if year:
            enriched["year"] = year

    if not enriched.get("doi"):
        doi = extract_doi(text)
        if doi:
            enriched["doi"] = doi

    if not enriched.get("grade") and not enriched.get("evidence_level"):
        strength, quality = extract_grade(text)
        if strength:
            enriched["grade"] = strength
            enriched["evidence_level"] = quality

    return enriched


def get_chroma_collection():
    with open("settings.yaml", "r") as f:
        cfg = yaml.safe_load(f)
    client = chromadb.PersistentClient(
        path=cfg["persist_directory"],
        settings=Settings(anonymized_telemetry=False)
    )
    return client.get_collection(name=cfg.get("collection_name", "medical_rag"))


def analyze_metadata(col):
    total = col.count()
    print("Total chunks:", total)
    stats = {"total": total, "empty_specialty": 0, "empty_year": 0,
             "empty_doi": 0, "empty_grade": 0,
             "specialties": defaultdict(int), "years": defaultdict(int),
             "sources": defaultdict(int)}

    for offset in range(0, total, 5000):
        batch = col.get(limit=5000, offset=offset, include=["metadatas", "documents"])
        for meta, doc in zip(batch["metadatas"], batch["documents"]):
            if not meta:
                stats["empty_specialty"] += 1
                stats["empty_year"] += 1
                stats["empty_doi"] += 1
                stats["empty_grade"] += 1
                continue
            spec = str(meta.get("specialty", "")).strip()
            year = str(meta.get("year", "") or meta.get("guideline_year", "") or meta.get("timestamp", "") or meta.get("date", "")).strip()
            doi = str(meta.get("doi", "")).strip()
            grade = str(meta.get("grade", "") or meta.get("evidence_level", "")).strip()
            source = str(meta.get("source", "") or meta.get("society", "")).strip()
            if not spec:
                stats["empty_specialty"] += 1
            else:
                stats["specialties"][spec] += 1
            if not year:
                stats["empty_year"] += 1
            else:
                stats["years"][year] += 1
            if not doi:
                stats["empty_doi"] += 1
            if not grade:
                stats["empty_grade"] += 1
            if source:
                stats["sources"][source] += 1

    print()
    print("=== Current Metadata State ===")
    print("Empty specialty:", stats["empty_specialty"], "/", total)
    print("Empty year:", stats["empty_year"], "/", total)
    print("Empty DOI:", stats["empty_doi"], "/", total)
    print("Empty GRADE:", stats["empty_grade"], "/", total)
    print()
    print("Sources (top 20):")
    for src, cnt in sorted(stats["sources"].items(), key=lambda x: -x[1])[:20]:
        print("  %s: %d" % (src, cnt))
    if stats["specialties"]:
        print()
        print("Specialties with data:")
        for sp, cnt in sorted(stats["specialties"].items(), key=lambda x: -x[1]):
            print("  %s: %d" % (sp, cnt))
    if stats["years"]:
        print()
        print("Years (top 10):")
        for yr, cnt in sorted(stats["years"].items(), key=lambda x: -x[1])[:10]:
            print("  %s: %d" % (yr, cnt))
    return stats


def enrich_and_upsert(col, batch_size=1000):
    total = col.count()
    print("Enriching %d chunks in batches of %d..." % (total, batch_size))
    results = {"total": total, "enriched_specialty": 0, "enriched_year": 0,
               "enriched_doi": 0, "enriched_grade": 0, "unchanged": 0, "errors": 0}

    for offset in range(0, total, 5000):
        batch = col.get(limit=5000, offset=offset, include=["metadatas", "documents"])
        ids = batch["ids"]
        docs = batch["documents"]
        new_metas = []

        for mid, meta, doc in zip(ids, batch["metadatas"], batch["documents"]):
            old_spec = str((meta or {}).get("specialty", "")).strip()
            old_year = str((meta or {}).get("year", "") or (meta or {}).get("guideline_year", "") or (meta or {}).get("timestamp", "") or (meta or {}).get("date", "")).strip()
            old_doi = str((meta or {}).get("doi", "")).strip()
            old_grade = str((meta or {}).get("grade", "") or (meta or {}).get("evidence_level", "")).strip()

            enriched = enrich_chunk(doc or "", meta)

            new_spec = str(enriched.get("specialty", "")).strip()
            new_year = str(enriched.get("year", "")).strip()
            new_doi = str(enriched.get("doi", "")).strip()
            new_grade = str(enriched.get("grade", "") or enriched.get("evidence_level", "")).strip()

            changed = False
            if new_spec and not old_spec:
                results["enriched_specialty"] += 1
                changed = True
            if new_year and not old_year:
                results["enriched_year"] += 1
                changed = True
            if new_doi and not old_doi:
                results["enriched_doi"] += 1
                changed = True
            if new_grade and not old_grade:
                results["enriched_grade"] += 1
                changed = True
            if not changed:
                results["unchanged"] += 1
            new_metas.append(enriched)

        # Upsert with existing embeddings to avoid re-embedding (OOM risk)
        try:
            batch_with_emb = col.get(ids=ids, include=["embeddings"])
            existing_embs = batch_with_emb.get("embeddings")
            col.upsert(ids=ids, documents=docs, metadatas=new_metas, embeddings=existing_embs)
        except Exception as e:
            print("  ERROR upserting batch at offset %d: %s" % (offset, e))
            results["errors"] += 1

        processed = min(offset + 5000, total)
        pct = processed / total * 100
        print("  Processed %d/%d (%.1f%%)" % (processed, total, pct))

    print()
    print("=== Enrichment Results ===")
    for k, v in results.items():
        print("  %s: %d" % (k, v))
    return results


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Enrich ChromaDB chunk metadata")
    parser.add_argument("--enrich", action="store_true", help="Enrich and upsert")
    parser.add_argument("--batch", type=int, default=1000, help="Batch size")
    args = parser.parse_args()

    col = get_chroma_collection()
    print("=== Analyzing current metadata ===")
    analyze_metadata(col)

    if args.enrich:
        print()
        print("=== Starting enrichment ===")
        start = time.time()
        results = enrich_and_upsert(col, batch_size=args.batch)
        elapsed = time.time() - start
        print()
        print("Completed in %.1fs (%.1f min)" % (elapsed, elapsed/60))
        print()
        print("=== Post-enrichment verification ===")
        analyze_metadata(col)
    else:
        print()
        print("Dry run complete. Use --enrich to apply changes.")


if __name__ == "__main__":
    main()
