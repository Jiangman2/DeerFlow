# September 30 test merge: deferred activation-scoped skill tool policy

## Decision and evidence

With the operator's approval, this merge defers only the incompletely integrated
activation-scoped lead-agent tool policy from source commit
`523f1f0fbc6145c454b5a1430d60775fe7215150`. It does not bypass an existing
production authorization boundary or disable test collection globally.

Read-only checks of the production Gateway image
`20260928-ppt-preview-profile-r4-gateway-9473ecee03df4e43` showed:

- `runtime/secret_context.py`, `skill_activation_middleware.py`, and
  `lead_agent/agent.py` have exactly the same hashes as this source commit.
- The production image does not contain the new permission middleware or call
  its constructor from the lead-agent execution chain.
- The source adds that middleware and its tests, but lacks the matching context
  readers/writers, policy-decision key, and activation shared-token plumbing.

Consequently the incoming tests cannot even be collected; adding a stub helper
or skipping the test runner would not establish the promised enforcement.

## Scope retained and deferred

Retain existing production Skill activation, secret binding, workflow gates,
authorization and construction-time tool filtering. Keep all pre-merge lead-agent
regression tests, both data-lake filtering cases, sandbox execution-lease coverage,
test-environment stream/admission/dispatch optimizations, and Haineng frontend work.

Defer the new middleware file and its two dedicated test modules together with the
two new lead-agent integration/order tests and two new secret-context helper tests
that require the absent implementation. Restore existing loader names, constructor
signatures, static filtering expectations, and secret-context fixtures throughout
the pre-existing test modules; the production implementation has not changed.
Retain the independent new Skill self-evolution prompt assertions.
Keep the deferred implementation recoverable in the source commit, and preserve
a checksummed copy of affected files before applying the portable patch.

This decision does not establish that the complete merge is deployment-ready.
Run focused regressions, the complete backend suite, and frontend checks against
the isolated merge worktree before committing or deploying it.

## Conditions for a later integration

Reintroduce the feature only as a complete, separately reviewed change containing
the context contract, activation-to-policy ownership token, execution-chain
ordering, secret/context redaction, and schema plus tool-execution enforcement.
Restore its dedicated tests and both compiled-chain regression tests together;
cover forged context, invalid/stale references, passive skills, deferred-tool
discovery, synchronous/asynchronous execution, and production-compatible activation.
Never substitute stub helpers, broad test skips, or an unwired middleware for
end-to-end verification.
