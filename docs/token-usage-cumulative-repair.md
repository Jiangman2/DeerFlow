# Cumulative stream token-usage repair

Some enterprise OpenAI-compatible providers emit a cumulative usage snapshot
on every streamed frame. Before cumulative snapshot normalization was enabled,
LangChain added those snapshots together and persisted impossible per-call
totals. The runtime fix prevents new corruption; this document covers the
separate historical-data repair.

The final provider snapshot was not persisted independently, so historical
aggregates cannot be reconstructed exactly. The maintenance command uses a
conservative quarantine policy instead: it removes only per-call contributions
above an explicit impossible threshold and preserves every other recorded call,
including external subagent usage. `run_events` is never modified.

## Audit first

Run the command inside the Gateway container from `/app/backend`. Audit is
read-only and does not create a backup table.

```bash
PYTHONPATH=packages/harness:. .venv/bin/python \
  scripts/correct_cumulative_token_usage.py audit \
  --from-time 2026-08-24T21:18:00+08:00 \
  --to-time 2026-08-25T14:13:00+08:00 \
  --model deepseek-v4-flash \
  --minimum-suspicious-call-tokens 1000000
```

Review `affected_runs`, `suspicious_calls`, `original_total_tokens`,
`retained_total_tokens`, and the per-run list before applying anything.

## Apply with an immutable backup

Choose a unique incident ID. Mutation is refused unless `--confirm` exactly
matches it. `apply` creates `token_usage_correction_backups` and writes every
original aggregate plus metadata in the same database transaction before
updating `runs`.

```bash
CORRECTION_ID=cnooc-20260825-cumulative-usage

PYTHONPATH=packages/harness:. .venv/bin/python \
  scripts/correct_cumulative_token_usage.py apply \
  --from-time 2026-08-24T21:18:00+08:00 \
  --to-time 2026-08-25T14:13:00+08:00 \
  --model deepseek-v4-flash \
  --minimum-suspicious-call-tokens 1000000 \
  --correction-id "$CORRECTION_ID" \
  --confirm "$CORRECTION_ID"
```

Afterward, reload the operations dashboard and compare its natural-day totals
with the audit report. Keep the backup table; it is the exact rollback source.

## Roll back

```bash
CORRECTION_ID=cnooc-20260825-cumulative-usage

PYTHONPATH=packages/harness:. .venv/bin/python \
  scripts/correct_cumulative_token_usage.py rollback \
  --correction-id "$CORRECTION_ID" \
  --confirm "$CORRECTION_ID"
```

Rollback restores the original token columns, model breakdown, and run metadata
and marks the backup rows with `rolled_back_at`. It does not delete the audit
record.
