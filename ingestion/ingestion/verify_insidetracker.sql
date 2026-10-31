-- Read-only validation; use with psql -f and verify counts after initial import.
SELECT s.code, a.original_filename, COUNT(DISTINCT e.collection_event_id) AS events,
       COUNT(r.lab_result_id) AS results
FROM core.source_artifact a
JOIN core.source_system s ON s.source_system_id = a.source_system_id
JOIN core.collection_event e ON e.source_artifact_id = a.source_artifact_id
LEFT JOIN core.lab_result r ON r.collection_event_id = e.collection_event_id
WHERE s.code = 'insidetracker'
GROUP BY s.code, a.source_artifact_id, a.original_filename
ORDER BY a.source_artifact_id;

SELECT e.source_column_index, e.source_date_text, e.result_on, COUNT(r.lab_result_id) AS results
FROM core.collection_event e
JOIN core.source_system s ON s.source_system_id = e.source_system_id
LEFT JOIN core.lab_result r ON r.collection_event_id = e.collection_event_id
WHERE s.code = 'insidetracker'
GROUP BY e.collection_event_id, e.source_column_index, e.source_date_text, e.result_on
ORDER BY e.source_column_index;

SELECT COUNT(*) AS insidetracker_aliases
FROM core.analyte_alias aa
JOIN core.source_system s ON s.source_system_id = aa.source_system_id
WHERE s.code = 'insidetracker';
