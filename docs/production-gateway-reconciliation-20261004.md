# 2026-10-04 production Gateway source reconciliation

This change reconciles two production-accepted repairs onto the
`a963fc0a5acb69632a1a57e9099b253af5370372` baseline. It does not deploy services.

## Stop and reconnect routing

The optional `k8s/local-pvc/gateway-run-placement-nginx.inc` now contains both
new-run placement and existing-run owner resolution. Merge all four sibling
locations into the live server before its plain `/api/langgraph/` prefix.
Do not replace the full live configuration or blindly include the file into a
different server. A `^~` API prefix suppresses the regex routes and must be
reviewed before deployment. Keep other routes and rendering commands unchanged.

`/resolve` is a metadata-only, read-only internal lookup. It receives neither
credentials nor request bodies. Active-owner disappearance fails closed, and
mutating requests are not retried on another Gateway. The actual request retains
its method, query, body and authentication/CSRF headers at Gateway. Redis-only
read joins remain cross-process; stream requests containing an action go to the
owner, including duplicate and percent-encoded lowercase action keys.
This does not claim native `/api/` or IM cancellation routing acceptance.

## Interrupted history

Only a client-requested interrupt that finishes as `interrupted` saves public
lead-agent partial text through the existing journal path. IDs, deduplication,
artifact validation and final status remain unchanged. Hidden reasoning, internal
and subagent messages, unfinished tool calls, rollback, model failure, shutdown
and lease takeover are excluded. There are no per-token SQL writes, guessed
usage, checkpoint-context changes, automatic continuation or schema migrations.
Already-lost old text is not reconstructed.

## Verification and rebuild boundary

Two real browser stop/refresh trials were accepted on different Gateway Pods:
one partial history row each, with 924 and 600 saved characters. Two normal
completions were accepted, and the final active-run count was zero. Original
ARM64 release builds passed 45 history and 39 owner-resolver tests.
Focused repository verification passed 94 tests after scoped Ruff import and
format fixes; scoped lint and format checks also passed. The Nginx fragment
tests are static contract checks, not fresh HTTP or load-test acceptance.

### Full-suite comparison recorded on 2026-10-05

- Baseline: 9450 passed, 30 failed, 69 skipped, 27 subtests passed.
- Candidate: 9543 passed, 31 failed, 69 skipped, 27 subtests passed.
- Both runs reported 17 warnings and wrapper exit code 124 (timeout), even
  though pytest summaries were printed. Neither full-suite run passed.
- All 30 baseline failures also occurred in the candidate. Their causes and
  the wrapper timeouts remain unresolved; they are not classified as harmless.

The one candidate-only failure was
`test_client.py::TestStream::test_chat_accumulates_in_linear_time`: the timed
10000-chunk operation took 1.311 seconds against the unchanged 1.0-second
limit. Client source and test files were byte-identical across both snapshots.
Three isolated baseline and three isolated candidate reruns all passed with
the original threshold. The failure did not reproduce in those reruns, but
its root cause remains undetermined; this does not make the full suite green.
Publish this repair branch with those limitations visible. No mass-scale
capacity or production rebuild acceptance is claimed by these tests.

Run from `backend`, after installing the locked dev dependencies:

```bash
PYTHONPATH=. uv run --no-sync pytest -q tests/test_runtime_interrupted_history.py tests/test_run_placement_owner_routing.py tests/test_run_owner_nginx_fragment.py tests/test_run_placement.py
make test
```

Multi-Gateway operation still requires the existing PostgreSQL backend,
shared Redis stream bridge and enabled Run lease heartbeat. Same-node replicas
are not node-level HA. Source synchronization neither changes those runtime
settings nor rebuilds/preloads images. The enterprise browser-capable build
profile must be reconciled separately before claiming a full rebuild release.
No Secret, token, proxy credential, database dump, CA bundle, runtime backup,
OCI archive or complete live ConfigMap belongs in this Git change.
