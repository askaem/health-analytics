-- Health Analytics: complete laboratory schema, initial build (v1)
-- PostgreSQL 17+
-- For a NEW health database only. Replaces both earlier 001 and 002 scripts.
-- Handles InsideTracker, Function, and future clinician/lab reports.
-- Fitbit / wearables / daily metrics are intentionally EXCLUDED.
-- No patient records, results, or credentials are inserted by this script.
--
-- Usage from the Raspberry Pi (adjust directory if needed):
--   cd /opt/health-analytics
--   docker compose exec -T postgres \
--     psql -X -v ON_ERROR_STOP=1 -U health_admin -d health \
--     < schema/001_health_lab_schema.sql
--
-- Transactional, one-time schema creation; fails on conflicting existing tables.
-- This script does NOT drop, truncate, or replace any existing data.

BEGIN;

-- Separate normalized data, future import work, and BI-facing views.
CREATE SCHEMA IF NOT EXISTS core;
CREATE SCHEMA IF NOT EXISTS staging;
CREATE SCHEMA IF NOT EXISTS analytics;

-- Supports one person today and multiple people later, if desired.
CREATE TABLE core.person (
    person_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    label TEXT NOT NULL UNIQUE
);

-- Originating vendors, physician practices, or other data providers.
CREATE TABLE core.source_system (
    source_system_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL
);

-- Trace every import to its original file/report. Store originals outside PG.
CREATE TABLE core.source_artifact (
    source_artifact_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_system_id BIGINT NOT NULL
        REFERENCES core.source_system(source_system_id),
    original_filename TEXT NOT NULL,
    storage_path TEXT,
    sha256 CHAR(64),
    media_type TEXT,
    registered_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT source_artifact_checksum_format
        CHECK (sha256 IS NULL OR sha256 ~ '^[a-fA-F0-9]{64}$'),
    CONSTRAINT source_artifact_source_sha256_uq
        UNIQUE (source_system_id, sha256)
);

-- One testing/reporting group, not necessarily one distinct calendar day.
-- InsideTracker: each CSV result column becomes a separate event, even if
-- two columns have the same date. Function: displayed result_on may be known
-- while actual specimen collected_on is unknown; do not fabricate collection.
CREATE TABLE core.collection_event (
    collection_event_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    person_id BIGINT NOT NULL
        REFERENCES core.person(person_id),
    source_system_id BIGINT NOT NULL
        REFERENCES core.source_system(source_system_id),
    source_artifact_id BIGINT
        REFERENCES core.source_artifact(source_artifact_id),
    source_column_index INTEGER
        CHECK (source_column_index IS NULL OR source_column_index > 0),
    collected_on DATE,
    collected_at TIMESTAMPTZ,
    result_on DATE,
    source_date_text TEXT,
    date_basis TEXT,
    source_event_label TEXT,
    lab_name TEXT,
    fasting_status BOOLEAN,
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT collection_event_date_basis_ck
        CHECK (date_basis IS NULL OR date_basis IN
              ('collected', 'result', 'reported', 'unknown'))
);

CREATE UNIQUE INDEX collection_event_artifact_column_uq
    ON core.collection_event (source_artifact_id, source_column_index)
    WHERE source_artifact_id IS NOT NULL
      AND source_column_index IS NOT NULL;
CREATE INDEX collection_event_person_collected_idx
    ON core.collection_event (person_id, collected_on DESC);
CREATE INDEX collection_event_person_result_idx
    ON core.collection_event (person_id, result_on DESC);
CREATE INDEX collection_event_artifact_idx
    ON core.collection_event (source_artifact_id);

-- Canonical test concepts; a specimen-specific marker may need its own analyte
-- (e.g. serum albumin is not interchangeable with urine albumin).
CREATE TABLE core.analyte (
    analyte_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    canonical_name TEXT NOT NULL UNIQUE,
    canonical_unit TEXT,
    category TEXT,
    loinc_code TEXT,
    is_derived BOOLEAN NOT NULL DEFAULT FALSE,
    description TEXT,
    value_type TEXT,
    specimen_type TEXT,
    CONSTRAINT analyte_value_type_ck
        CHECK (value_type IS NULL OR value_type IN
              ('numeric', 'categorical', 'text', 'mixed'))
);

-- For example: vendor-specific test labels -> one analyte concept.
CREATE TABLE core.analyte_alias (
    source_system_id BIGINT NOT NULL
        REFERENCES core.source_system(source_system_id),
    source_marker_name TEXT NOT NULL,
    analyte_id BIGINT NOT NULL
        REFERENCES core.analyte(analyte_id),
    PRIMARY KEY (source_system_id, source_marker_name)
);
CREATE INDEX analyte_alias_analyte_idx
    ON core.analyte_alias (analyte_id);

-- Preserve the originally reported raw value (e.g. '<10', 'Negative', 'A').
-- value_numeric is either an exact value (operator '=' or NULL) OR a numeric
-- bound when value_operator is '<', '<=', '>' or '>='. Never interpret a bound
-- as an exact observation. The source-reported flag can exist without numeric
-- reference limits (as is common in Function reports).
CREATE TABLE core.lab_result (
    lab_result_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    collection_event_id BIGINT NOT NULL
        REFERENCES core.collection_event(collection_event_id),
    analyte_id BIGINT NOT NULL
        REFERENCES core.analyte(analyte_id),
    source_row_number INTEGER
        CHECK (source_row_number IS NULL OR source_row_number > 0),
    source_marker_name TEXT NOT NULL,
    raw_value TEXT NOT NULL,
    value_numeric NUMERIC,
    value_text TEXT,
    value_operator TEXT,
    original_unit TEXT,
    normalized_value NUMERIC,
    normalized_unit TEXT,
    specimen_type TEXT,
    reference_low NUMERIC,
    reference_high NUMERIC,
    reference_unit TEXT,
    reference_text TEXT,
    reported_flag TEXT,
    is_calculated BOOLEAN,
    calculation_note TEXT,
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT lab_result_reference_range_ck
        CHECK (reference_low IS NULL OR reference_high IS NULL
               OR reference_low <= reference_high),
    CONSTRAINT lab_result_value_operator_ck
        CHECK (value_operator IS NULL OR value_operator IN
              ('=', '<', '<=', '>', '>=')),
    CONSTRAINT lab_result_numeric_operator_ck
        CHECK (value_operator IS NULL OR value_numeric IS NOT NULL)
);

CREATE UNIQUE INDEX lab_result_event_source_row_uq
    ON core.lab_result (collection_event_id, source_row_number)
    WHERE source_row_number IS NOT NULL;
CREATE INDEX lab_result_analyte_event_idx
    ON core.lab_result (analyte_id, collection_event_id);
CREATE INDEX lab_result_event_idx
    ON core.lab_result (collection_event_id);

-- A source report has presentation sections, distinct from analyte taxonomy.
-- E.g. Function displays the same calcium observation under several sections.
CREATE TABLE core.report_section (
    report_section_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_artifact_id BIGINT NOT NULL
        REFERENCES core.source_artifact(source_artifact_id),
    section_name TEXT NOT NULL,
    section_order INTEGER NOT NULL CHECK (section_order > 0),
    CONSTRAINT report_section_artifact_order_uq
        UNIQUE (source_artifact_id, section_order)
);

-- Preserve locations where a lab observation is printed without duplicating it
-- in core.lab_result when it appears more than once in the same report.
CREATE TABLE core.report_result_appearance (
    report_result_appearance_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    lab_result_id BIGINT NOT NULL
        REFERENCES core.lab_result(lab_result_id) ON DELETE CASCADE,
    report_section_id BIGINT NOT NULL
        REFERENCES core.report_section(report_section_id),
    page_number INTEGER CHECK (page_number IS NULL OR page_number > 0),
    display_order INTEGER CHECK (display_order IS NULL OR display_order > 0),
    displayed_marker_name TEXT,
    displayed_raw_value TEXT,
    displayed_flag TEXT,
    notes TEXT
);
CREATE INDEX report_result_appearance_result_idx
    ON core.report_result_appearance (lab_result_id);
CREATE INDEX report_result_appearance_section_idx
    ON core.report_result_appearance (report_section_id);

COMMENT ON SCHEMA core IS 'Normalized, source-traceable laboratory observations.';
COMMENT ON SCHEMA staging IS 'Reserved for source-specific staging and quality checks.';
COMMENT ON SCHEMA analytics IS 'Reserved for reporting views and derived analytics.';
COMMENT ON COLUMN core.collection_event.result_on IS
    'Date stated for results; not necessarily specimen collection date.';
COMMENT ON COLUMN core.collection_event.source_column_index IS
    '1-based result-column number for source files with duplicate date headers.';
COMMENT ON COLUMN core.lab_result.raw_value IS
    'Verbatim result string from source (including <, >, or qualitative text).';
COMMENT ON COLUMN core.lab_result.value_numeric IS
    'Numeric result, or numeric bound if value_operator is an inequality.';
COMMENT ON COLUMN core.lab_result.value_text IS
    'Parsed qualitative result such as NEGATIVE, A, or NONE SEEN.';
COMMENT ON COLUMN core.lab_result.reported_flag IS
    'Source classification, such as In Range, Above Range, or Below Range.';
COMMENT ON COLUMN core.lab_result.reference_text IS
    'Original reference-range wording when no numeric limits are available.';
COMMENT ON COLUMN core.lab_result.is_calculated IS
    'True=source indicates calculated, false=source indicates direct, NULL=unknown.';

COMMIT;
