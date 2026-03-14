# scripts/process_spl_drugs.py
"""
Process FDA Structured Product Label (SPL) XML files for drug information.

This script inventories the SPL corpus, parses XML labels to extract drug
metadata and clinically relevant sections, and emits normalized JSONL without
including associated images. It does NOT push data into the live RAG index;
instead it prepares artifacts for manual review/integration.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from collections import defaultdict
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional, Tuple

from lxml import etree


LOGGER = logging.getLogger("spl_processor")

# SPL section codes of interest (LOINC codes commonly used in FDA labels)
SECTION_CODES = {
    "34084-4": "adverse_reactions",
    "34073-7": "drug_interactions",
    "43678-2": "warnings_and_precautions",
    "34089-3": "dosage_and_administration",
    "43682-4": "contraindications",
    "34090-1": "dosage_forms_and_strengths",
    "43683-2": "warnings",
    "42232-8": "clinical_pharmacology",
    "42229-4": "indications_and_usage",
    "48780-1": "listing_data",  # metadata section
}


@dataclass
class Section:
    code: str
    name: str
    title: str
    text: str


@dataclass
class LabelRecord:
    source_path: str
    set_id: Optional[str]
    version: Optional[int]
    effective_time: Optional[str]
    product_name: Optional[str]
    ndc_list: List[str]
    manufacturer: Optional[str]
    sections: List[Section]


def configure_logging(log_file: Path, verbose: bool = False) -> None:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    handlers: List[logging.Handler] = [logging.FileHandler(log_file, encoding="utf-8")]
    if verbose:
        handlers.append(logging.StreamHandler(sys.stdout))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        handlers=handlers,
    )


def iter_xml_files(root: Path) -> Iterator[Path]:
    for xml_path in root.rglob("*.xml"):
        if xml_path.is_file():
            yield xml_path


def clean_text(elem: etree._Element) -> str:
    """Convert SPL XHTML content into readable text."""
    parts: List[str] = []

    def _recurse(node: etree._Element) -> None:
        if node.tag.endswith("}renderMultiMedia") or node.tag.endswith("}observationMedia"):
            return
        if node.text:
            parts.append(node.text)
        for child in node:
            _recurse(child)
            if child.tail:
                parts.append(child.tail)

    _recurse(elem)
    joined = " ".join(part.strip() for part in parts if part and part.strip())
    return " ".join(joined.split())


def parse_sections(body: etree._Element) -> List[Section]:
    sections: List[Section] = []
    for section in body.findall(".//{*}section"):
        code_elem = section.find("./{*}code")
        text_elem = section.find("./{*}text")
        title_elem = section.find("./{*}title")
        if code_elem is None or text_elem is None:
            continue
        code = code_elem.get("code")
        if not code or code not in SECTION_CODES:
            continue
        content = clean_text(text_elem)
        title = clean_text(title_elem) if title_elem is not None else SECTION_CODES[code]
        sections.append(
            Section(
                code=code,
                name=SECTION_CODES[code],
                title=title,
                text=content,
            )
        )
    return sections


def extract_label(xml_path: Path) -> Optional[LabelRecord]:
    try:
        context = etree.iterparse(str(xml_path), events=("start", "end"), recover=True)
    except etree.XMLSyntaxError as exc:
        LOGGER.error("XML syntax error in %s: %s", xml_path, exc)
        return None

    root_elem: Optional[etree._Element] = None
    for event, elem in context:
        if root_elem is None:
            root_elem = elem.getroottree().getroot()
            break
    if root_elem is None:
        LOGGER.warning("Empty XML: %s", xml_path)
        return None

    nsmap = root_elem.nsmap.copy()
    default_ns = nsmap.get(None, "")
    if default_ns:
        nsmap["ns"] = default_ns

    def find(path: str) -> Optional[etree._Element]:
        return root_elem.find(path, namespaces=nsmap)

    def findall(path: str) -> List[etree._Element]:
        return root_elem.findall(path, namespaces=nsmap)

    set_id = root_elem.get("setId")
    if set_id is None:
        set_elem = find(".//ns:setId")
        if set_elem is not None:
            set_id = set_elem.get("root")
    version = None
    version_elem = find(".//ns:versionNumber")
    if version_elem is not None:
        try:
            version = int(version_elem.get("value", "0"))
        except ValueError:
            pass
    effective_time = None
    eff_elem = find(".//ns:effectiveTime")
    if eff_elem is not None:
        effective_time = eff_elem.get("value")

    title = root_elem.findtext(".//ns:title", namespaces=nsmap)
    ndc_elems = findall(".//ns:code[@codeSystem='2.16.840.1.113883.6.69']")
    ndc_list = sorted({elem.get("code") for elem in ndc_elems if elem.get("code")})

    manufacturer = None
    mfg_elem = find(".//ns:assignedEntity/ns:representedOrganization/ns:name")
    if mfg_elem is not None and mfg_elem.text:
        manufacturer = mfg_elem.text.strip()

    body = find(".//ns:structuredBody")
    if body is None:
        LOGGER.warning("No structuredBody in %s", xml_path)
        return None
    sections = parse_sections(body)
    if not sections:
        LOGGER.info("No target sections extracted from %s", xml_path)
        return None

    return LabelRecord(
        source_path=str(xml_path),
        set_id=set_id,
        version=version,
        effective_time=effective_time,
        product_name=title.strip() if title else None,
        ndc_list=ndc_list,
        manufacturer=manufacturer,
        sections=sections,
    )


def write_jsonl(records: Iterable[LabelRecord], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        for record in records:
            data = asdict(record)
            data["sections"] = [asdict(section) for section in record.sections]
            f.write(json.dumps(data, ensure_ascii=False) + "\n")


def build_inventory(root: Path) -> Dict[str, int]:
    counts = defaultdict(int)
    for path in root.iterdir():
        if path.is_file():
            counts[path.suffix.lower()] += 1
        elif path.is_dir():
            counts["<directory>"] += 1
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description="Process FDA SPL drug XML corpus.")
    parser.add_argument("--input", type=Path, required=True, help="Source directory with SPL XML files.")
    parser.add_argument("--output", type=Path, required=True, help="Destination JSONL path.")
    parser.add_argument("--log", type=Path, required=True, help="Log file path.")
    parser.add_argument("--limit", type=int, default=None, help="Optional limit of files to process (for dry runs).")
    parser.add_argument("--verbose", action="store_true", help="Print logs to stdout.")
    args = parser.parse_args()

    configure_logging(args.log, verbose=args.verbose)
    LOGGER.info("Starting SPL processing: input=%s limit=%s", args.input, args.limit)

    processed: List[LabelRecord] = []
    total = 0
    for xml_path in iter_xml_files(args.input):
        total += 1
        record = extract_label(xml_path)
        if record is not None:
            processed.append(record)
        if args.limit and total >= args.limit:
            break

        if total % 1000 == 0:
            LOGGER.info("Scanned %d files (%d parsed)", total, len(processed))

    LOGGER.info("Finished scanning %d files; extracted %d labels", total, len(processed))
    write_jsonl(processed, args.output)
    LOGGER.info("Wrote normalized data to %s", args.output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
