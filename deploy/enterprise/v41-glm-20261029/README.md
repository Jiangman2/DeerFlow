# V4.1 configuration preparation after PR #21

This archive records the operator-confirmed state on 2026-10-29. The application
baseline remains Gateway **389**, Frontend **231**, PPT Runner **55**. V4.1 is
**prepared, not activated**. Merging this archive does not deploy or enable it.

## Evidence and current state

| Item | Confirmed state | Evidence |
| --- | --- | --- |
| Application adapter | Already shipped in Gateway 389; unchanged here | `production-source-lock-20261029.json` in the parent directory |
| Credential reference | V4.1 uses `$GLM_5_3_FLASH_API_KEY`, not the V4 credential | `model.yaml`, `provider-acceptance-field-summary.json` |
| Provider verification | Seven synthetic requests passed, all HTTP 200 | Server receipt SHA256 `9fb9f3e11de46b4f2441abd1e317e349537553eba963515f9bec7c0c2a0b58df` |
| Candidate ConfigMap | Created and verified, not mounted by Gateway | `preparation-field-summary.json` |
| Gateway rollout | Deferred by the operator; zero deployment patches in preparation | `SPLIT-ENABLEMENT.md` |
| Default model | Remains `deepseek-v4-flash` | Preparation receipt |
| Browser/business acceptance | Pending actual activation | No rollout acceptance receipt exists in this archive |

The candidate ConfigMap is
`deer-flow-config-v41-glm-20261029-83f63163c359`, UID
`c36badad-02bf-4c4b-8ad5-6994ff0522e5`. The preparation report remains on datax-01 at
`/var/lib/portal-v41-glm-preparation-20261029-r1/REPORT.json` (9,871 bytes,
SHA256 `3fe88df30ff1801f1732264d77a178e9d6b0577a678cfc09870de25915c946fd`).
The field summaries are projections of operator-pasted JSON, not copies of those
original server report bytes. Private configuration snapshots and credentials
are deliberately absent.

## Tool history

- `host.py` / `package.py`: original verification tool; its operator run stopped
  before provider requests with `OUTPUT_BOUND`.
- `host_r2.py` / `package_r2.py`: scoped Pod index and bounded reader; the seven
  provider checks succeeded using the existing GLM credential. Do not rerun them.
- `enable_host.py` / `package_enable.py`: superseded one-step activation design,
  retained as tested source used by the split packager. It was not executed.
- `split_host.py` / `package_split.py`: separate preparation and later rollout.
  Preparation succeeded; rollout remains deferred. A plain rollout restart is
  not a substitute for mounting the prepared configuration.

The `VERIFICATION*.json` files describe qualification at the time of each tool's
delivery. Their earlier "pending" states are historical; the later field
summaries above establish what was actually executed. Preserve all such records.
Before a future authorized rollout, reconcile any intervening production changes;
the tool must refuse a changed deployment baseline rather than overwrite it.

## Offline verification

From the repository root, using Python 3.12+:

```bash
python -B deploy/enterprise/verify-v41-preparation-20261029.py
python -B deploy/enterprise/verify-production-baseline-20261029.py --ref HEAD
```

`ARCHIVE.json` seals the 24 original payloads. The first command checks those raw
bytes, reconstructs all five delivered tools in memory, verifies their delivery
digests and the preparation/rollout bundle binding, and checks the receipt state.
It never invokes an operator tool, Kubernetes, or a provider. The dedicated CI
also runs the existing synthetic provider/configuration tests and stages the
sealed host tools in a fresh temporary directory for Linux tests. Real HTTP is
limited to the reader test's loopback fake Kubernetes API.

Application source files, the production source lock, the default model, and the
original active ConfigMap are unchanged. This PR excludes the separate natural-
language data-lake trigger defect, the paused Work update, and WeKnora changes.
