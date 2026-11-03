# Gateway run-context provenance reconciliation — 2026-10-01

The isolated merge remains pinned to snapshot
`33688ae5724bff20c5a5fddf060b77c97b9c1b01` and incoming baseline
`523f1f0fbc6145c454b5a1430d60775fe7215150`. The first backend reconciliation
applied successfully, but its focused regression stopped at 16 Gateway context
failures (332 passed). Because the command used `set -e`, the following full
backend test command did not run.

The snapshot and incoming source both lack the complete identity injection
contract already described and tested in the incoming baseline. This narrow
follow-up implements it without enabling the deferred Skill policy middleware:

- `request.state.auth_source` alone supplies runtime `is_internal`, including
  early-return paths where there is no user. Internal system role is not proof
  of internal request authentication.
- Remove request-supplied `is_internal`, `authz_attributes`, and `channel_user_id`
  from both configuration sections before writing server provenance.
- Only an internally authenticated request's original top-level `body.context`
  supplies a nonempty string platform sender. Free-form `body.config` cannot
  override that sender. Keep it runtime-only, outside checkpointed configurable.
- Retain authenticated user and server-resolved internal owner attribution,
  WeKnora injection, message sanitization, and run lifecycle/performance code.
- Fail visibly on non-dict runtime context instead of silently leaving it unchecked.

Existing tests are unchanged. New tests cover no-user scrubbing, config-only
forgery, authenticated sender precedence, role/source separation, stale sender
reuse, invalid sender values, and malformed context. The existing `start_run`
integration tests must verify the real call passes the original request context.
Pure extracted-function checks do not replace that integration coverage.

Apply after the first backend reconciliation. This supplement changes only
`services.py`, adds its regression module and this record, and does not commit,
deploy, alter credentials, or enable authorization policy. The original frontend
reconciliation checksum guard expects the earlier services bytes: use its v2
package after applying this supplement if frontend reconciliation is still pending.
Keep full backend and frontend gates mandatory before final merge or deployment.
