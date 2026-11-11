# Work approximate-title candidate source

This narrow candidate inherits the accepted local R14 Git parent
`28b6269ad488bc7d34778187cf3bf8dc837ce13b`. Its cloud-routing ancestor remains
`ced7db0cf083aeff02fd142a7d8fb7dcc266213d`. It includes only title-search
candidate handling, three associated Work tests and three scoped backend
notes. It does not include the separate Work media delivery branch.

The new seal qualifies all declared current bytes and exact 28b parent bytes
before exposing parent sources to the existing cloud-routing qualifier. The
cloud-routing SOURCE and its literal seal are unchanged. Its own source check
continues to verify the effective 28b view against ced7, including documentation
shared by the two layers. Newest changes cannot hide unlisted paths.

Committed-ref archives include the new archive, cloud-routing SOURCE and all
three earlier parent manifests, plus document inputs needed by both layers.
Missing seals, missing parents, changed current/parent bytes and undeclared
source deltas fail closed. Temporary independent Git tests exercise the ref
reader and missing-manifest path. Dynamic spec imports need no custom PYTHONPATH.

This is NOT_DEPLOYED source provenance, not image/production/business or
70-Pod acceptance. No media, frontend, dependency, schema or permission change
is part of this candidate. Source tools do not run models or Work API calls.

From the repository root, use Python 3.12+:

```powershell
python deploy/enterprise/verify-work-search-candidates-source-20261111.py
python deploy/enterprise/verify-work-cloud-routing-source-20261111.py
python deploy/enterprise/verify-release-source-20261023.py
python deploy/enterprise/verify-production-baseline-20261029.py
python deploy/enterprise/verify-nov01-released-source-20261102.py
python deploy/enterprise/verify-nov02-released-source-20261103.py
python deploy/enterprise/verify-released-sync-20261110.py
python -m unittest discover -s deploy/enterprise -p test_work_search_candidates_source_20261111.py
```

After committing, repeat the seven verifiers with `--ref HEAD` to qualify
immutable Git bytes. Product hashes and validation counts are finalized only
after freeze; source registration does not deploy the candidate.

## Frozen candidate validation 2026-11-11

The final frozen `file_search.py` LF SHA-256 is
`9d1d1762df7d5b9578603170a35503d2380f853155935f983a8e6cce03e77305`.
The final ten-file integrated regression passed **360 tests, 0 skipped** in
93.27 seconds. It enabled both `WORK_TEST_POSTGRES_URL` and
`WORK_TASK_TEST_POSTGRES_URL` against isolated PostgreSQL. The group covers
file search, search service/cards, ordinary cards, file-lookup routing,
cloud-lookup dialogue, file reading/content, task-state and production cases.
The previously reported 57 core tests are a subset and are not added to 360.

The final runtime hash also passed independent read-only review. No real model
or Work business calls were made in this validation. These local checks do not
establish production deployment, full image identity or 70-Pod capacity.

The seven existing backend source-manifest regression files passed **148 tests
plus 39 subtests** in 30.78 seconds. These provenance checks are recorded
separately and are not added to the 360 product-test total.

After freeze, all seven working-tree source verifiers passed, along with **71
source/rejection tests** (43.844 seconds). Immutable Git-ref verification is
repeated after the local commit; this source validation does not deploy code.
