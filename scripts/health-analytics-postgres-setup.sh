#!/usr/bin/bash

## Prep directories for postgres setup
sudo mkdir -p /opt/health-analytics/{backups,imports}
sudo chown -R $USER:$USER /opt/health-analytics
cd /opt/health-analytics

PG_PASSWORD=$(openssl rand -base64 32)
echo $POSTGRES_PASSWORD
cat <<EOF > /opt/health-analytics/.env
POSTGRES_DB=health
POSTGRES_USER=health_admin
POSTGRES_PASSWORD=$PG_PASSWORD
EOF

chmod 600 /opt/health-analytics/.env

cat <<'EOF' > /opt/health-analytics/compose.yaml
services:
  postgres:
    image: postgres:17-bookworm
    container_name: health-analytics-postgres

    restart: unless-stopped

    environment:
      POSTGRES_DB: ${POSTGRES_DB}
      POSTGRES_USER: ${POSTGRES_USER}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}

    volumes:
      - /data/databases/health-analytics:/var/lib/postgresql/data
      - ./backups:/backups
      - ./imports:/imports:ro

    ports:
      - "127.0.0.1:5432:5432"

    healthcheck:
      test:
        [
          "CMD-SHELL",
          "pg_isready -U ${POSTGRES_USER} -d ${POSTGRES_DB}"
        ]
      interval: 10s
      timeout: 5s
      retries: 5

    networks:
      - health-analytics-net

networks:
  health-analytics-net:
    driver: bridge
EOF