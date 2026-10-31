-- Safe read-only validation of Function source; no private values in this script.
\echo 'Function source artifact(s), event and result counts'
SELECT s.code, a.source_artifact_id, a.original_filename, a.sha256,
       COUNT(DISTINCT e.collection_event_id) AS events,
       COUNT(DISTINCT r.lab_result_id) AS distinct_results,
       COUNT(DISTINCT ra.report_result_appearance_id) AS report_appearances
FROM core.source_artifact a
JOIN core.source_system s ON s.source_system_id=a.source_system_id
LEFT JOIN core.collection_event e ON e.source_artifact_id=a.source_artifact_id
LEFT JOIN core.lab_result r ON r.collection_event_id=e.collection_event_id
LEFT JOIN core.report_result_appearance ra ON ra.lab_result_id=r.lab_result_id
WHERE s.code='function'
GROUP BY s.code, a.source_artifact_id, a.original_filename, a.sha256
ORDER BY a.source_artifact_id;
\echo 'Function report section counts (count repeated presentation appearances, not unique lab results)'
SELECT sec.section_order, sec.section_name,
       COUNT(ra.report_result_appearance_id) AS displayed_entries
FROM core.report_section sec
JOIN core.source_artifact a ON a.source_artifact_id=sec.source_artifact_id
JOIN core.source_system s ON s.source_system_id=a.source_system_id
LEFT JOIN core.report_result_appearance ra ON ra.report_section_id=sec.report_section_id
WHERE s.code='function'
GROUP BY sec.source_artifact_id, sec.section_order, sec.section_name
ORDER BY sec.source_artifact_id, sec.section_order;
\echo 'Categorical, limited, and flagged Function lab results'
SELECT e.result_on, a.canonical_name, r.raw_value, r.value_operator,
       r.value_numeric, r.value_text, r.reported_flag, r.original_unit
FROM core.lab_result r
JOIN core.collection_event e ON e.collection_event_id=r.collection_event_id
JOIN core.source_system s ON s.source_system_id=e.source_system_id
JOIN core.analyte a ON a.analyte_id=r.analyte_id
WHERE s.code='function'
  AND (r.value_operator NOT IN ('=', '') OR r.value_numeric IS NULL
       OR r.reported_flag IN ('Above Range', 'Below Range'))
ORDER BY e.result_on, a.canonical_name;
\echo 'Cross-source biomarker concepts (at least one result from each source)'
SELECT a.canonical_name, COUNT(DISTINCT s.code) AS source_count,
       STRING_AGG(DISTINCT s.code, ', ' ORDER BY s.code) AS source_codes
FROM core.lab_result r
JOIN core.analyte a ON a.analyte_id=r.analyte_id
JOIN core.collection_event e ON e.collection_event_id=r.collection_event_id
JOIN core.source_system s ON s.source_system_id=e.source_system_id
WHERE s.code IN ('function', 'insidetracker')
GROUP BY a.analyte_id, a.canonical_name
HAVING COUNT(DISTINCT s.code) > 1
ORDER BY a.canonical_name;
