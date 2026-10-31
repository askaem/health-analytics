#!/usr/bin/env python3
"""Conservative Function Health PDF -> PostgreSQL 9-table health-lab schema importer.

Defaults to an OFFLINE, read-only dry-run (no PostgreSQL connection).
--apply commits all changes in a single transaction. Same-file SHA-256 replay is
idempotent; unexpected duplicate/conflicting observations abort the import.

Designed/tested against the user's 11-page July 25, 2026 Function export.
No demographic/PDF-header data or Daily Metrics/Fitbit values are imported.
"""
from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, date
from decimal import Decimal
from pathlib import Path

DATE_RE = re.compile(r"^[A-Z][a-z]{2} \d{1,2}, \d{4}$")
NUMERIC_RE = re.compile(r"^(?P<op><=|>=|<|>)?\s*(?P<num>-?\d+(?:\.\d+)?)"
                        r"(?:\s+(?P<unit>.+?))?(?:\s+\(calc\))?$", re.I)
STATUS_RE = re.compile(r"^(In Range|Above Range|Below Range)\s*·\s*(.+)$")
ALL_SECTIONS = [
    "Autoimmunity", "Blood", "Daily Metrics", "Electrolytes", "Environmental Toxins",
    "Heart", "Immune Regulation", "Kidney", "Liver", "Male Health", "Metabolic",
    "Nutrients", "Pancreas", "Stress & Aging", "Thyroid", "Urine",
]
SKIP_SECTIONS = {"Daily Metrics"}  # Fitbit-derived; deliberately defer.
LAB_SECTIONS = [s for s in ALL_SECTIONS if s not in SKIP_SECTIONS]
PUA = str.maketrans({"\ue081": "(", "\ue082": ")", "\ue088": "-", "\ue092": ":", "\ue097": "<"})

# Explicit, reviewed concept crosswalk to existing InsideTracker analytes.
# (existing canonical name, Function original unit, multiplier to canonical unit)
# All raw numbers/units are retained even when normalized copies are calculated.
# Assay method equivalence is NOT established by this mapping.
D = Decimal
CROSSWALK = {
    "Hematocrit": ("Hematocrit", "%", D("1")),
    "Hemoglobin": ("Hemoglobin", "g/dL", D("1")),
    "Mean Corpuscular Hemoglobin (MCH)": ("MCH", "pg", D("1")),
    "Mean Corpuscular Hemoglobin Concentration (MCHC)": ("MCHC", "g/dL", D("1")),
    "Mean Corpuscular Volume (MCV)": ("MCV", "fL", D("1")),
    "Mean Platelet Volume (MPV)": ("MPV", "fL", D("1")),
    "Platelet Count": ("Platelets", "Thousand/uL", D("1")),
    "Red Blood Cell (RBC) Count": ("RBC", "Million/uL", D("1")),
    "Red Cell Distribution Width (RDW)": ("RDW", "%", D("1")),
    "Calcium": ("Calcium", "mg/dL", D("1")),
    "Magnesium, RBC": ("RBC Magnesium", "mg/dL", D("1")),
    "Potassium": ("Potassium", "mmol/L", D("1")),
    "Sodium": ("Sodium", "mmol/L", D("1")),
    "Apolipoprotein B (ApoB)": ("ApoB", "mg/dL", D("1")),
    "High-Sensitivity C-Reactive Protein (hs-CRP)": ("hsCRP", "mg/L", D("1")),
    "LDL-Cholesterol": ("LDL Cholesterol", "mg/dL", D("1")),
    "Total Cholesterol": ("Total Cholesterol", "mg/dL", D("1")),
    "HDL-Cholesterol": ("HDL Cholesterol", "mg/dL", D("1")),
    "Triglycerides": ("Triglycerides", "mg/dL", D("1")),
    "Basophils": ("Basophil count", "cells/uL", D("1")),
    "Eosinophils": ("Eosinophil count", "cells/uL", D("1")),
    "Lymphocytes": ("Lymphocyte count", "cells/uL", D("1")),
    "Monocytes": ("Monocyte count", "cells/uL", D("1")),
    "Neutrophils": ("Neutrophil count", "cells/uL", D("1")),
    "White Blood Cell (WBC) Count": ("White blood cells", "Thousand/uL", D("1")),
    "Basophils %": ("Basophil percentage", "%", D("1")),
    "Eosinophils %": ("Eosinophil percentage", "%", D("1")),
    "Lymphocytes %": ("Lymphocyte percentage", "%", D("1")),
    "Monocytes %": ("Monocyte percentage", "%", D("1")),
    "Neutrophils %": ("Neutrophil percentage", "%", D("1")),
    "Alanine Aminotransferase (ALT)": ("ALT", "U/L", D("1")),
    "Albumin": ("Albumin", "g/dL", D("1")),
    "Aspartate Aminotransferase (AST)": ("AST", "U/L", D("1")),
    "Gamma-glutamyl Transferase (GGT)": ("GGT", "U/L", D("1")),
    "Estradiol (E2)": ("Estradiol", "pg/mL", D("1")),
    "Sex Hormone Binding Globulin (SHBG)": ("SHBG", "nmol/L", D("1")),
    "Testosterone, Free": ("Free testosterone", "pg/mL", D("0.1")),
    "Testosterone, Total": ("Testosterone", "ng/dL", D("1")),
    "Glucose": ("Glucose", "mg/dL", D("1")),
    "Hemoglobin A1c (HbA1c)": ("HbA1c", "%", D("1")),
    "Insulin": ("Insulin", "uIU/mL", D("1")),
    "Ferritin": ("Ferritin", "ng/mL", D("1")),
    "Iron": ("Iron", "mcg/dL", D("1")),
    "Iron % Saturation": ("Transferrin saturation", "%", D("1")),
    "Iron Binding Capacity": ("TIBC", "mcg/dL", D("1")),
    "Vitamin D": ("Vitamin D", "ng/mL", D("1")),
    "Cortisol": ("Cortisol", "mcg/dL", D("1")),
    "Thyroid-Stimulating Hormone (TSH)": ("TSH", "mIU/L", D("1")),
}
# These unit representations are equivalent in scale to InsideTracker's labels.
ALLOWED_CANONICAL_UNITS = {
    "RBC": {"x10E6/µL"}, "Platelets": {"thousands/uL"},
    "White blood cells": {"thousands/uL"},
    "Basophil count": {"cells/µL"},
    "Eosinophil count": {"cells/µL"},
    "Lymphocyte count": {"cells/µL"},
    "Monocyte count": {"cells/µL"},
    "Neutrophil count": {"cells/µL"},
    "Cortisol": {"µg/dL"}, "Iron": {"ug/dL"}, "TIBC": {"ug/dL"},
    "Insulin": {"µIU/mL"}, "TSH": {"µIU/L"},
}


@dataclass(frozen=True)
class Appearance:
    name: str
    raw_value: str
    flag: str | None
    section: str
    page: int
    date_text: str
    result_date: date
    order: int


@dataclass
class Lab:
    name: str
    raw_value: str
    value_numeric: Decimal | None
    value_text: str | None
    value_operator: str | None
    unit: str | None
    flag: str | None
    calculated: bool
    result_date: date
    first_appearance: Appearance
    appearances: list[Appearance] = field(default_factory=list)


@dataclass
class ParsedReport:
    checksum: str
    pages: int
    sections: list[str]
    appearances: list[Appearance]
    results: list[Lab]
    dates: list[date]


def clean(text: str) -> str:
    return " ".join(text.translate(PUA).replace("\u00a0", " ").strip().split())


def parse_value(text: str) -> tuple[str, Decimal | None, str | None, str | None, bool]:
    text = clean(text)
    calculated = text.endswith("(calc)")
    parse_text = text[:-6].rstrip() if calculated else text
    match = NUMERIC_RE.fullmatch(parse_text)
    if match:
        op = match.group("op") or "="
        return ("numeric", Decimal(match.group("num")), op,
                match.group("unit"), calculated)
    if re.match(r"^(?:[<>]=?)?\s*\d", parse_text):
        raise ValueError(f"Numeric-looking value could not be parsed safely: {text!r}")
    return ("text", None, None, None, calculated)


def read_pdf(path: Path) -> ParsedReport:
    if path.suffix.lower() != ".pdf":
        raise ValueError("Expected a .pdf Function report")
    content = path.read_bytes()
    if not content.startswith(b"%PDF-"):
        raise ValueError("Not a PDF file")
    checksum = hashlib.sha256(content).hexdigest()
    cmd = ["pdftotext", "-layout", "-enc", "UTF-8", str(path), "-"]
    cp = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if cp.returncode != 0:
        raise ValueError("pdftotext failed: " + cp.stderr.decode("utf-8", "replace")[:300])
    pages = [p for p in cp.stdout.decode("utf-8", "replace").split("\f") if p.strip()]
    if not pages:
        raise ValueError("No selectable text extracted; OCR/manual review required")
    current_section: str | None = None
    sections: list[str] = []
    appearances: list[Appearance] = []
    order = 0
    for page_number, text in enumerate(pages, 1):
        lines = [clean(l) for l in text.splitlines()]
        tokens = [l for l in lines if l]
        i = 0
        while i < len(tokens):
            value = tokens[i]
            if value in ALL_SECTIONS:
                current_section = value
                if value not in sections:
                    sections.append(value)
                i += 1
                continue
            if current_section in SKIP_SECTIONS:
                i += 1
                continue
            if DATE_RE.fullmatch(value):
                if current_section not in LAB_SECTIONS:
                    raise ValueError(f"Page {page_number}: result date outside a lab section")
                if i + 2 >= len(tokens):
                    raise ValueError(f"Page {page_number}: truncated entry after {value}")
                name = tokens[i+1]
                result_line = tokens[i+2]
                if DATE_RE.fullmatch(name) or name in ALL_SECTIONS or DATE_RE.fullmatch(result_line) or result_line in ALL_SECTIONS:
                    raise ValueError(f"Page {page_number}: malformed record starting {value}")
                try:
                    result_date = datetime.strptime(value, "%b %d, %Y").date()
                except ValueError as exc:
                    raise ValueError(f"Bad Function displayed date: {value}") from exc
                status_match = STATUS_RE.fullmatch(result_line)
                flag, raw_value = (status_match.group(1), status_match.group(2)) if status_match else (None, result_line)
                order += 1
                appearances.append(Appearance(name, raw_value, flag, current_section,
                                              page_number, value, result_date, order))
                i += 3
                continue
            # Only the header may exist outside a section. Unexpected lines in lab sections
            # must not silently disappear (avoids a misleading 'successful' import).
            if current_section in LAB_SECTIONS:
                raise ValueError(f"Unexpected PDF content on page {page_number} in {current_section!r}: {value!r}")
            # Report heading may include patient name and DOB; never store them.
            # This allowance applies ONLY before the first recognized section.
            if current_section is not None:
                raise ValueError(f"Unexpected content on page {page_number}: {value!r}")
            i += 1
    missing_sections = set(LAB_SECTIONS) - set(sections)
    if missing_sections:
        raise ValueError(f"Expected Function report sections missing: {sorted(missing_sections)}")
    if "Daily Metrics" not in sections:
        print("NOTE: No Daily Metrics section in this report (still excluded).")
    labs_by_key: dict[tuple[date, str], Lab] = {}
    for appearance in appearances:
        kind, numeric, op, unit, is_calculated = parse_value(appearance.raw_value)
        key = (appearance.result_date, appearance.name)
        if key in labs_by_key:
            lab = labs_by_key[key]
            # Repeated display of a test is allowed only when all fields agree.
            if ((lab.raw_value, lab.flag, lab.unit, lab.value_numeric,
                 lab.value_operator, lab.value_text, lab.calculated) !=
                (appearance.raw_value, appearance.flag, unit, numeric, op,
                 appearance.raw_value if kind == "text" else None, is_calculated)):
                raise ValueError(f"Conflicting repeats for {appearance.name} on {appearance.result_date}; manual review required")
            lab.appearances.append(appearance)
        else:
            lab = Lab(name=appearance.name, raw_value=appearance.raw_value,
                      value_numeric=numeric, value_text=(appearance.raw_value if kind == "text" else None),
                      value_operator=op, unit=unit, flag=appearance.flag,
                      calculated=is_calculated, result_date=appearance.result_date,
                      first_appearance=appearance, appearances=[appearance])
            labs_by_key[key] = lab
    results = list(labs_by_key.values())
    if not results:
        raise ValueError("No laboratory results found")
    # No source date is labeled specimen-collection date. Treat all as report/result dates.
    return ParsedReport(checksum, len(pages), sections, appearances, results,
                        sorted({r.result_date for r in results}))


def preview(report: ParsedReport, path: Path, show_all: bool) -> None:
    print(f"File: {path.name}; PDF pages: {report.pages}; SHA256: {report.checksum}")
    print(f"Laboratory result dates: {', '.join(map(str, report.dates))}")
    print(f"Lab sections: {len([s for s in report.sections if s not in SKIP_SECTIONS])}; daily metrics excluded: yes")
    print(f"Displayed laboratory entries: {len(report.appearances)}")
    print(f"Distinct laboratory observations: {len(report.results)}")
    print(f"Repeated display appearances: {len(report.appearances)-len(report.results)}")
    print(f"Matched existing InsideTracker concepts (mapping candidates): {sum(r.name in CROSSWALK for r in report.results)}")
    print("Lab appearance counts by section:")
    counts = Counter(a.section for a in report.appearances)
    for section in report.sections:
        if section not in SKIP_SECTIONS:
            print(f"  {section:<24} {counts[section]:>3}")
    print("Inequalities / nonnumeric values:")
    for r in report.results:
        if r.value_operator in ("<", ">", "<=", ">=") or r.value_numeric is None:
            print(f"  p{r.first_appearance.page}: {r.name}: {r.raw_value}")
    if show_all:
        print("ALL parsed observations (review sensitive values locally; do not post publicly):")
        for r in report.results:
            print(f"  {r.result_date} [{r.first_appearance.section},p{r.first_appearance.page}]"
                  f" {r.name} = {r.raw_value} [{r.flag or 'no flag'}]"
                  f" (canonical={CROSSWALK.get(r.name, (r.name,))[0]})")


def one(cur, query: str, params: tuple, what: str):
    cur.execute(query, params)
    row = cur.fetchone()
    if row is None:
        raise RuntimeError(f"Missing {what}; verify source registration and InsideTracker import")
    return row[0]


def get_analyte(cur, source_id: int, result: Lab) -> tuple[int, Decimal | None, str | None]:
    cur.execute("SELECT analyte_id FROM core.analyte_alias WHERE source_system_id=%s AND source_marker_name=%s",
                (source_id, result.name))
    existing_alias = cur.fetchone()
    if existing_alias:
        analyte_id = existing_alias[0]
        cur.execute("SELECT canonical_unit FROM core.analyte WHERE analyte_id=%s", (analyte_id,))
        target_unit = cur.fetchone()[0]
        # Pre-curated alias respected; do not assume a unit conversion unless
        # the exact mapping in this script is known and compatible.
        if result.name in CROSSWALK:
            target, expected, factor = CROSSWALK[result.name]
            if result.unit != expected:
                raise ValueError(f"Unit changed for {result.name!r}: expected {expected!r}, got {result.unit!r}")
            return analyte_id, (result.value_numeric*factor if result.value_numeric is not None else None), target_unit
        return analyte_id, None, None

    if result.name in CROSSWALK:
        target, expected_unit, factor = CROSSWALK[result.name]
        if result.unit != expected_unit:
            raise ValueError(f"Unexpected Function unit for {result.name!r}: {result.unit!r} != {expected_unit!r}")
        cur.execute("SELECT analyte_id, canonical_unit FROM core.analyte WHERE canonical_name=%s", (target,))
        row = cur.fetchone()
        if row is None:
            raise RuntimeError(f"Mapped InsideTracker analyte {target!r} does not exist. Import InsideTracker first.")
        analyte_id, canonical_unit = row
        allowed = ALLOWED_CANONICAL_UNITS.get(target, {expected_unit})
        if target == "Free testosterone":
            allowed = {"ng/dL"}
        if canonical_unit not in allowed:
            raise RuntimeError(f"Unexpected existing canonical unit {canonical_unit!r} for {target!r}; expected {allowed!r}")
        normalized = result.value_numeric * factor if result.value_numeric is not None else None
    else:
        # No inferred equivalence with other vendor tests. Future manual mapping can
        # coalesce concepts after reviewing assay/specimen definitions.
        target = result.name
        if result.name == "Albumin, Random Urine without Creatinine":
            target = "Urine Albumin (random, without creatinine)"
        cur.execute("SELECT analyte_id, canonical_unit FROM core.analyte WHERE canonical_name=%s", (target,))
        existing = cur.fetchone()
        if existing:
            raise RuntimeError(f"Unreviewed analyte collision: {target!r}. Add explicit crosswalk mapping before applying.")
        cur.execute("""INSERT INTO core.analyte
                        (canonical_name, canonical_unit, value_type, specimen_type, is_derived)
                        VALUES (%s,%s,%s,%s,%s) RETURNING analyte_id""",
                    (target, result.unit,
                     "numeric" if result.value_numeric is not None else "categorical",
                     "urine" if (result.first_appearance.section == "Urine" or
                                 result.name == "Albumin, Random Urine without Creatinine") else None,
                     result.calculated))
        analyte_id = cur.fetchone()[0]
        normalized = result.value_numeric
        canonical_unit = result.unit
    cur.execute("INSERT INTO core.analyte_alias (source_system_id,source_marker_name,analyte_id) VALUES (%s,%s,%s)",
                (source_id, result.name, analyte_id))
    return analyte_id, normalized, canonical_unit


def apply_report(report: ParsedReport, path: Path, person_label: str) -> None:
    try:
        import psycopg
    except ImportError as exc:
        raise RuntimeError("psycopg unavailable: run inside the provided Docker image") from exc
    with psycopg.connect(connect_timeout=15) as conn:
        with conn.cursor() as cur:
            person_id = one(cur, "SELECT person_id FROM core.person WHERE label=%s", (person_label,), "person")
            source_id = one(cur, "SELECT source_system_id FROM core.source_system WHERE code='function'", (), "Function source")
            cur.execute("""SELECT source_artifact_id FROM core.source_artifact
                           WHERE source_system_id=%s AND sha256=%s""", (source_id, report.checksum))
            previous = cur.fetchone()
            if previous:
                artifact_id = previous[0]
                cur.execute("""SELECT COUNT(DISTINCT e.collection_event_id), COUNT(DISTINCT r.lab_result_id),
                                       COUNT(DISTINCT ra.report_result_appearance_id)
                               FROM core.collection_event e
                               LEFT JOIN core.lab_result r ON r.collection_event_id=e.collection_event_id
                               LEFT JOIN core.report_result_appearance ra ON ra.lab_result_id=r.lab_result_id
                               WHERE e.source_artifact_id=%s""", (artifact_id,))
                counts = cur.fetchone()
                expected = (len(report.dates), len(report.results), len(report.appearances))
                if counts != expected:
                    raise RuntimeError(f"Existing same-checksum artifact incomplete: {counts} vs expected {expected}")
                print(f"ALREADY IMPORTED: {counts[0]} events, {counts[1]} results, {counts[2]} appearances. No changes.")
                return
            cur.execute("""INSERT INTO core.source_artifact
                           (source_system_id, original_filename,storage_path,sha256,media_type)
                           VALUES (%s,%s,%s,%s,'application/pdf') RETURNING source_artifact_id""",
                        (source_id, path.name, str(path), report.checksum))
            artifact_id = cur.fetchone()[0]
            event_ids = {}
            for d in report.dates:
                cur.execute("""INSERT INTO core.collection_event
                               (person_id,source_system_id,source_artifact_id,result_on,source_date_text,
                                date_basis,source_event_label)
                               VALUES (%s,%s,%s,%s,%s,'reported',%s) RETURNING collection_event_id""",
                            (person_id, source_id, artifact_id, d,
                             d.strftime('%b %d, %Y'), f'Function PDF results dated {d.isoformat()}'))
                event_ids[d] = cur.fetchone()[0]
            section_ids = {}
            for idx, section in enumerate((s for s in report.sections if s not in SKIP_SECTIONS), start=1):
                cur.execute("""INSERT INTO core.report_section (source_artifact_id,section_name,section_order)
                               VALUES (%s,%s,%s) RETURNING report_section_id""",
                            (artifact_id, section, idx))
                section_ids[section] = cur.fetchone()[0]
            # No automatic merging with other report files on same date.
            for idx, r in enumerate(report.results, start=1):
                analyte_id, normalized, norm_unit = get_analyte(cur, source_id, r)
                cur.execute("""INSERT INTO core.lab_result
                               (collection_event_id,analyte_id,source_row_number,source_marker_name,
                                raw_value,value_numeric,value_text,value_operator,original_unit,
                                normalized_value,normalized_unit,reported_flag,is_calculated,
                                calculation_note,specimen_type)
                               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                               RETURNING lab_result_id""",
                            (event_ids[r.result_date], analyte_id, idx, r.name,
                             r.raw_value, r.value_numeric, r.value_text, r.value_operator,
                             r.unit, normalized, norm_unit, r.flag, r.calculated,
                             'Source marked (calc)' if r.calculated else None,
                             'urine' if (r.first_appearance.section == 'Urine' or
                                         r.name == 'Albumin, Random Urine without Creatinine') else None))
                result_id = cur.fetchone()[0]
                for a in r.appearances:
                    cur.execute("""INSERT INTO core.report_result_appearance
                                   (lab_result_id,report_section_id,page_number,display_order,
                                    displayed_marker_name,displayed_raw_value,displayed_flag)
                                   VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                                (result_id, section_ids[a.section], a.page, a.order,
                                 a.name, a.raw_value, a.flag))
            cur.execute("""SELECT COUNT(DISTINCT r.lab_result_id), COUNT(ra.report_result_appearance_id)
                           FROM core.lab_result r
                           JOIN core.collection_event e ON e.collection_event_id=r.collection_event_id
                           LEFT JOIN core.report_result_appearance ra ON ra.lab_result_id=r.lab_result_id
                           WHERE e.source_artifact_id=%s""", (artifact_id,))
            stored_results, stored_appearances = cur.fetchone()
            if (stored_results, stored_appearances) != (len(report.results), len(report.appearances)):
                raise RuntimeError("Insert count mismatch; transaction rolled back")
        # automatic commit on success, rollback on any exception.
    print(f"COMMITTED: {len(report.dates)} event(s), {len(report.results)} unique lab results, "
          f"{len(report.appearances)} report appearances; Daily Metrics excluded.")


def main() -> int:
    p = argparse.ArgumentParser(description="Function PDF laboratory importer; dry-run by default")
    p.add_argument("--file", required=True, type=Path, help="PDF path inside container")
    p.add_argument("--apply", action="store_true", help="Commit a transactional import")
    p.add_argument("--show-all", action="store_true", help="Print all observations for manual review")
    p.add_argument("--person", default="primary")
    p.add_argument("--expect-observations", type=int,
                   help="Abort if the distinct lab observation count differs")
    p.add_argument("--expect-appearances", type=int,
                   help="Abort if the displayed lab entry count differs")
    p.add_argument("--expect-date", type=date.fromisoformat,
                   help="Abort unless the report contains just this date (YYYY-MM-DD)")
    args = p.parse_args()
    try:
        report = read_pdf(args.file)
        if args.expect_observations is not None and len(report.results) != args.expect_observations:
            raise ValueError(f"Expected {args.expect_observations} observations; got {len(report.results)}")
        if args.expect_appearances is not None and len(report.appearances) != args.expect_appearances:
            raise ValueError(f"Expected {args.expect_appearances} appearances; got {len(report.appearances)}")
        if args.expect_date is not None and report.dates != [args.expect_date]:
            raise ValueError(f"Unexpected report dates: {report.dates} (expected {args.expect_date})")
        preview(report, args.file, args.show_all)
        if args.apply:
            apply_report(report, args.file, args.person)
        else:
            print("DRY RUN COMPLETE: no PostgreSQL connection; no rows written.")
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"IMPORT FAILED ({type(exc).__name__}): {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
