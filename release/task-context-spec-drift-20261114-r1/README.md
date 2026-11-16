# Gateway 417 configuration difference observed by operator

The operator pasted the readonly diagnostic result at 2026-11-14 10:59:51
Asia/Shanghai. `OPERATOR-REPORT.json` is a JSON transcription of that output,
not a byte-for-byte copy of an on-server receipt. Its local file hash is used
only to bind the independently observed defaulting evidence.

The sole observed spec difference from the submitted r1 candidate is the new
`task-conversation-context` ConfigMap volume's `defaultMode: 420` at
`/template/spec/volumes/9/configMap/defaultMode`. Candidate source, immutable
ConfigMap and readonly source mount matched, and the deployment spec remained
stable across two reads. Gateway generation was 417, updated replicas 6/70,
available replicas 70. This observation is not rollout or business acceptance.

Do not replay the r1 activation. Its original PLAN, REPORT and journals remain
immutable. A separate readonly postcheck may accept exactly this observed API
default in memory and must retain all source, budget, replica and endpoint
checks before saving a new acceptance receipt.
