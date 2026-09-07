#!/bin/bash
# Deliberately avoid the vendor daemon/cron entrypoint: keep database as PID 1.
set -euo pipefail
umask 077
bin=/home/kingbase/install/kingbase/bin
export LD_LIBRARY_PATH="/home/kingbase/install/kingbase/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
: "${DATA_DIR:=/home/kingbase/userdata/data}"
: "${DB_USER:?DB_USER is required}"
password_file=/run/kingbase-auth/password
license_file="$bin/license.dat"
test -s "$password_file"
test -s "$license_file"
marker="$DATA_DIR/.deerflow-init-complete"
if [ ! -f "$marker" ]; then
    mkdir -p "$DATA_DIR"
    if [ -n "$(ls -A "$DATA_DIR")" ]; then
        echo 'Refusing to initialize a non-empty, unrecognized data directory' >&2
        exit 1
    fi
    "$bin/initdb" -U "$DB_USER" --pwfile="$password_file" -D "$DATA_DIR" -E UTF8 -m pg -A scram-sha-256
    # TCP remains password-authenticated; Service is ClusterIP only.
    printf '\nhost all all 0.0.0.0/0 scram-sha-256\nhost all all ::/0 scram-sha-256\n' >> "$DATA_DIR/sys_hba.conf"
    test -s "$DATA_DIR/SYS_VERSION"
    test -s "$DATA_DIR/kingbase.conf"
    test -s "$DATA_DIR/sys_hba.conf"
    touch "$marker"
fi
test -s "$DATA_DIR/SYS_VERSION"
test -s "$DATA_DIR/kingbase.conf"
test -s "$DATA_DIR/sys_hba.conf"
exec "$bin/kingbase" -D "$DATA_DIR" -p 54321 -c listen_addresses='*'
