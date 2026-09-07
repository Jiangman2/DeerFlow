#!/bin/bash
set -euo pipefail
umask 077
bin=/home/kingbase/install/kingbase/bin
export LD_LIBRARY_PATH="/home/kingbase/install/kingbase/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export KINGBASE_PASSWORD
KINGBASE_PASSWORD=$(cat /run/kingbase-auth/password)
: "${DB_USER:?}"
: "${DB_NAME:?}"
mkdir -p /backups/kingbase
stamp=$(date -u +%Y%m%dT%H%M%SZ)
partial="/backups/kingbase/${stamp}.dump.partial"
trap 'rm -f -- "$partial"' EXIT
"$bin/sys_dump" -h kingbase -p 54321 -U "$DB_USER" -d "$DB_NAME" -Fc -f "$partial"
"$bin/sys_restore" --list "$partial" >/dev/null
mv -- "$partial" "/backups/kingbase/${stamp}.dump"
# Retention is deliberately manual until a real restore drill is accepted.
