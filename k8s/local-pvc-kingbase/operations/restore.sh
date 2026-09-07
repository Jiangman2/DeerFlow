#!/bin/bash
set -euo pipefail
bin=/home/kingbase/install/kingbase/bin
export LD_LIBRARY_PATH="/home/kingbase/install/kingbase/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export KINGBASE_PASSWORD
KINGBASE_PASSWORD=$(cat /run/kingbase-auth/password)
: "${DB_USER:?}"
: "${RESTORE_DATABASE:?}"
: "${RESTORE_FILE:?}"
# Restore only to an operator-created, empty scratch database. No drop/clean mode.
[[ "$RESTORE_DATABASE" =~ ^deerflow_restore_check_[a-z0-9_]+$ ]]
[[ "$RESTORE_FILE" =~ ^[0-9]{8}T[0-9]{6}Z.dump$ ]]
file="/backups/kingbase/$RESTORE_FILE"
test -s "$file"
objects=$("$bin/ksql" -X -h kingbase -p 54321 -U "$DB_USER" -d "$RESTORE_DATABASE" -v ON_ERROR_STOP=1 -Atc "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'")
[ "$objects" = 0 ]
"$bin/sys_restore" --exit-on-error --single-transaction --no-owner --no-privileges -h kingbase -p 54321 -U "$DB_USER" -d "$RESTORE_DATABASE" "$file"
for table in users threads_meta runs run_events; do
    "$bin/ksql" -X -h kingbase -p 54321 -U "$DB_USER" -d "$RESTORE_DATABASE" -v ON_ERROR_STOP=1 -Atc "SELECT '$table', count(*) FROM $table"
done
