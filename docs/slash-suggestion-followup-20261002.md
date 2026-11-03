# Slash suggestion filter follow-up

After shared-contract alignment, dmdb passed 41 backend Slash tests, frontend
lint/typechecking and both frontend parser/contract modules. One of the 31
composer-helper tests still failed: a Skill named bootstrap was suggested even
though both unchanged parsers reserve that name and cannot activate it.

The helper previously excluded only names from the caller's visible builtin
command list. That list is not the parser's complete reservation set. Reuse
RESERVED_SLASH_SKILL_NAMES directly from core/skills/slash in the Skill-only
filter, alongside the existing builtin-name collision check. Do not duplicate
the names or change either parser, the shared JSON, the visible builtin list,
enabled-state filter, prefix ranking, original ordering or six-item limit.

This is a bounded composer suggestion fix, not completion of the deferred Skill
policy/activation feature. A context Skill remains eligible because context is
not in the existing parser reservation set. Ordinary suffix names such as
bootstrap-helper remain eligible. Existing /compact and /context compact
submission, goal handling, thread abort and stale-request protections remain
byte-for-byte unchanged. Backend execution, authorization and runtime
configuration are not modified.

Keep all frontend test files, names and assertions unchanged, including the
failing test. Direct local verification runs all 31 captured helper callbacks
against the actual type-stripped helper code and placeholder implementation;
it reproduces the sole failure before the patch and checks all 31 after. It
also checks the exact import/filter-only delta and reservation/grammar equality.
These native checks are not a replacement for real-dependency Rstest, pnpm
check or browser release validation on dmdb.

The patch helper requires the pinned unfinished isolated merge and matching
working/index hashes, including the already applied contract follow-up. It
backs up changed files, rejects protected drift and checks reversible patch
application. No test is removed or skipped, no merge commit is created and no
deployment is changed. Run the three frontend modules again with pnpm check,
then continue the full backend suite before final merge/release acceptance.
