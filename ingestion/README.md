# InsideTracker ingestion — Pi / PostgreSQL 17 starter

This starter **adds** an importer service to the existing `health-analytics` Docker Compose project.
It does not replace the working `compose.yaml`, PostgreSQL volume, or schema. It does not
contain the user's personal CSV or any database credentials.

## Prerequisites

- Existing `/opt/health-analytics/compose.yaml` with services named `postgres` and a network `health-net`.
- Existing `/opt/health-analytics/.env` with POSTGRES_DB/USER/PASSWORD.
- Deployed `001_health_lab_schema.sql` (9 core tables).
- A `core.person` row with label `primary` and `core.source_system` row code `insidetracker`.

## Installation

Unzip the bundle **into the existing directory**:

```bash
sudo mkdir -p /opt/health-analytics/imports
cd /opt/health-analytics
# copy bundle from your workstation here first
unzip health_ingestion_starter.zip -d /opt/health-analytics
```

Copy the original InsideTracker CSV to
`/opt/health-analytics/imports/bloodwork_history-eric.csv` without editing it.
The importer sees this file read-only as `/imports/bloodwork_history-eric.csv`.

### 1. Confirm the combined Compose model

```bash
cd /opt/health-analytics
docker compose -f compose.yaml -f compose.ingest.yaml config --services
```

Expected: `postgres` and `importer` (depending on Compose profile filtering, importer may not
show without `--profile tools`; explicitly invoking `run importer` activates the service).

### 2. Build image and perform safe dry-run

```bash
docker compose -f compose.yaml -f compose.ingest.yaml build importer

docker compose -f compose.yaml -f compose.ingest.yaml run --rm importer \
  --file /imports/bloodwork_history-eric.csv
```

**Dry-run uses NO database connection and writes nothing.** For the original July 2026 export,
expect **54 markers, 10 result columns, 321 numeric results**. Column-specific counts should be:
52, 52, 54, 26, 1, 1, 1, 31, 52, 51.

### 3. Import (only after validating dry-run)

```bash
docker compose -f compose.yaml -f compose.ingest.yaml run --rm importer \
  --file /imports/bloodwork_history-eric.csv --apply
```

The import commits as a **single PostgreSQL transaction**. On error it rolls back. Re-running
with identical file bytes checks the SHA-256 and reports already imported; it does not create
duplicates. If the matching file has unexpected DB counts, the importer stops with an error.

### 4. Verify in PostgreSQL

```bash
docker compose exec -T postgres psql -X -U health_admin -d health \
  < ingestion/verify_insidetracker.sql
```

Or if your .env uses a different user/database, replace them above.

## Modeling choices

- Each date column becomes an event, even when two headers share a date. The source column index
  distinguishes them.
- A heading date is stored as `result_on` with `date_basis='reported'`;
  `collected_on` stays NULL because the CSV doesn't independently establish collection date.
- Blank and `-` cells are omitted, not zeroes.
- Original strings and units are preserved; values are parsed with `Decimal`.
  Normalized fields are left NULL pending reviewed cross-source harmonization.
- The canonical analyte name is initially the test label without its terminal parenthesized
  units. This is a **provisional InsideTracker-specific mapping**, not an assertion that
  provider assays with similarly spelled names are clinically interchangeable.
- PDF-only reference ranges, colored classifications and categories are intentionally not
  inferred from this CSV. We can add them with a later controlled enrichment step.
- Only the known calculated InsideTracker `Testosterone:Cortisol Ratio` is flagged as calculated;
  other results have unknown direct-vs-calculated status.

## Security

- Keep all files under `imports/`, PostgreSQL dumps, `.env` and `.csv`/`.pdf` out of Git.
- Importer is nonroot, uses a read-only CSV mount, and doesn't expose ports.
- Long-term: use a dedicated least-privilege PostgreSQL importer role instead of health_admin.
  For initial setup, matching your existing credentials is simplest.
