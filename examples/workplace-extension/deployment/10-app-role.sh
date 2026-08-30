#!/bin/bash
# A failed bootstrap must not leave a role with partial grants. Run every
# convergent statement in one transaction so an administrator can run it again.
set -euo pipefail
connection=(--username "$POSTGRES_USER" --dbname "$POSTGRES_DB")
if [[ -n ${POSTGRES_CONNINFO:-} ]]; then
  connection=(--dbname "$POSTGRES_CONNINFO")
fi
psql -v ON_ERROR_STOP=1 --single-transaction \
  "${connection[@]}" \
  -v app_user="$SKEIN_APP_USER" -v app_password="$SKEIN_APP_PASSWORD" \
  -v dbname="$POSTGRES_DB" <<-'EOSQL'
    SELECT format('CREATE ROLE %I', :'app_user')
    WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = :'app_user')
    \gexec
    ALTER ROLE :"app_user" LOGIN PASSWORD :'app_password'
        NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
    GRANT CONNECT ON DATABASE :"dbname" TO :"app_user";
    -- Skein applies its own migrations, so the role must create tables in public.
    GRANT USAGE, CREATE ON SCHEMA public TO :"app_user";
    -- Database-wide CREATE lets the role make arbitrary schemas. Give it only
    -- the private schema that Skein owns.
    CREATE SCHEMA IF NOT EXISTS private AUTHORIZATION :"app_user";
    ALTER SCHEMA private OWNER TO :"app_user";
EOSQL
