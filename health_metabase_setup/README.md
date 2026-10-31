# Metabase for health-analytics on Raspberry Pi

## Prerequisites

- Existing `compose.yaml` service `postgres` and network `health-net`.
- PostgreSQL database `health` contains `analytics.v_biomarker_history` (430 records).
- Run from `/opt/health-analytics` on Raspberry Pi.

## 1. Install overlay and SQL

Copy `compose.metabase.yaml` to `/opt/health-analytics/compose.metabase.yaml`.
Copy `003_metabase_roles.sql` to `/opt/health-analytics/schema/003_metabase_roles.sql`.

## 2. Add secrets to `.env`

Generate separate values locally, e.g. `openssl rand -hex 24` for `METABASE_APP_PASSWORD`, and `openssl rand -base64 32` twice for the two keys.

    METABASE_APP_PASSWORD=<same password you will enter for metabase_app role>
    METABASE_ENCRYPTION_KEY=<generated key; keep for restores>
    METABASE_SESSION_SECRET_KEY=<second generated key>

Protect `.env`: `chmod 600 /opt/health-analytics/.env`; do not commit or upload it.

## 3. Create Postgres database and roles

    docker compose exec -T postgres psql -X -v ON_ERROR_STOP=1 -U health_admin -d postgres < schema/003_metabase_roles.sql

Then:

    docker compose exec postgres psql -U health_admin -d postgres

Use interactive psql meta-commands (do not put passwords in shell history):

    \password metabase_app
    \password metabase_reader
    \q

The `metabase_app` password must match the .env value. Make `metabase_reader` a DIFFERENT strong password, entered later in Metabase's data connection UI.

## 4. Validate read-only permissions

    docker compose exec postgres psql -U health_admin -d health -c 'SET ROLE metabase_reader; SELECT count(*) FROM analytics.v_biomarker_history;'

Expected count: 430.

## 5. Start Metabase

    docker compose -f compose.yaml -f compose.metabase.yaml config --quiet
    docker compose -f compose.yaml -f compose.metabase.yaml pull metabase
    docker compose -f compose.yaml -f compose.metabase.yaml up -d metabase
    docker compose -f compose.yaml -f compose.metabase.yaml ps
    docker compose -f compose.yaml -f compose.metabase.yaml logs -f --tail=80 metabase

## 6. Access securely over SSH tunnel

From your desktop (not from Pi):

    ssh -L 3000:127.0.0.1:3000 <pi-user>@<pi-ip-or-hostname>

Visit http://localhost:3000 on the desktop. Do not expose TCP 3000 directly to the Internet.

## 7. Setup in Metabase UI

- Create the Metabase admin account.
- Add data source: PostgreSQL
- Display name: Health Analytics
- Host: `postgres`, port: `5432`
- Database: `health`
- Username: `metabase_reader`
- Password: separate reader password entered in step 3
- Schemas to sync: `Only these...` -> `analytics`
- Save and sync.

To test, use New -> SQL query in Metabase:

    SELECT observation_date, source_name, biomarker, trend_value, canonical_unit
    FROM analytics.v_biomarker_history
    WHERE biomarker = 'ApoB' AND trend_value IS NOT NULL
    ORDER BY observation_date;

## 8. Back up both databases

    docker compose exec -T postgres pg_dump -U health_admin -Fc health > "backups/health-$(date +%F).dump"
    docker compose exec -T postgres pg_dump -U health_admin -Fc metabase_app > "backups/metabase_app-$(date +%F).dump"

Copy backups to encrypted off-device storage. Protect `.env` and encryption/session keys independently.

## Maintenance notes

- Image pinned to `metabase/metabase:v0.64.2.8` (verified ARM64 on Docker Hub in Oct 2026). Review future security updates before changing tag.
- Environment secret interpolation with `$` requires care in `.env`; generated hex/base64 secrets avoid `$`.
- JAVA_OPTS max 1GB heap; monitor `docker stats`, adjust for Pi RAM.
- Compose overlay must be supplied when running future compose commands touching Metabase; do not run `down --remove-orphans` with only the base compose file.
