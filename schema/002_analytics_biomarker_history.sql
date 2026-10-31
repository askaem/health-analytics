-- Health Analytics / Phase 4: unified laboratory reporting view
-- Migration 002, following 001_health_lab_schema.sql
-- Requires the existing nine-table core schema and the analytics schema.
-- Safe to rerun. Read-only view over existing rows; does NOT change lab data.
-- Fitbit/daily metrics are intentionally excluded.
--
-- Apply on the Raspberry Pi:
--   cd /opt/health-analytics
--   docker compose exec -T postgres psql -X -v ON_ERROR_STOP=1 \
--     -U health_admin -d health < schema/002_analytics_biomarker_history.sql
--
-- Result semantics:
--   trend_value: a numeric EXACT observation expressed in canonical units.
--   bound_value: a numeric LIMIT ('<', '<=', '>', '>=') in canonical units;
--                never treat this as an observed exact value.
--   raw_value: the source's exact display value, including any qualifier.
--   A NULL trend_value can mean a text/limited result or a unit not yet mapped.
--   observation_date: collection date when known, otherwise reported result date.
--   observation_date_basis explains which date was used.
--   Section labels are aggregated so repeated appearances do not multiply rows.

BEGIN;

CREATE OR REPLACE VIEW analytics.v_biomarker_history AS
SELECT
    r.lab_result_id AS lab_result_id,
    e.person_id,
    p.label AS person_label,
    e.collection_event_id,
    e.collected_on AS collection_date,
    e.result_on AS result_date,
    COALESCE(e.collected_on, e.result_on) AS observation_date,
    CASE
        WHEN e.collected_on IS NOT NULL THEN 'collected'
        WHEN e.result_on IS NOT NULL THEN COALESCE(e.date_basis, 'reported')
        ELSE 'unknown'
    END AS observation_date_basis,
    e.source_date_text,
    e.source_column_index,
    s.source_system_id,
    s.code AS source_code,
    s.display_name AS source_name,
    art.source_artifact_id,
    art.original_filename AS source_filename,
    a.analyte_id,
    a.canonical_name AS biomarker,
    a.category AS biomarker_category,
    a.loinc_code,
    COALESCE(r.specimen_type, a.specimen_type) AS specimen_type,
    r.source_marker_name,
    r.raw_value,
    r.value_numeric AS reported_numeric_value,
    r.value_text AS reported_text_value,
    COALESCE(r.value_operator,
             CASE WHEN r.value_numeric IS NOT NULL THEN '=' END) AS value_operator,
    r.original_unit AS reported_unit,
    a.canonical_unit,
    r.normalized_value,
    r.normalized_unit,
    CASE
        WHEN r.value_numeric IS NULL THEN 'text'
        WHEN r.value_operator IN ('<', '<=', '>', '>=') THEN 'bound'
        ELSE 'exact'
    END AS result_kind,
    -- Suitable for numeric trend charts only when truly measured and unit-matched.
    CASE
        WHEN r.value_numeric IS NOT NULL
         AND COALESCE(r.value_operator, '=') = '='
        THEN u.canonical_numeric_value
    END AS trend_value,
    -- Preserve quantitative upper/lower bounds without treating as measurements.
    CASE
        WHEN r.value_operator IN ('<', '<=', '>', '>=')
        THEN u.canonical_numeric_value
    END AS bound_value,
    r.reference_low,
    r.reference_high,
    r.reference_unit,
    r.reference_text,
    r.reported_flag,
    r.is_calculated,
    r.calculation_note,
    appearances.report_sections,
    appearances.report_appearance_count
FROM core.lab_result AS r
JOIN core.collection_event AS e
  ON e.collection_event_id = r.collection_event_id
JOIN core.person AS p
  ON p.person_id = e.person_id
JOIN core.source_system AS s
  ON s.source_system_id = e.source_system_id
LEFT JOIN core.source_artifact AS art
  ON art.source_artifact_id = e.source_artifact_id
JOIN core.analyte AS a
  ON a.analyte_id = r.analyte_id
-- Do not assume source units are comparable unless they literally match
-- canonical units OR the reviewed importer explicitly supplied a normalized
-- value and matching canonical unit. NULL units match NULL units here.
CROSS JOIN LATERAL (
    SELECT CASE
        WHEN r.normalized_value IS NOT NULL
         AND r.normalized_unit IS NOT DISTINCT FROM a.canonical_unit
            THEN r.normalized_value
        WHEN r.value_numeric IS NOT NULL
         AND r.original_unit IS NOT DISTINCT FROM a.canonical_unit
            THEN r.value_numeric
        ELSE NULL
    END AS canonical_numeric_value
) AS u
-- Multiple appearances in a PDF are one lab_result: aggregate, not JOIN raw.
LEFT JOIN LATERAL (
    SELECT
        string_agg(DISTINCT sec.section_name, ', ' ORDER BY sec.section_name)
            AS report_sections,
        COUNT(ra.report_result_appearance_id)::INTEGER
            AS report_appearance_count
    FROM core.report_result_appearance AS ra
    JOIN core.report_section AS sec
      ON sec.report_section_id = ra.report_section_id
    WHERE ra.lab_result_id = r.lab_result_id
) AS appearances ON TRUE;

COMMENT ON VIEW analytics.v_biomarker_history IS
    'One row per unique laboratory result from any source; safe numeric trend_value, inequality bound_value and full provenance.';

COMMIT;

-- A successful deployment produces the view but does not modify observations.
-- Confirm counts (currently expected: InsideTracker 321, Function 109):
-- SELECT source_name, COUNT(*) AS results
-- FROM analytics.v_biomarker_history
-- GROUP BY source_name ORDER BY source_name;
