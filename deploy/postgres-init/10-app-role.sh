#!/bin/bash
# Shared by docker-compose and the Kubernetes ConfigMap in
# deploy/k8s/base/postgres.yaml — keep the two identical, or dev stops
# matching the deployment it is supposed to rehearse.
#
# Idempotent and atomic on purpose: the entrypoint runs this only on the
# first boot of an empty volume, so a crash mid-init would otherwise leave a
# partial role configuration that no restart ever repairs. Every statement
# converges, and --single-transaction makes a failed run leave nothing.
# To repair a broken bootstrap, re-run this script by hand as the superuser.
set -euo pipefail
psql -v ON_ERROR_STOP=1 --single-transaction \
  --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -v app_user="$SKEIN_APP_USER" -v app_password="$SKEIN_APP_PASSWORD" \
  -v dbname="$POSTGRES_DB" <<-'EOSQL'
    SELECT format('CREATE ROLE %I', :'app_user')
    WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = :'app_user')
    \gexec
    ALTER ROLE :"app_user" LOGIN PASSWORD :'app_password'
        NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
    GRANT CONNECT ON DATABASE :"dbname" TO :"app_user";
    -- CREATE as well as USAGE: the application applies its own migrations
    -- at startup, so it must be able to create tables in this schema.
    GRANT USAGE, CREATE ON SCHEMA public TO :"app_user";
    -- The app role owns only this dedicated private schema. It does not get
    -- database-wide CREATE, which would let it manufacture arbitrary schemas.
    CREATE SCHEMA IF NOT EXISTS private AUTHORIZATION :"app_user";
    ALTER SCHEMA private OWNER TO :"app_user";
EOSQL
