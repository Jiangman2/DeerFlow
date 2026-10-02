# Bounded search observation follow-up

The expanded path-mask regression completed with 245 passed and 5 failed.
The path-mask tests passed. All failures came from additional search coverage:
two incomplete-empty observations and three AIO directory-glob cap boundaries.

## Repairs and unchanged boundaries

Empty glob/grep observations now distinguish a completed no-match search from
one whose provider reported truncation. The latter explicitly says the result
is incomplete, describes only the scanned part, and suggests narrowing the
search. Complete empty and all nonempty result formatting remain unchanged.

For AIO `glob(include_dirs=True)`, preserve the existing root-boundary,
ignored-path and glob-pattern filters. Check the result cap only after an entry
passes all three filters, but before appending it. A further eligible match
proves an omitted match; reaching exactly the cap does not. Continue filtering
the remaining returned entries until such a match is found or the list ends.
A zero cap returns no paths and is truncated only if an eligible match exists.
Do not count ignored, out-of-root or pattern-mismatched tail entries.

This does not add remote calls, new scan roots or a different SDK route:
the existing list_path request is unchanged and its already-returned entries
are inspected locally. The AIO native find_files branch and grep implementation
are unchanged; this is not a claim that every provider's scan is exhaustive.
Tool result limits/defaults, user/disabled-skill filters, local read validation,
output masking, command environment/secrets, leases and thread ownership remain
unchanged. Every existing test module, assertion and test name is retained.

## Validation

The local fixture uses actual formatting and tool callbacks, the actual AIO
glob method with a fake client response, and the real shared ignore/pattern
helpers. It reproduces the five failures before repair and exercises exact-cap,
zero-cap, omitted-match and filtered-tail observations after repair. AST checks
limit runtime changes to the two formatters and the AIO glob method. The package
guards earlier repairs and unchanged tests in both index and working tree and
is checked in a disposable merge fixture with drift rejection and reversal.

Run real-dependency search/path/security regressions on dmdb, then continue the
complete backend suite. These fixtures do not establish whole-suite or release
readiness. No tests are skipped/deferred by this package, no configuration or
Secret is required, and no final merge commit or deployment is created.
