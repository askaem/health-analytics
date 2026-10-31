# Function Health PDF laboratory importer

**Add-on for the existing Raspberry Pi `/opt/health-analytics` installation.**
Designed and locally **parser-tested** using the 11-page Function report dated July 25, 2026.
It has **not** been tested against your live PostgreSQL instance.

* Does not overwrite existing `compose.yaml`, InsideTracker importer, or the 9-table schema.
* Does not include a PDF, extracted private results, credentials, or demographics.
* Requires **no new SQL migration**; uses your current `core` tables.
* Ignores the entire **Daily Metrics** (Fitbit) section.
* Preserves raw strings, text results, source flags, date, page, categories, and units.
* Treats numeric `<` results as **bounds** (`value_operator='<'`), not exact values.
* One `lab_result` per (reported date, source test label), and extra `report_result_appearance`
  rows for repeated entries. Inconsistent duplicates abort the import.
* Reuses 48 existing InsideTracker concepts through an explicit unit-checked crosswalk.
  Original values remain intact; normalized values are saved for recognized conversions.
  Common analyte identity does **not** prove different assay methods are directly comparable.
* Performs transaction-based writes only under `--apply`, idempotent by source PDF SHA-256.

## Installation (Pi)

1. Download/extract `function_ingestion_starter.zip` under your existing project folder:

   ```bash
   cd /opt/health-analytics
   unzip function_ingestion_starter.zip
   ```

   This adds `compose.function.yaml`, `ingestion/Dockerfile.function`,
   `ingestion/function_import.py`, and `ingestion/verify_function.sql`.

2. Copy the **real Function PDF** separately into `/opt/health-analytics/imports/` and
   give it a simple basename, e.g. `function-2026-07.pdf`. No PDF is inside this ZIP.
   Confirm only you/admins can access the file. It remains mounted read-only in the container.

3. Build the additional importer image:

   ```bash
   cd /opt/health-analytics
   docker compose -f compose.yaml -f compose.function.yaml build function-importer
   ```

## Dry run (NO DB connection / NO writes)

```bash
cd /opt/health-analytics
docker compose -f compose.yaml -f compose.function.yaml run --rm function-importer \
  --file /imports/function-2026-07.pdf \
  --expect-date 2026-07-25 \
  --expect-observations 109 \
  --expect-appearances 117
```

Expected summary from *this* report:

- PDF pages: **11**
- Lab result dates: **2026-07-25** (shown result date; not assumed specimen collection date)
- Lab report sections: **15**, excluding Daily Metrics
- Displayed lab entries: **117**
- Distinct lab observations: **109**
- Repeated appearances: **8**
- Existing InsideTracker concept crosswalk matches: **48**
- Nonnumeric results: **12**, quantitative `<` bounds: **6**, source-calculated results: **8**

Use `--show-all` if you want to review every parsed biomarker and value locally. It displays
sensitive values in terminal output—don't post that output publicly.

### Known repeated appearances (correctly deduplicated)

- Calcium (3 appearances)
- Chloride, RBC Magnesium, Potassium, Sodium, hs-CRP, and DHEA Sulfate (2 each)

## Apply only AFTER inspecting the dry-run summary

```bash
cd /opt/health-analytics
docker compose -f compose.yaml -f compose.function.yaml run --rm function-importer \
  --file /imports/function-2026-07.pdf \
  --expect-date 2026-07-25 \
  --expect-observations 109 \
  --expect-appearances 117 \
  --apply
```

Expected:

```text
COMMITTED: 1 event(s), 109 unique lab results, 117 report appearances; Daily Metrics excluded.
```

An error during insert automatically rolls back the **entire** new Function import.
Replaying identical PDF bytes prints `ALREADY IMPORTED` and makes no changes.
The existing 321 InsideTracker results are not modified by the importer.

## Post-import validation

```bash
cd /opt/health-analytics
docker compose exec -T postgres psql -X -v ON_ERROR_STOP=1 \
  -U health_admin -d health < ingestion/verify_function.sql
```

The first query should show **1 event, 109 results, 117 appearances** for this report.
The cross-source list shows which canonical biomarkers are now shared.

## Mapping / normalization rules

An explicit `CROSSWALK` constant at the top of `function_import.py` maps only named Function
markers to matching InsideTracker analytes, with strict expected units. Common spelling
aliases with identical numerical scales are allowed (e.g. `mcg/dL` and `µg/dL`).
Free testosterone `pg/mL -> ng/dL` uses a factor of **0.1** (96.2 pg/mL → 9.62 ng/dL),
but this **does not** establish assay equivalence. No clinical interpretation is attempted.

Unmapped Function tests create their own canonical analytes. For example, **serum Albumin**
remains distinct from **urine Albumin (random, without creatinine)**. We do not invent lab
reference cutoffs when the Function PDF reports only `In Range`/`Above Range`/`Below Range`.
`value_operator='<'` rows store their numeric value as an upper bound, not an observation
of that exact number. Always use `value_operator` in subsequent analytics.

## Security

Keep `imports/`, `.env`, `.pdf`, `.csv`, backup dumps, and notebook exports out of Git.
`function-importer` is non-root, uses a read-only file bind mount, and exposes no ports.
The image includes `poppler-utils` (`pdftotext`) for text PDF extraction and `psycopg`.
No outside network or third-party OCR service is used by the parser.

Troubleshooting: If `--apply` fails with password authentication, the password in PostgreSQL's
stored role may differ from the Compose `.env` password. Do not reset your database volume.

NOTE: The script expects searchable/selectable PDF text with the report section structure
seen in your Function July 2026 export. If Function changes the PDF format, it fails closed
on unexpected content instead of silently making up values.
