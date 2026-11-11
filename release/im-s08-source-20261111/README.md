# IM S08 source candidate (2026-11-11)

`SOURCE.json` seals the scoped IM S08 change set against the enterprise `0716`
lineage at base commit `ced7db0cf083aeff02fd142a7d8fb7dcc266213d`.

The candidate registers the Feishu thread archive, the read-only `/api/im`
session-tree surface and the artifact-to-Feishu delivery button that were
developed on this branch. It does not deploy anything, does not apply the
`0023`/`0024` migrations and does not claim production or business acceptance.

Every declared row carries the parent digest at the base commit and the
candidate digest of the current bytes, exactly like the earlier November 3/6
successors. The `work_integration_source_20261106.py` reader calls this module
first, verifies both sides, and then projects the parent bytes into the older
verifiers so their sealed inventories keep their original meaning.

Run with Python 3.12+:

```bash
python deploy/enterprise/verify-im-s08-source-20261111.py --ref HEAD
python deploy/enterprise/test_im_s08_source_20261111.py
python deploy/enterprise/verify-production-baseline-20261029.py --ref HEAD
```

`production_acceptance_claimed` stays `false`; `schema_migration_requested`
stays `true` until an operator applies migration `0024_im_s08_archive` and
records separate acceptance.