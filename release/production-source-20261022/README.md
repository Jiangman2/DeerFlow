# Sampled production Python source snapshot

Updated on 2026-10-23 using accepted release overlays: the primary Gateway
Python tree now has 582 files; the separate Runner tree has 551 files, including
the two-file OA schema compatibility patch. The original 2026-10-22 observations
below remain historical evidence. Current image bindings and source checks are
in `docs/production-source-reconciliation-20261023.md` and
`deploy/enterprise/production-source-lock-20261023.json`.

Captured from the operator-provided 2026-10-22 runtime fingerprints. The primary
`backend/app` and `backend/packages/harness` directories match the sampled Gateway.
`ppt-master-runner/backend` separately preserves all 550 sampled Runner Python files.
The components have different source versions; never substitute one for the other.

Verify with Python 3.12+:

```sh
python deploy/enterprise/verify-production-source-snapshot.py
```

The verifier checks exact LF-normalized hashes, complete Python inventories, and
syntax without importing application modules. The observed server files used LF,
so normalized source bytes reproduce the server hashes. It does not connect to a
cluster, database, or provider.

This is NOT a complete image rebuild context: dependency packages, non-Python
resources, entrypoints, build environment, and effective production configuration
are outside this source-only verification. Do not repoint a production Dockerfile
to this directory until those components have their own evidence and checks.
The existing September Runner build lock remains historical, not a claim about
the currently deployed October image.

Evidence: `deploy/enterprise/production-runtime-fingerprints-20261022.json` and
`deploy/enterprise/source-restoration-20261022.json` at repository root.
