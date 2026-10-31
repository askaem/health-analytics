#!/usr/bin/bash

cd /opt/health-analytics

## Backup the health database to a dump file
docker compose exec -T postgres \
  pg_dump -U health_admin -Fc health \
  > "backups/health-$(date +%F).dump"

## Apply the Metabase roles schema to the health database
docker compose exec -T postgres \
  psql -X -v ON_ERROR_STOP=1 \
  -U health_admin -d postgres \
  < schema/003_metabase_roles.sql

## Build the passwords and session keys
METABASE_APP_PASSWORD=$(openssl rand -base64 32)
METABASE_ENCRYPTION_KEY=$(openssl rand -hex 24)
METABASE_SESSION_SECRET_KEY=$(openssl rand -base64 32)

cat <<EOF >> /opt/health-analytics/.env
METABASE_APP_PASSWORD=$METABASE_APP_PASSWORD
METABASE_ENCRYPTION_KEY=$METABASE_ENCRYPTION_KEY
METABASE_SESSION_SECRET_KEY=$METABASE_SESSION_SECRET_KEY
EOF

