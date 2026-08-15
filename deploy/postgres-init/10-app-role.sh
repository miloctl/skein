# Shared by docker-compose and the Kubernetes ConfigMap in
# deploy/k8s/base/postgres.yaml — keep the two identical, or dev stops
# matching the deployment it is supposed to rehearse.
#!/bin/bash
set -euo pipefail
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -v app_user="$SKEIN_APP_USER" -v app_password="$SKEIN_APP_PASSWORD" \
  -v dbname="$POSTGRES_DB" <<-'EOSQL'
    CREATE ROLE :"app_user" LOGIN PASSWORD :'app_password'
        NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
    GRANT CONNECT ON DATABASE :"dbname" TO :"app_user";
    -- CREATE as well as USAGE: the application applies its own migrations
    -- at startup, so it must be able to create tables in this schema.
    GRANT USAGE, CREATE ON SCHEMA public TO :"app_user";
EOSQL
