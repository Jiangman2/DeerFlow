# Gateway 406 / Frontend 239: November 3 read-only observation

This is a historical checkpoint, not the current production baseline. The
subsequent Frontend 240 release included the three dashboard copy changes listed
below; see [the dashboard navigation release](dashboard-navigation-20261103.md).
The original operator receipt and sealed source manifest remain unchanged.

The operator supplied `log(21).txt` after running the read-only comparison tool
on datax-01. This checkpoint relates the observed production sources to enterprise
commit `443cbd8ab6e2e74298075976fa68af079c1f7100`; it is not a new deployment or
an exact whole-image/dependency identity claim.

| Component | Observed image digest | Coverage |
| --- | --- | --- |
| Gateway 406 | `sha256:f8faded6240cbc6d98c7dbfe30388a7ff4c3f4df243f0d6409b1f6a026c19b38` | All 70 Pod image/ready metadata records; one Python-source and startup-environment sample on each of nodes 0018–0022 |
| Frontend 239 | `sha256:97a84226f1c9f77871c05cebaae1fa42b7525644edc844bccf75e29518ba44c1` | Both Pod image/ready metadata records; same previously accepted frontend image |

Deployment and Pod metadata remained stable during the inspection. Each of the
five Gateway samples contained the same 635 expected Python files: 613 matched
the enterprise LF-normalized hashes, and 22 matched archived format variants.
The original inspector already recognized eight variants; its 14 `mismatch`
entries omitted earlier October/November formatting inventories. The offline
reconciliation checks each exact historical hash and AST equivalence against the
enterprise source. All 14 are format-only differences. Unknown hashes or changed
executable syntax remain failures. The original receipt and inspector are kept
unchanged under `release/production-observation-20261103-gw406/`.

All sampled Gateway process-start environments report:

- `OA_POLICY_FLOW_RUN_TIMEOUT_SECONDS=3600`;
- `OA_POLICY_FLOW_ENABLED=true`, `OA_POLICY_FLOW_MULTI_RESULT_ENABLED=true`;
- `OA_POLICY_FLOW_CALLBACK_ENABLED=false`;
- `DEERFLOW_BROWSER_NAVIGATE_ENABLED=false`;
- `DEERFLOW_LLM_PERFORMANCE_LOGS=true`, batch `20261103-evening`;
- `DEER_FLOW_MODEL_METRICS_ENABLED=true`.

`OA_POLICY_FLOW_ENABLED` is absent from the explicit Pod `env` list, but is
present and true in both the exec environment and sampled Gateway process-start
environments. Its precise injection source was not queried; the absent explicit
entry does not establish that OA is disabled. The configuration-file SHA256 is
unchanged at `bf39d16f673d2259a0430357e7a9a435482add27194aa947a0e34cdd594f1fb7`.

## Known frontend release gap

Relative to the previously accepted frontend, the enterprise dashboard has only
these three pending copy changes in
`frontend/src/app/workspace/admin/dashboard/page.tsx`:

- `全部失败运行（` → `最近失败运行（`;
- `全历史 · 点击可查看只读错误诊断` → `最新 1000 条范围内筛选 · 点击可查看只读错误诊断`;
- `没有匹配该账号或姓名的失败运行` → `最新 1000 条中没有匹配该账号或姓名的失败运行`.

`frontend/tests/e2e/dashboard-failures-recovery.spec.ts` updates the corresponding
assertions and is not production runtime code. Existing formatter-only differences
are tracked separately. This record does not publish the copy changes.

## Evidence and limits

`OPERATOR.json` preserves the exact JSON line from the supplied log, excluding
login banners and shell text. `SOURCE.json` pins its hash, the original log hash,
the delivered inspector hash, all 635 expected enterprise Python hashes, the 22
format proofs and the pending frontend files. No historical inventory or earlier
operator receipt is rewritten. The original inspector is archived for provenance;
it does not need to be run again for this reconciliation.

This is five-node source sampling, not individual source verification of all 70
Gateway instances. Unexpected additional files, installed dependencies, other
services and live in-memory Python configuration objects were not audited. The
probe made no database, model, OA callback or business requests. Work-grant sources
were absent in the samples; database head 0022 and PPT Runner remain prior records,
not fresh database/Runner observations. Browser navigation refers to the specific
tool registration switch, not a ban on every possible outbound network operation.

Run the following locally or in CI with Python 3.12+; both are offline:

```bash
python deploy/enterprise/test_production_observation_406_20261103.py
python deploy/enterprise/verify-production-observation-406-20261103.py --ref HEAD
```

The default verifier reads runtime sources from the sealed reference commit and
checks the archived evidence from the requested revision. It therefore remains a
historical check after intentional future changes; passing it does not establish
that a later HEAD is deployed. Add `--check-current` to also require the requested
revision's sources to match this checkpoint. Future releases need a new, additive
record and must not rewrite this observation to claim later code was already
deployed. Record future
releases as commit → image digest → configuration → operator acceptance, with
source integration and production activation tracked separately.
