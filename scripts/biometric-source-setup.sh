#!/usr/bin/bash

cd /opt/health-analytics

docker compose exec -T postgres \
  psql -X -v ON_ERROR_STOP=1 \
  -U health_admin -d health <<'SQL'

BEGIN;

INSERT INTO core.person (label)
VALUES ('primary')
ON CONFLICT (label) DO NOTHING;

INSERT INTO core.source_system (code, display_name)
VALUES
    ('insidetracker', 'InsideTracker'),
    ('function', 'Function Health')
ON CONFLICT (code) DO NOTHING;

COMMIT;

SELECT * FROM core.person;
SELECT * FROM core.source_system;

SQL