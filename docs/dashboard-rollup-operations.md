# Administrator dashboard rollup operations

The dashboard is an administrative reporting surface, not part of DeerFlow's
conversation execution path. Its maintenance and queries therefore fail or
degrade before they are allowed to consume unbounded resources needed by chats,
scheduled runs, or digital employees.

In enterprise mode both interactive analytics and maintenance source scans use
the standby. The maintenance process opens a separate primary connection only
for small atomic rollup/state upserts; it bounds change cursors by the standby's
last replayed transaction timestamp so replication lag cannot skip records.

## Runtime design

- `DEER_FLOW_DASHBOARD_ROLLUPS_ENABLED=false` is the compatibility default for
  existing installations. Additive migrations may be applied while historical
  summary, ranking, and external-platform click reads continue through the
  bounded cached source path. This prevents an empty fact table from appearing
  as missing history during rollout.
- Set `DEER_FLOW_DASHBOARD_ROLLUPS_ENABLED=true` only after the initial daily and
  hourly backfills are complete, the readiness endpoint reports no required
  gaps, and scheduled maintenance has completed successfully. The remaining
  rules in this section describe that enabled mode.
- Rolling windows with at least six complete hours use one immutable hourly
  fact per completed Shanghai clock hour from the latest 72 hours. A rolling
  24-hour query therefore reads about 23 hourly rows; only its two fractional
  boundary slices, totaling at most one hour, come from source records.
- Windows with at least two complete Asia/Shanghai calendar days read one
  immutable daily fact per completed day. Their edge-day slices reuse hourly
  facts before reading any fractional edge hours.
- If any required completed day/hour is absent or has the wrong metric version, the API
  returns `503` with `Retry-After` and does not scan the historical source
  tables. The frontend remains usable and normal DeerFlow requests are
  unaffected.
- A cache miss admits one dashboard producer by default. Another distinct
  dashboard query waits at most 250 ms and then receives `429`. PostgreSQL
  statement and lock waits are bounded.
- `DEER_FLOW_DASHBOARD_DATABASE_URL` points dashboard reads to a PostgreSQL
  standby through `pool_size=1` and `max_overflow=0`. Enterprise sets
  `DEER_FLOW_DASHBOARD_REQUIRE_READ_REPLICA=true`: Gateway verifies
  `transaction_read_only=on` and `pg_is_in_recovery()=true`. Missing or invalid
  configuration disables only the dashboard and never falls back to the primary
  application database.
- Redis remains only a response cache. PostgreSQL daily/hourly facts are the durable
  metric layer and can be rebuilt from source records.
- Slow-run and high-token cards use two database-side top-N queries from
  `/api/admin/dashboard/run-rankings`; the dashboard page never materializes the
  legacy full current/comparison observability window.
- Historical external-platform click totals and the fixed 30-day trend also
  come from daily facts after activation. Until those dates are complete, only
  that panel shows a preparation state; enabled mode never falls back to
  scanning all audit-log rows.

## Schema and metric version

Migrations `0016_dashboard_daily_rollups` and
`0017_dashboard_hourly_rollups` create:

- `dashboard_daily_rollups`: atomic per-day payload, source row counts, build
  duration, refresh time, and `schema_version`.
- `dashboard_rollup_state`: successful source-change scan cursor plus the
  durable lists of changed historical days and hours still awaiting refresh.
- `dashboard_hourly_rollups`: atomic per-hour payloads for the bounded hot
  window, with the same source counts, build duration, refresh time, and metric
  version as daily facts.

Changing metric meaning requires incrementing `ROLLUP_SCHEMA_VERSION` and
backfilling the new version. Do not silently reinterpret old payloads.

## Local and pre-release verification

Run from `backend/`:

```bash
uv run python scripts/manage_dashboard_rollups.py status
uv run python scripts/manage_dashboard_rollups.py backfill --max-days 1
uv run python scripts/manage_dashboard_rollups.py refresh-changes --max-days 1
uv run python scripts/manage_dashboard_rollups.py backfill-hours --max-hours 24
uv run python scripts/manage_dashboard_rollups.py refresh-changed-hours --max-hours 2
uv run python scripts/manage_dashboard_rollups.py status
```

Run the Gateway/Alembic upgrade first. The maintenance CLI intentionally opens
its own `NullPool` connections and does not run schema bootstrap or migrations,
so a daily analytics job never takes the application's migration lock.

The authenticated readiness endpoint is read-only:

```text
GET /api/admin/dashboard/rollups/status
```

It reports expected, ready, missing, and pending-refresh day/hour counts. There
is no HTTP refresh endpoint by design.

For PostgreSQL, inspect plans before enabling scheduled work:

```bash
uv run python scripts/manage_dashboard_rollups.py explain \
  --start-day 2026-01-01 --end-day 2026-01-31
```

Only during a controlled maintenance window, add `--analyze`. It executes the
read-only statements but remains subject to the rollup statement timeout.
Confirm that the rollup query uses its primary-key date range and the raw edge
query uses `ix_runs_created_at`; reject a sequential scan over a large `runs`
table.

## Strongest-isolation PostgreSQL standby

The enterprise overlay deploys `postgres-dashboard-replica` on `datax-03` with
the independent local PV
`/opt/deerflow-storage/dashboard-postgresql`. The primary remains on
`datax-02`. Dashboard reads use a login with only CONNECT, schema USAGE, and
SELECT grants. Normal DeerFlow repositories never receive the replica URL.

Before applying the overlay, create the dedicated storage path and Secret. Use
different generated passwords for streaming replication and dashboard reads;
do not commit the rendered Secret:

```bash
sudo install -d -o 999 -g 999 -m 0700 /opt/deerflow-storage/dashboard-postgresql
REPLICATION_PASSWORD="$(openssl rand -hex 32)"
DASHBOARD_PASSWORD="$(openssl rand -hex 32)"
kubectl -n deer-flow create secret generic deer-flow-dashboard-postgres \
  --from-literal=REPLICATION_USER=deerflow_dashboard_replica \
  --from-literal=REPLICATION_PASSWORD="$REPLICATION_PASSWORD" \
  --from-literal=DASHBOARD_USER=deerflow_dashboard \
  --from-literal=DASHBOARD_PASSWORD="$DASHBOARD_PASSWORD" \
  --from-literal=DATABASE_URL="postgresql://deerflow_dashboard:${DASHBOARD_PASSWORD}@postgres-dashboard-replica:5432/deerflow" \
  --dry-run=client -o yaml | kubectl apply -f -
unset REPLICATION_PASSWORD DASHBOARD_PASSWORD
```

Run migration `0017` on the primary before starting the standby. During the
initial base-backup bootstrap, the replica init container creates the two
least-privilege roles, grants current and future table SELECT access, creates
one physical replication slot, and takes a base backup with a spread checkpoint
and a 32 MiB/s transfer cap. Schedule that first copy in a maintenance window;
the rate limit reduces rather than eliminates primary I/O. The primary caps
retained slot WAL at 4 GiB so a failed standby cannot fill the primary disk
indefinitely. Credential rotation is a controlled standby rebuild: update the
Secret, remove only the dedicated replica PVC after confirming the target, and
let the init container take a fresh base backup. If `wal_status=lost`, use the
same rebuild procedure; do not point the dashboard at the primary as a
temporary workaround.

Verify physical isolation without printing credentials:

```bash
kubectl -n deer-flow rollout status statefulset/postgres-dashboard-replica --timeout=10m
kubectl -n deer-flow exec statefulset/postgres-dashboard-replica -- sh -ec \
  'PGPASSWORD="$DASHBOARD_PASSWORD" psql -X -U "$DASHBOARD_USER" -d "$POSTGRES_DB" -tAc "SELECT pg_is_in_recovery(), current_setting('"'"'transaction_read_only'"'"'), now()-pg_last_xact_replay_timestamp()"'
```

Expected values are `t`, `on`, and a bounded replay delay. Gateway startup
remains available if this check fails, but dashboard endpoints return 503 and
explicitly refuse primary fallback.

## Initial backfill

The default is deliberately one day per invocation. During a measured
maintenance window, an operator may use up to 31 days:

```bash
uv run python scripts/manage_dashboard_rollups.py backfill --max-days 31
uv run python scripts/manage_dashboard_rollups.py backfill-hours --max-hours 72
```

Observe database CPU, read IOPS, replica lag, lock waits, Gateway latency, and
the reported per-day `duration_ms`. Stop increasing batch size if normal-service
latency changes. Re-running a day is idempotent and atomically replaces that
day; readers see either the old complete fact or the new complete fact.

Do not enable rollup reads yet. First confirm `status` reports no missing daily
facts for the intended historical window and no missing hourly facts in the
retained hot window. Missing facts are not zero-valued data and must never be
treated as deleted history.

## Scheduled maintenance

`k8s/local-pvc/dashboard-rollup-cronjob.yaml` is installed with `suspend: true`.
After migrations, a successful one-day/hour run, plan review, daily backfill,
72-hour backfill, and standby verification, enable it explicitly:

```bash
kubectl -n deer-flow patch cronjob dashboard-rollup-maintenance \
  --type merge -p '{"spec":{"suspend":false}}'
```

Wait for one maintenance Job to succeed and recheck rollup status. Activate the
read path last:

```bash
kubectl -n deer-flow set env deployment/gateway \
  DEER_FLOW_DASHBOARD_ROLLUPS_ENABLED=true
kubectl -n deer-flow rollout status deployment/gateway --timeout=10m
```

The full enterprise desired-state overlay already carries this flag because it
assumes its replica, backfill, status, and CronJob prerequisites were completed.
Do not apply only that environment fragment to an unprepared existing cluster.

The job runs every five minutes with `concurrencyPolicy: Forbid`, no retry
storm, a five-minute active deadline, 100m CPU/256 MiB hard limits, and
30-second SQL statements. It processes at most one changed day and two changed
or missing hours; historical daily backfill is manual. A PostgreSQL transaction
advisory lock prevents another process from rebuilding the same bucket.

## Failure and rollback behavior

- Maintenance failure leaves prior daily/hourly facts intact and retains pending
  work for a later invocation.
- Read failure affects only the admin dashboard. It does not stop Gateway
  startup or alter normal request pools.
- To stop all maintenance immediately, set the CronJob back to
  `spec.suspend=true`. Existing cached responses continue to work; required
  uncovered ranges fail fast safely.
- To recover read availability after an unexpected fact gap, set
  `DEER_FLOW_DASHBOARD_ROLLUPS_ENABLED=false` and restart Gateway. This restores
  the bounded compatibility read path while the facts are repaired; it does not
  delete or rewrite source history.
- Do not delete source records or rollup tables as an operational retry. Fix the
  cause, rerun a bounded day, and verify the status endpoint.

## Future scale

The daily/hourly fact contract is intentionally independent of its storage engine. If
retention, concurrency, or dimensionality later requires Doris or ClickHouse,
keep the same metric version, Shanghai-day boundaries, completeness rules, and
fail-fast behavior while replacing only the fact reader/writer adapter. This
avoids a second dashboard metric definition during an OLAP migration.
