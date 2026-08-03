import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import linear_kernel


@dataclass(frozen=True)
class CleanConfig:
    max_encounters: int = 24
    ttl_days: int = 9
    # Tightened dedupe: require higher similarity to collapse templates.
    similarity_threshold: float = 0.96
    sheet_name: str = "Prompts templates"


RX_NAME_IN_TITLE = re.compile(
    r"\b(?:mr|mrs|ms|dr|doctor)\.?\s+[A-Z][a-z]+\b|\b[A-Z][a-z]{2,}['’]s\b"
)
RX_EXCLUDED_DOMAIN = re.compile(
    r"\b("
    # Explicit specialties
    r"radiology|podiatry|dentistry|psychology|psychologist|dental|podiatrist|"
    # Generic imaging terms
    r"imaging|radiograph|radiography|x[\s-]?ray|sonography|ultrasound|doppler|"
    r"ct|c\.t\.|cta|ctpa|mri|m\.r\.i\.|mra|mrcp|pet|pet[\s-]?ct|spect|"
    r"mammogram|mammography|dexa|fluoroscopy|angiogram|angiography"
    r")\b",
    re.I,
)
RX_FORM_OR_CERT = re.compile(
    r"\b(form|certificate|certification|workcover|insurance|fmla|disability|fit\s*note|sick\s*note|attestation)\b",
    re.I,
)
RX_FORM_VERB = re.compile(r"\b(fill|complete|sign|certify)\b", re.I)


def sentence_case_title(title: str) -> str:
    t = str(title or "").strip()
    if not t:
        return ""
    t = re.sub(r"\s+", " ", t)
    tl = t.lower()
    m = re.search(r"[a-z]", tl)
    if not m:
        return t
    i = m.start()
    return tl[:i] + tl[i].upper() + tl[i + 1 :]


def _row_blob(row: pd.Series) -> str:
    return "\n".join(
        [
            str(row.get("Title", "") or ""),
            str(row.get("Specialty", "") or ""),
            str(row.get("Prompt", "") or ""),
            str(row.get("Examples", "") or ""),
        ]
    )


def removal_reason(row: pd.Series) -> str:
    reasons: list[str] = []
    title = str(row.get("Title", "") or "")
    blob = _row_blob(row)

    if RX_NAME_IN_TITLE.search(title):
        reasons.append("name_in_title")
    if RX_EXCLUDED_DOMAIN.search(blob):
        reasons.append("excluded_domain")
    if RX_FORM_OR_CERT.search(blob) and RX_FORM_VERB.search(blob):
        reasons.append("form_or_certificate")
    return "|".join(reasons)


def dedupe_by_similarity(df: pd.DataFrame, threshold: float) -> pd.DataFrame:
    # Dedupe on Prompt; if Prompt blank, fall back to Title+Examples.
    texts = (
        df["Prompt"]
        .astype(str)
        .str.strip()
        .replace("", np.nan)
        .fillna(df["Title"].astype(str).str.strip() + "\n" + df["Examples"].astype(str).str.strip())
        .tolist()
    )
    vec = TfidfVectorizer(stop_words="english", max_features=6000, ngram_range=(1, 2))
    X = vec.fit_transform(texts)
    S = linear_kernel(X, X)

    n = S.shape[0]
    keep = np.ones(n, dtype=bool)

    # Keep the most content-rich (longest Prompt) representative in each near-duplicate cluster.
    lengths = df["Prompt"].astype(str).str.len().to_numpy()
    order = np.argsort(-lengths)
    suppressed: set[int] = set()

    for idx in order:
        if idx in suppressed or not keep[idx]:
            continue
        dup = np.where(S[idx] >= threshold)[0]
        for j in dup:
            if j == idx:
                continue
            keep[j] = False
            suppressed.add(j)

    return df.loc[keep].copy()


def main() -> int:
    src = Path(r"C:\Users\NoteGenerator\Downloads\PromptTemplates.xlsx")
    if not src.exists():
        raise SystemExit(f"Missing input file: {src}")

    cfg = CleanConfig()

    df = pd.read_excel(src, sheet_name=cfg.sheet_name)
    # Normalize columns to strings.
    for c in ["Title", "Specialty", "Prompt", "Examples"]:
        if c in df.columns:
            df[c] = df[c].fillna("").astype(str)

    df["_remove_reason"] = df.apply(removal_reason, axis=1)
    removed = df[df["_remove_reason"] != ""].copy()
    base = df[df["_remove_reason"] == ""].copy()

    base["Title"] = base["Title"].map(sentence_case_title)
    clean = dedupe_by_similarity(base, threshold=cfg.similarity_threshold)

    out_xlsx = Path(r"c:\project-root\PromptTemplates.cleaned.xlsx")
    out_csv = Path(r"c:\project-root\PromptTemplates.cleaned.csv")
    removed_csv = Path(r"c:\project-root\PromptTemplates.removed.csv")

    clean.to_excel(out_xlsx, index=False, sheet_name=cfg.sheet_name)
    clean.to_csv(out_csv, index=False)
    removed.to_csv(removed_csv, index=False)

    print("original_rows", len(df))
    print("removed_by_rules", len(removed))
    print("after_rule_filters", len(base))
    print("after_similarity_dedupe", len(clean))
    print("wrote", str(out_xlsx))
    print("also", str(out_csv))
    print("removed_log", str(removed_csv))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

