# Work cloud routing source candidate

This is a narrow source registration against 0716 parent
`ced7db0cf083aeff02fd142a7d8fb7dcc266213d`. It covers only the Work cloud-file
intent routing/dialogue correction and associated tests/design note. It does
not include the separate, unmerged Work media delivery branch.

The candidate seal qualifies current LF-normalized bytes and exact Git-parent
bytes before projecting parent sources for historical verifiers. The November
6 Work, November 9 artifact recovery and November 10 released-sync manifests
remain immutable; all three are included in committed-ref archive verification.
Missing seals, missing declared sources, changed parent/current bytes and
undeclared source deltas fail closed. An archive without its seal also fails.

This is NOT_DEPLOYED source provenance, not production/image/business or
70-Pod acceptance. No schema migration or dependency update is requested.
No production operation, model call or Work API is used by these checks.

Run offline from the repository root with Python 3.12+:

```powershell
python deploy/enterprise/verify-work-cloud-routing-source-20261111.py
python -m unittest discover -s deploy/enterprise -p test_work_cloud_routing_source_20261111.py
python deploy/enterprise/verify-release-source-20261023.py
python deploy/enterprise/verify-production-baseline-20261029.py
python deploy/enterprise/verify-nov01-released-source-20261102.py
python deploy/enterprise/verify-nov02-released-source-20261103.py
python deploy/enterprise/verify-released-sync-20261110.py
```

After the source is committed, repeat the six verifiers with `--ref HEAD`.
Working-tree verification does not establish committed or deployed bytes.
## Frozen candidate validation 2026-11-11

- The final six-file focused suite passed **212 tests, 0 skipped**, including
  the isolated PostgreSQL task-state/production cases. It covers
  `test_work_cloud_lookup_dialogue.py`, `test_work_file_lookup_routing.py`,
  `test_work_file_search_service.py`, `test_work_file_search.py`,
  `test_work_task_state.py` and `test_work_production.py`.
- A separate, nonoverlapping eleven-file regression group passed **381 tests,
  17 skipped** without the PostgreSQL test environment. It covers turn planning,
  calendar workflow/drafts/domain/batch/calendar behavior, routing boundaries,
  documents, router, cards and search cards. These skips are not counted as
  passing database tests.
- Across those two disjoint product-test groups: **17 files, 593 passed,
  17 skipped**. The focused six-file group above separately exercised isolated
  PostgreSQL; its final test table was confirmed absent after cleanup.
- The original first real-model run remains recorded as **11/20 passed**. Its
  original responses were retained; this is not represented as twenty passing
  fresh model scenarios.
- Offline replay of those original responses after correction passed **18**;
  **2** required conversation context. Replaying old responses is not a new
  real-model run.
- The final targeted follow-up exercised **6 real-model scenarios with 6 raw
  calls, 6/6 passed**, covering the additional contextual cases. This is scoped
  model-recognition evidence, not complete Work bot/OAuth/resource-read or
  production/multi-Pod acceptance.
- No media-delivery feature, frontend change, dependency-lock change or schema
  migration is part of this candidate. Deployment/business acceptance remains
  pending; the source tools themselves make no model or Work business calls.


Source qualification after freeze passed all six working-tree verifiers and
**55 source/rejection tests**. This is separate from the product-test totals
above. Immutable `--ref` verification is repeated after the local commit.

The seven existing backend source-manifest regression files also passed:
**148 tests plus 39 subtests** (34 seconds). These provenance checks are
reported separately and are not added to the 593 product-test total.
