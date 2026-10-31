#!/usr/bin/env python3
"""Import InsideTracker's *wide* CSV into health schema v1.

Default is a pure CSV dry-run: no database connection, no writes.
--apply makes a single transaction; failed imports roll back completely.

Data principles:
- Duplicate date headings are separate event columns (indexed 1 through N).
- CSV date is a reported result date, not independently verified collection date.
- Store the source's units and numeric precision without claiming conversions.
- No inferred reference ranges, annotations, or fabricated observations.
- Alias mappings are initially provisional and specific to InsideTracker;
  cross-source harmonization requires later review.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import os
import re
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

NAME_UNIT_RE = re.compile(r"^(.*?)\s+\(([^()]*)\)\s*$")
NUMERIC_RE = re.compile(r"^[+-]?\d+(?:\.\d+)?$")
MISSING_VALUES = {"", "-", "—", "–"}
DATE_FORMAT = "%b %d %Y"


@dataclass(frozen=True)
class Marker:
    row_number: int                 # physical CSV row (1-based, including header)
    source_name: str
    canonical_name: str
    original_unit: str | None
    is_derived: bool


@dataclass(frozen=True)
class Observation:
    source_row_number: int
    source_column_index: int        # 1-based excluding marker-name column
    source_marker_name: str
    raw_value: str
    numeric_value: Decimal
    original_unit: str | None
    is_derived: bool


@dataclass(frozen=True)
class ImportData:
    dates: list[date]
    date_headings: list[str]
    markers: list[Marker]
    observations: list[Observation]
    checksum: str


def parse_name(source_name: str, row_number: int) -> Marker:
    match = NAME_UNIT_RE.fullmatch(source_name)
    if not match:
        raise ValueError(f"Row {row_number}: no unambiguous '(unit)' suffix: {source_name!r}")
    name, unit = match.group(1).strip(), match.group(2).strip()
    if not name or not unit:
        raise ValueError(f"Row {row_number}: missing name or unit")
    # The InsideTracker source includes a calculated testosterone/cortisol ratio.
    derived = name == "Testosterone:Cortisol Ratio"
    return Marker(row_number, source_name, name, unit, derived)


def parse_numeric(raw: str, row_number: int, column_index: int) -> Decimal:
    # Thousands separators in this export are spaces (e.g. '3 077.00').
    # Preserve the original raw string elsewhere.
    compact = raw.strip().replace(" ", "").replace("\u00a0", "").replace("\u202f", "")
    if not NUMERIC_RE.fullmatch(compact):
        raise ValueError(f"Non-numeric result in row {row_number}, result column {column_index}: {raw!r}")
    try:
        return Decimal(compact)
    except InvalidOperation as e:
        raise ValueError(f"Invalid number row {row_number}, column {column_index}: {raw!r}") from e


def parse_csv(path: Path) -> ImportData:
    content = path.read_bytes()
    sha = hashlib.sha256(content).hexdigest()
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        if not header or header[0].strip() != "Marker name" or len(header) < 2:
            raise ValueError("Expected InsideTracker CSV header: 'Marker name', followed by dated columns")
        headings = [s.strip() for s in header[1:]]
        try:
            dates = [datetime.strptime(s, DATE_FORMAT).date() for s in headings]
        except ValueError as e:
            raise ValueError(f"Could not parse InsideTracker date heading(s): {headings}") from e
        markers: list[Marker] = []
        observations: list[Observation] = []
        seen_source_names: set[str] = set()
        for row_number, row in enumerate(reader, start=2):
            if not row or all(not s.strip() for s in row):
                continue
            if len(row) != len(header):
                raise ValueError(f"Row {row_number}: expected {len(header)} columns, got {len(row)}")
            marker = parse_name(row[0].strip(), row_number)
            if marker.source_name in seen_source_names:
                raise ValueError(f"Duplicate marker name in source: {marker.source_name!r}")
            seen_source_names.add(marker.source_name)
            markers.append(marker)
            for idx, raw_value in enumerate(row[1:], start=1):
                if raw_value.strip() in MISSING_VALUES:
                    continue
                numeric = parse_numeric(raw_value, row_number, idx)
                observations.append(Observation(
                    source_row_number=row_number,
                    source_column_index=idx,
                    source_marker_name=marker.source_name,
                    raw_value=raw_value,
                    numeric_value=numeric,
                    original_unit=marker.original_unit,
                    is_derived=marker.is_derived,
                ))
        if not markers or not observations:
            raise ValueError("CSV has no markers or no usable results")
    # Canonical names are deliberately provisional until cross-source mappings are curated.
    if len({m.canonical_name for m in markers}) != len(markers):
        raise ValueError("Different source markers map to the same provisional canonical name; review manually")
    return ImportData(dates, headings, markers, observations, sha)


def print_summary(data: ImportData, path: Path) -> None:
    print(f"File: {path.name}")
    print(f"SHA256: {data.checksum}")
    print(f"Source marker rows: {len(data.markers)}")
    print(f"Dated result columns: {len(data.dates)}")
    print(f"Nonempty numeric observations: {len(data.observations)}")
    counts = Counter(o.source_column_index for o in data.observations)
    for idx, heading in enumerate(data.date_headings, start=1):
        print(f"  Column {idx:>2}: {heading:<13} results={counts[idx]}")
    print("Source units are preserved; collection dates and reference ranges are not invented.")


def require_one(cursor, sql: str, params: tuple, what: str) -> int:
    cursor.execute(sql, params)
    row = cursor.fetchone()
    if row is None:
        raise RuntimeError(f"Missing {what}. Ensure source registration succeeded before --apply.")
    return row[0]


def get_analyte_id(cursor, source_system_id: int, marker: Marker) -> int:
    cursor.execute(
        "SELECT analyte_id FROM core.analyte_alias WHERE source_system_id=%s AND source_marker_name=%s",
        (source_system_id, marker.source_name),
    )
    existing_alias = cursor.fetchone()
    if existing_alias:
        return existing_alias[0]  # Respect any curated/previously established mapping.

    cursor.execute(
        "SELECT analyte_id, canonical_unit FROM core.analyte WHERE canonical_name=%s",
        (marker.canonical_name,),
    )
    existing_analyte = cursor.fetchone()
    if existing_analyte:
        analyte_id, canonical_unit = existing_analyte
        if canonical_unit is not None and canonical_unit != marker.original_unit:
            raise RuntimeError(
                f"Unit conflict for {marker.canonical_name!r}: database canonical unit "
                f"{canonical_unit!r}, source unit {marker.original_unit!r}. "
                "Review mapping rather than automatically converting."
            )
    else:
        cursor.execute(
            """INSERT INTO core.analyte
                 (canonical_name, canonical_unit, value_type, is_derived)
                 VALUES (%s, %s, 'numeric', %s) RETURNING analyte_id""",
            (marker.canonical_name, marker.original_unit, marker.is_derived),
        )
        analyte_id = cursor.fetchone()[0]
    cursor.execute(
        """INSERT INTO core.analyte_alias (source_system_id, source_marker_name, analyte_id)
           VALUES (%s, %s, %s)""",
        (source_system_id, marker.source_name, analyte_id),
    )
    return analyte_id


def apply_to_postgres(data: ImportData, path: Path, person_label: str) -> None:
    try:
        import psycopg
    except ImportError as exc:
        raise RuntimeError("psycopg is not installed; build/run the importer Docker image") from exc

    # libpq reads PGHOST, PGPORT, PGDATABASE, PGUSER and PGPASSWORD.
    with psycopg.connect(connect_timeout=15) as connection:
        with connection.cursor() as cursor:
            person_id = require_one(
                cursor, "SELECT person_id FROM core.person WHERE label=%s", (person_label,),
                f"person label={person_label!r}",
            )
            source_id = require_one(
                cursor, "SELECT source_system_id FROM core.source_system WHERE code=%s",
                ("insidetracker",), "source_system code='insidetracker'",
            )
            cursor.execute(
                """SELECT source_artifact_id FROM core.source_artifact
                   WHERE source_system_id=%s AND sha256=%s""",
                (source_id, data.checksum),
            )
            prior = cursor.fetchone()
            if prior:
                artifact_id = prior[0]
                cursor.execute(
                    """SELECT COUNT(DISTINCT e.collection_event_id), COUNT(r.lab_result_id)
                       FROM core.collection_event e
                       LEFT JOIN core.lab_result r ON r.collection_event_id=e.collection_event_id
                       WHERE e.source_artifact_id=%s""",
                    (artifact_id,),
                )
                event_count, observation_count = cursor.fetchone()
                if event_count != len(data.dates) or observation_count != len(data.observations):
                    raise RuntimeError(
                        f"Existing matching file hash has only {event_count} events and "
                        f"{observation_count} observations. Expected {len(data.dates)} and "
                        f"{len(data.observations)}. Review; no changes made."
                    )
                print(f"ALREADY IMPORTED (checksum match): {event_count} events, "
                      f"{observation_count} results. No rows added.")
                return

            cursor.execute(
                """INSERT INTO core.source_artifact
                     (source_system_id, original_filename, storage_path, sha256, media_type)
                     VALUES (%s, %s, %s, %s, %s)
                     RETURNING source_artifact_id""",
                (source_id, path.name, str(path), data.checksum, "text/csv"),
            )
            artifact_id = cursor.fetchone()[0]
            analyte_by_marker = {
                m.source_name: get_analyte_id(cursor, source_id, m) for m in data.markers
            }
            event_ids: dict[int, int] = {}
            for idx, (heading, result_date) in enumerate(zip(data.date_headings, data.dates), start=1):
                cursor.execute(
                    """INSERT INTO core.collection_event
                         (person_id, source_system_id, source_artifact_id, source_column_index,
                          result_on, source_date_text, date_basis, source_event_label)
                         VALUES (%s, %s, %s, %s, %s, %s, 'reported', %s)
                         RETURNING collection_event_id""",
                    (person_id, source_id, artifact_id, idx, result_date,
                     heading, f"InsideTracker CSV result column {idx}: {heading}"),
                )
                event_ids[idx] = cursor.fetchone()[0]

            # 321 small rows in the original file: straightforward inserts suffice.
            for obs in data.observations:
                cursor.execute(
                    """INSERT INTO core.lab_result
                         (collection_event_id, analyte_id, source_row_number,
                          source_marker_name, raw_value, value_numeric, value_operator,
                          original_unit, is_calculated)
                         VALUES (%s, %s, %s, %s, %s, %s, '=', %s, %s)""",
                    (event_ids[obs.source_column_index],
                     analyte_by_marker[obs.source_marker_name],
                     obs.source_row_number, obs.source_marker_name, obs.raw_value,
                     obs.numeric_value, obs.original_unit,
                     True if obs.is_derived else None),
                )
            # Verify inserted results while still inside the transaction.
            cursor.execute(
                """SELECT COUNT(*) FROM core.lab_result r
                   JOIN core.collection_event e ON r.collection_event_id=e.collection_event_id
                   WHERE e.source_artifact_id=%s""",
                (artifact_id,),
            )
            inserted_results = cursor.fetchone()[0]
            if inserted_results != len(data.observations):
                raise RuntimeError(f"Verification failed: inserted {inserted_results} results")
        # The `with connection:` context commits here on success; rolls back on any error.
    print(f"COMMITTED: {len(data.dates)} events, {len(data.markers)} marker aliases, "
          f"{len(data.observations)} lab results (source checksum {data.checksum[:12]}...).")


def main() -> int:
    parser = argparse.ArgumentParser(description="InsideTracker CSV importer (dry-run by default)")
    parser.add_argument("--file", required=True, type=Path, help="CSV path inside importer container")
    parser.add_argument("--apply", action="store_true", help="Write to PostgreSQL (otherwise only parse/report)")
    parser.add_argument("--person", default="primary", help="core.person.label (default: primary)")
    args = parser.parse_args()
    try:
        data = parse_csv(args.file)
        print_summary(data, args.file)
        if args.apply:
            apply_to_postgres(data, args.file, args.person)
        else:
            print("DRY RUN COMPLETE: no database connection; no data written.")
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        # Don't print passwords or the connection URI.
        print(f"IMPORT FAILED ({type(exc).__name__}): {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
