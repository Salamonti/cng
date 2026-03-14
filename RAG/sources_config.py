# sources_config.py
"""
RAG Pipeline: Medical Sources and Filters Config

This module defines the scope of the medical knowledge base for ingestion and retrieval.
It exports a single function `get_config()` that returns a dictionary, and can also be
executed directly to write a `sources_config.yaml` file next to this script.

Usage in your code:

    from sources_config import get_config
    CFG = get_config()
    domains = CFG["domains"]
    sources = CFG["trusted_sources"]
    include = CFG["filters"]["include"]
    exclude = CFG["filters"]["exclude"]

Run as a script to generate YAML:

    python sources_config.py  # writes ./sources_config.yaml

"""
from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional  # noqa: F401
import pathlib
import sys  # noqa: F401
import yaml

# -----------------------
# Core domain definitions
# -----------------------
SUPPORTED_DOMAINS: List[str] = [
    # Core
    "cardiology",
    "pulmonology",
    "endocrinology",
    "infectious_diseases",
    "nephrology",
    "gastroenterology",
    "hematology",
    "oncology",
    "neurology",
    "rheumatology",
    "geriatrics",
    "emergency_medicine",
    # Cross-cutting
    "critical_care",
    "primary_care",
    "pharmacology",
    "diagnostics",
]

# -----------------------
# Trustworthy source list
# -----------------------
@dataclass(frozen=True)
class Source:
    id: str
    name: str
    base_url: str
    type: str  # "guideline" | "registry" | "literature" | "drugdb" | "regulatory"
    notes: str = ""

TRUSTED_SOURCES: List[Source] = [
    Source(id="pubmed", name="PubMed", base_url="https://pubmed.ncbi.nlm.nih.gov/", type="literature"),
    Source(id="who_iris", name="WHO IRIS", base_url="https://iris.who.int/", type="literature"),
    Source(id="clinicaltrials", name="ClinicalTrials.gov", base_url="https://clinicaltrials.gov/", type="registry"),
    Source(id="drugbank", name="DrugBank", base_url="https://go.drugbank.com/", type="drugdb"),
    Source(id="openfda", name="OpenFDA", base_url="https://open.fda.gov/", type="regulatory"),
    Source(id="nice", name="NICE Guidance", base_url="https://www.nice.org.uk/guidance", type="guideline"),
    Source(id="ada", name="American Diabetes Association", base_url="https://diabetes.org/", type="guideline"),
    Source(id="acc", name="American College of Cardiology", base_url="https://www.acc.org/", type="guideline"),
]

# ---------------------------------
# Inclusion / exclusion query filters
# ---------------------------------
# Global inclusion MeSH terms and keywords frequently useful across internal medicine
GLOBAL_MESH_TERMS: List[str] = [
    "Practice Guidelines as Topic",
    "Meta-Analysis as Topic",
    "Randomized Controlled Trials as Topic",
    "Systematic Reviews as Topic",
    "Drug-Related Side Effects and Adverse Reactions",
]

GLOBAL_KEYWORDS: List[str] = [
    # outcomes & evidence strength
    "guideline", "consensus", "systematic review", "meta-analysis",
    "randomized", "controlled", "cohort", "case-control",
    # diagnostics
    "sensitivity", "specificity", "likelihood ratio", "predictive value",
    # therapeutics
    "first-line", "second-line", "contraindicated", "dose", "dosing", "renal dose",
]

# Domain-specific adds to inclusion filters
DOMAIN_FILTERS_INCLUDE: Dict[str, Dict[str, List[str]]] = {
    "cardiology": {
        "mesh": ["Myocardial Infarction", "Heart Failure", "Atrial Fibrillation", "Hypertension"],
        "keywords": [
            "NSTEMI", "STEMI", "troponin", "PCI", "stent", "ACE inhibitor", "beta-blocker",
            "ACC/AHA guideline", "statin intensity", "HFpEF", "HFrEF",
        ],
    },
    "pulmonology": {
        "mesh": ["Asthma", "Pulmonary Disease, Chronic Obstructive", "Pulmonary Embolism", "Interstitial Lung Diseases"],
        "keywords": ["FEV1", "GOLD guideline", "oxygen therapy", "DLCO", "VTE", "anticoagulation"],
    },
    "endocrinology": {
        "mesh": ["Diabetes Mellitus, Type 2", "Hypothyroidism", "Hyperthyroidism", "Obesity"],
        "keywords": ["A1C target", "ADA Standards of Care", "SGLT2", "GLP-1", "DKA", "insulin titration"],
    },
    "infectious_diseases": {
        "mesh": ["Anti-Bacterial Agents", "COVID-19", "Bacteremia", "Sepsis"],
        "keywords": ["IDSA guideline", "antimicrobial stewardship", "MRSA", "C. difficile", "pip-tazo", "ceftriaxone"],
    },
    "nephrology": {
        "mesh": ["Chronic Kidney Disease", "Acute Kidney Injury", "Renal Dialysis"],
        "keywords": ["eGFR", "albuminuria", "KDIGO", "renal replacement therapy", "hyperkalemia"],
    },
    "gastroenterology": {
        "mesh": ["Cirrhosis", "Gastrointestinal Hemorrhage", "Inflammatory Bowel Diseases"],
        "keywords": ["MELD", "portal hypertension", "UGIB", "H. pylori", "pancreatitis"],
    },
    "hematology": {
        "mesh": ["Anemia", "Thrombocytopenia", "Venous Thromboembolism"],
        "keywords": ["anticoagulation", "DOAC", "warfarin", "HIT", "transfusion threshold"],
    },
    "oncology": {
        "mesh": ["Neoplasms", "Chemotherapy, Adjuvant", "Immunotherapy"],
        "keywords": ["staging", "TNM", "EGFR", "PD-1", "adverse events", "RECIST"],
    },
    "neurology": {
        "mesh": ["Stroke", "Epilepsy", "Parkinson Disease"],
        "keywords": ["tPA", "thrombectomy", "seizure", "antiepileptic", "NIHSS"],
    },
    "rheumatology": {
        "mesh": ["Arthritis, Rheumatoid", "Spondylarthropathies", "Gout"],
        "keywords": ["DMARD", "biologic", "treat-to-target", "uric acid", "flare"],
    },
    "geriatrics": {
        "mesh": ["Geriatric Assessment", "Frail Elderly"],
        "keywords": ["deprescribing", "BEERS criteria", "falls", "delirium"],
    },
    "critical_care": {
        "mesh": ["Respiration, Artificial", "Shock, Septic", "Acute Respiratory Distress Syndrome"],
        "keywords": ["vasopressor", "ventilator settings", "ARDSnet", "sedation", "analgesia"],
    },
    "primary_care": {
        "mesh": ["Preventive Health Services", "Vaccination", "Hypertension"],
        "keywords": ["screening", "USPSTF", "immunization schedule", "ASCVD risk"],
    },
    "pharmacology": {
        "mesh": ["Drug Interactions", "Pharmacokinetics", "Renal Insufficiency"],
        "keywords": ["renal dosing", "hepatic dosing", "contraindication", "black box warning"],
    },
    "diagnostics": {
        "mesh": ["Diagnostic Tests, Routine", "Biomarkers"],
        "keywords": ["reference range", "positive predictive value", "negative predictive value", "cutoff"],
    },
}

# Exclusion filters: non-clinical or non-actionable content to avoid
GLOBAL_EXCLUDE_KEYWORDS: List[str] = [
    "insurance", "reimbursement", "billing", "coding", "CPT", "ICD-10",
    "economics", "cost-effectiveness", "coverage",
    "policy", "legislation", "malpractice", "legal",
    "marketing", "advertorial", "press release", "newsroom",
    "opinion", "editorial", "commentary"  # still allow systematic reviews
]

# ---------------------------------
# Public config export structure
# ---------------------------------

def get_config() -> Dict:
    return {
        "domains": SUPPORTED_DOMAINS,
        "trusted_sources": [asdict(s) for s in TRUSTED_SOURCES],
        "filters": {
            "include": {
                "global": {"mesh": GLOBAL_MESH_TERMS, "keywords": GLOBAL_KEYWORDS},
                "by_domain": DOMAIN_FILTERS_INCLUDE,
            },
            "exclude": {"global": {"keywords": GLOBAL_EXCLUDE_KEYWORDS}},
        },
        # Optional downstream behavior controls
        "ingest": {
            "dedupe_by": ["title", "doi", "trial_id"],
            "min_year": 2015,  # ignore very old items by default; override as needed
        },
    }

# -----------------------
# YAML dump helper
# -----------------------

def write_yaml(path: str | pathlib.Path) -> pathlib.Path:
    cfg = get_config()
    p = pathlib.Path(path)
    with p.open("w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, sort_keys=False, allow_unicode=True)
    return p


if __name__ == "__main__":
    out = pathlib.Path(__file__).with_name("sources_config.yaml")
    p = write_yaml(out)
    print(f"Wrote {p}")
