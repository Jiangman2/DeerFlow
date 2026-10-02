# Colon-joined host path masking follow-up

The real-dependency batch-nine focused run returned 407 passed and one failed.
Its failure is an output-masking defect, not a missing production configuration:
the shared path-tail regex consumed colons and all later host paths in a PATH
list. Each substitution masked only the first root of its greedy match. Extra
base variants could mask a second root but did not solve the arbitrary-length
list. Repeating substitutions until a test passed would hide that shared defect.

## Narrow shared-rule repair

Stop the optional tail at a colon so every following mapped host path receives
its own match. Change only the shared `_PATH_TAIL`; tools and LocalSandbox keep
using the same builder. Root escaping, sibling rejection, root end-of-output,
separator-sensitive LocalSandbox mode, separator-agnostic tools mode, caching,
longest-root order, symlink display behavior and all I/O/auth gates are unchanged.
Windows drive colons within the matched base remain part of the base. A literal
colon filename suffix remains in the output; masking is not path authorization.

Preserve every existing security test, including the failed eight-entry test.
Keep all original shared-helper tests and names. Their legacy literal snapshot
now permits exactly the reviewed tail replacement, while continuing to pin all
other bytes and both separator modes. Add eight-entry POSIX/Windows matching,
literal-colon suffix preservation and actual LocalSandbox reverse-output checks.
Do not restore the previously deferred scanner/normalize-mask-tail increment.

## Validation and limits

Local validation executes actual helper/tools/LocalSandbox masking callbacks
under narrow dependency fixtures, plus both old and new assertion callbacks.
It also checks unchanged I/O and authentication method bodies and the package
in a disposable Git merge/index fixture, including drift rejection and reversal.
This is not a full FastAPI/LangChain test run; rerun focused regressions on dmdb.

The package requires the applied batch-nine state and preserves its MCP/OAuth,
MindIE, OpenAPI and memory-filter repairs. No old tests are deleted or skipped,
no final merge commit or deployment is created, and no config/Secret is needed.
Full backend tests, frontend tests/build and release acceptance still remain.
