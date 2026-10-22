# Batch eight: read ranges, local path display, and supported runtime contracts

This follow-up is for the uncommitted dmdb merge worktree at HEAD
`33688ae5724bff20c5a5fddf060b77c97b9c1b01`, merging
`523f1f0fbc6145c454b5a1430d60775fe7215150`. It does not commit, deploy,
touch the live checkout, copy production configuration, or change credentials.

## Runtime fixes

- Read-file loop keys retain the exact normalized line interval. Different pages
  in the former 200-line bucket no longer trigger a repeated-call hard stop.
  An omitted or invalid end line has an explicit EOF identity. Repeating the
  same range still hits the existing hard stop. Thresholds, frequency caps,
  per-run isolation, and terminal stop-reason ownership are unchanged.
- Output reverse mapping still prefers the mount containing the physical target.
  If a symlink escapes all mounts, its lexical mount spelling is used solely to
  mask display output. Actual reads/writes/downloads still resolve the target
  and reject escaping paths. This is not permission to read outside a mount.
- Listing a missing directory raises FileNotFoundError using the requested path,
  rather than masquerading as a successful empty listing. Empty real directories
  and synthetic mount parents containing real child mounts remain supported.

## Tests aligned with the retained implementation

The enterprise identity test expects the current `海能通用智能体` name;
its enterprise-description and no-artifact-branding assertions are retained.
Its memory configuration is explicitly disabled instead of relying on a missing
fixture field. No prompt template or branding implementation is changed.

The shared subagent floor remains two, not one. The incoming single-subagent
increment lacks its runtime constant change and is deferred. All preexisting
clamp tests are restored, and an additional prompt/enforcement consistency test
explicitly pins the retained floor. Defaults, budgets, and concurrency guards
are not altered to make unsupported feature tests pass.

The incoming Windows bounded-runner, configurable capture encoding/newline,
PowerShell UTF-8 prelude, and selective MSYS path-conversion increments are
also deferred. They refer to absent `_run_windows_command`, locale support,
capture constructor options, or `_msys_path_conversion_exclusions`.
The original Windows PowerShell/CMD/POSIX command-mode, secret-environment,
non-MSYS, UTF-8 file-I/O, and TimeoutExpired tests are restored in full.
The new, supported POSIX newline-preservation test is retained. No existing
test module or preexisting test function is removed or marked skipped.
Windows execution remains the original subprocess.run implementation; Windows
process-tree termination, bounded capture, and locale round-trip improvements
are not advertised as repaired by this package.

## Verification and release gates

Local checks exercise actual extracted runtime methods with narrow dependency
fakes, AST preservation, exact snapshot hashes, patch forward/reverse checks,
guard-drift rejection, and a disposable merge/index fixture. They are not a
substitute for dmdb's real dependency-backed pytest suite.

Run the local-sandbox, loop, prompt, and subagent focused tests on dmdb first,
then continue full backend tests with visible progress. Frontend pnpm check
previously passed and must be rechecked before final acceptance. Earlier
documented deferrals (cancellation/lock ownership, malformed call-ID recovery,
channel restarts, and skill policy) remain deferrals. A focused pass does not
establish full-suite, runtime-config, image-build, or deployment readiness.
