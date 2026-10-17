# Targeted read-only follow-up

Preserve the delivered deep diagnostic and its production evidence byte-for-byte.
Fix only this new follow-up: models.max_concurrency is not a column; read the
numeric override from models.parameters->>'max_concurrency'. Never export any
other model parameters, credentials, SQL text, document data or event payloads.
Use BEGIN READ ONLY, short statement/lock timeouts and metadata/known-model keys.

Bind the exact previous SUMMARY hash and root-owned helper hash; verify current
Pod identities before/after three short read samples. No stream/benchmark/model
request, task retry, signal, restart, mutation or implicit production tuning.
Keep backend_type in wait aggregates: parallel workers are not application
connections. Validate the corrected SQL on an isolated local PostgreSQL16 with
synthetic JSON/JSONB model parameters; never reuse a development/production DB.
