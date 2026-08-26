#!/bin/bash
# The restricted application role cannot create schemas. Create the fixed Atlas
# schema before Atlas runs its migrations.
set -euo pipefail
connection=(--username "$POSTGRES_USER" --dbname "$POSTGRES_DB")
if [[ -n ${POSTGRES_CONNINFO:-} ]]; then
  connection=(--dbname "$POSTGRES_CONNINFO")
fi
psql -v ON_ERROR_STOP=1 --single-transaction \
  "${connection[@]}" -v app_user="$SKEIN_APP_USER" <<-'EOSQL'
    CREATE SCHEMA IF NOT EXISTS ext_atlas_extension AUTHORIZATION :"app_user";
    ALTER SCHEMA ext_atlas_extension OWNER TO :"app_user";
EOSQL
