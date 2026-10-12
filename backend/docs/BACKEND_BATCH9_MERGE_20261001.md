# R10 reconciliation: MCP, model result framing, API schema and preserved memory scope

The r10 backend run stopped at its 20-failure limit: 5192 passed, 26 skipped,
27 subtests passed, 20 failed. It was not a completed full-suite run.

## Runtime repairs

- Malformed MCP URI references are ignored rather than aborting result conversion.
  File URIs accept only local authorities (including Windows drive forms on
  Windows); remote file hosts remain untouched. Existing thread/user-data
  containment and file-existence checks stay in place. Regex filename replacement
  uses a callback so backslashes are literal path data, never replacement escapes.
- MCP OAuth uses one non-blocking thread lock per server. Awaiting callers poll
  without blocking their event loop or spawning abandoned acquisition threads.
  Cache double-checking, expiry, refresh-token rotation and credential transport
  are preserved. Cancellation during wait owns no lock; cancellation or failure
  during fetch releases the lock in finally. This covers multiple caller loops.
- MindIE escapes tool-result text inside its existing XML response boundary. This
  prevents a tool payload from closing the boundary and introducing new tags;
  it does not guarantee that an LLM ignores every natural-language instruction.
- Artifact/live-preview HTTP methods are registered separately, all visible in
  OpenAPI with distinct generated operation IDs. The exact methods, paths,
  authenticated handlers, owner read/write permissions and proxy transport stay
  unchanged. No route is hidden merely to avoid duplicate-ID warnings.
- MemoryRunStore's optional user_id filter is restored exactly from the pre-merge
  method. Its thread index, status selection and token/model/caller accounting
  stay unchanged. All existing scope tests are retained. SQL/Base aggregation
  contracts are unchanged; this is not a new API or new production filtering claim.

## Fixture correction, not runtime rollback

The Postgres advisory-lock fake lacked the existing connection.commit method.
It now records commit and asserts lock -> commit -> migration body, including
the error path. Existing SQL-order and unlock assertions remain. The real
bootstrap code, commit and advisory locking are unchanged. SET LOCAL protects
the acquisition transaction; commit prevents idle-in-transaction during DDL.

## Explicitly deferred incoming-only increment

The exact read-file truncation-continuation module does not exist in the
pre-merge snapshot. All eight of its tests require a new marker/line-count
contract absent from the source implementation. Defer that incoming-only
module, rather than inventing partial support, marking tests skipped, or
deleting preexisting range/truncation coverage. The actual read tool is unchanged:
it still performs a full provider read before range slicing, caps visible
output, and emits its existing generic range hint. It does NOT promise an
exact gap-free continuation line, a whole-line recovery hint or oversized-line
fallback. A complete implementation and those eight tests belong together in
a later change. Every preexisting test module and test definition is preserved.

## Verification and release gates

Local verification covers AST-extracted actual functions with narrow dependency
fixtures, multiple real caller threads/loops, both cancellation boundaries,
literal replacements, URI authorities, escaped tool payloads, route registrations,
the unchanged pre-merge memory filter and real lock-order callback. It does not
replace dmdb's real FastAPI/LangChain/SQLAlchemy tests. The package guards every
previous repair plus relevant tests, runtime sandbox/auth/performance files,
and unchanged bootstrap/base/SQL contracts in both index and working tree.

Run focused regressions and then the complete backend suite again. New failures
must be reviewed, not globally skipped. Frontend check has passed but frontend
tests/build and release acceptance remain separate gates. No merge commit,
deployment, runtime Secret, database migration or formal-server config changes
are authorized by this package.
