# Backend merge reconciliation: first full-suite batch

This follow-up belongs only to the unfinished isolated merge on
`fix/upload_test_env-enterprise-merge-20260930`, snapshot `33688ae5`, incoming
`523f1f0f`. It does not commit the merge or change any deployment.

## Implemented corrections

- Custom Skill history keeps its existing administrator authorization before
  any storage operation. Storage construction, existence checks, and history
  reads run through `asyncio.to_thread`, preserving owner ContextVars while
  removing filesystem I/O from the event-loop thread. Existing 404/500 behavior
  and response shape remain unchanged.
- Channel binding accepts an explicit `/connect CODE` after complete leading
  platform mentions. Bare `connect` chat is not a binding command. The shared
  control-command classifier remains unchanged, so a non-bot mention cannot
  expand its command authority. Binding-code validation and consumption still
  belong to the existing repository.
- Three connection-reply assertions now match the deployed Chinese product
  names exactly. Identity, binding, and single-reply assertions are retained;
  runtime branding is not replaced with upstream English strings.

## Individually deferred incomplete incoming increments

- ACP `timeout_seconds`: neither the configuration field nor matching process
  deadline handling exists in this reviewed baseline. Restore the complete
  pre-merge ACP configuration and invocation tests, including all original
  assertions. Do not add a field without enforcing its process deadline.
- The absent `/api/skills/reload` endpoint: defer its three new HTTP tests and
  the new endpoint-only blocking-I/O test module, which did not exist before
  the merge. Preserve all three incoming cache invalidation/concurrency/timeout
  tests unchanged, and leave their implemented prompt-cache runtime untouched.
- AIO retained-session rotation, transient Bash sessions, structured directory
  status markers, and native append expectations: these incoming test increments
  have no matching implementation in the unchanged AIO runtime. Restore every
  pre-merge AIO test definition, preserving concurrency, environment isolation,
  path, cleanup, and download coverage. Retain the independently supported new
  regression that an unrelated shell 404 must not create a replacement session.
  These mocked API failures are not evidence that the live Sandbox image needs
  an upgrade.

## Validation boundaries

The 20-failure diagnostic run stopped normally after 36.05 seconds; it did not
identify the earlier wait near 8%. Local verification checks parser behavior,
offloaded history authorization/context/error boundaries, preservation of old
test definitions, and patch/guard/backup mechanics. It is not a replacement for
the ARM64 server's dependency-complete pytest run. Continue focused tests and
then full-suite diagnostics with verbose current-test names and bounded
faulthandler traces. Do not finalize or deploy this merge until required
verification succeeds.
