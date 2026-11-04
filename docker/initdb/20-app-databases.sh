#!/usr/bin/env bash
# One database and one login role per app, from APP_DATABASES ("name:aidb(0|1):ext,ext ..."; rendered by `ecoctl dev render` from ecosystem.yaml).
# Idempotent: it runs once when the volume is created (docker-entrypoint-initdb.d) and `ecoctl dev up` runs it again, so an app added later gets its database too.
#   - the role is a plain login role: no superuser, no createdb, no createrole, and NO password here (ecoctl sets it over stdin; until then it cannot sign in over TCP)
#   - it owns its database only; PUBLIC may not connect to it, and the role may not connect to the others
#   - extensions are created here, as the superuser, so the apps' own `CREATE EXTENSION IF NOT EXISTS` finds them; a name the image lacks is skipped with a notice
#   - `aidb_users` (the AIDB functions) is granted only where the second field is 1. It does NOT give access to the app's tables.
set -Eeuo pipefail
: "${APP_DATABASES:?APP_DATABASES is not set}"
PSQL=(psql -v ON_ERROR_STOP=1 -q --username "${POSTGRES_USER:-postgres}")
for spec in $APP_DATABASES; do
  IFS=: read -r name aidb exts <<<"$spec"
  [[ $name =~ ^[a-z][a-z0-9_]{1,30}$ ]] || { echo "bad database name: $name" >&2; exit 1; }
  if ! "${PSQL[@]}" -d postgres -Atc "SELECT 1 FROM pg_roles WHERE rolname='$name'" | grep -q 1; then
    "${PSQL[@]}" -d postgres -c "CREATE ROLE $name LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS"
  fi
  if ! "${PSQL[@]}" -d postgres -Atc "SELECT 1 FROM pg_database WHERE datname='$name'" | grep -q 1; then
    "${PSQL[@]}" -d postgres -c "CREATE DATABASE $name OWNER $name ENCODING 'UTF8'"
  fi
  "${PSQL[@]}" -d postgres -c "REVOKE ALL ON DATABASE $name FROM PUBLIC" -c "GRANT ALL ON DATABASE $name TO $name"
  list="${exts//,/ }"
  [[ $aidb == 1 ]] && list="vector $list aidb"
  for ext in $list; do
    if "${PSQL[@]}" -d postgres -Atc "SELECT 1 FROM pg_available_extensions WHERE name='$ext'" | grep -q 1; then
      if [[ $ext == aidb ]]; then "${PSQL[@]}" -d "$name" -c "CREATE EXTENSION IF NOT EXISTS aidb CASCADE"; else "${PSQL[@]}" -d "$name" -c "CREATE EXTENSION IF NOT EXISTS \"$ext\""; fi
    else
      echo "notice: extension $ext is not in this image: skipped for $name" >&2
    fi
  done
  if [[ $aidb == 1 ]]; then "${PSQL[@]}" -d postgres -c "GRANT aidb_users TO $name"; fi
done
