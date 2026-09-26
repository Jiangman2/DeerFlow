# Candidate verification — 2026-09-24

Scope: one Gateway adapter source file. Existing dirty Digital Employee work is excluded.

- Original adapter: SHA256 `be67018d948af473d32db24611b875fa3cb641855b472a94237c140e309892d7`, identical to the archived R16 payload.
- Before change: original adapter suite 12 passed. New regression suite reproduced 15 failures, including missing required fields being published.
- Focused suite: adapter, tool validation/recovery, Codex provider, Agent Host, release/audit tests: **85 passed**, one existing Starlette deprecation warning (55.64s). Includes schema-data compatibility regression.
- Ruff and diff whitespace checks passed at the completed checkpoint.
- Whole-backend attempts before/after change both stop at the existing Digital Employee delete test (`test_delete_agent_does_not_block_event_loop`, missing expected draft revision -> HTTP 428). Not a whole-repository PASS.
- Offline smoke uses mocked HTTP, actual adapter conversion/streaming code, and standard unittest. It does NOT call an enterprise model, execute a shell command, or generate a presentation.
- Host collector syntax checked as Python 3.7. Runtime adapter requires Python 3.11+; known Gateway baseline is 3.12, to be confirmed again by the read-only audit.
- No local Linux/ARM64 validation: Docker Desktop Linux engine unavailable during this turn. No production access or mutations performed.

Not yet release-authorized: live Gateway source/dependency/mount audit; exact-base ARM64 offline build and candidate smoke; isolated real-model function/custom-tool roundtrips; current-task maintenance gate; rollback image verification; ordinary chat/PDF/channel regression and user PPT acceptance.

No guarantee that a model always returns valid arguments: invalid calls are contained and correction is bounded. Repeated failure remains visible instead of executing guessed commands.
