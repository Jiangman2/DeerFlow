# Frontend reconciliation — October 2, 2026

Scope: the unfinished dmdb merge only, HEAD
`33688ae5724bff20c5a5fddf060b77c97b9c1b01`, MERGE_HEAD
`523f1f0fbc6145c454b5a1430d60775fe7215150`. No final commit, push,
deployment mutation, production configuration, env file, or Secret is included.
The server's latest backend suite reported 9482 passed and 67 skipped; skipped
live-model, database, Redis, browser, platform-specific and fixture-dependent
checks are not verified by that result.

## Repairs

- Tool-result anchors do not inherit Markdown's sanitizer automatically. A
  shared display boundary checks argument records/string fields and only admits
  absolute HTTP(S) URLs. Local/OS-handler schemes, malformed values, controls,
  backslashes and embedded credentials are inert labels. Blocked links retain
  the unsafe marker and search results/thumbnails where safe. Safe navigation
  uses the existing thread-aware preview link renderer; no loopback bypass.
- Search results tolerate malformed rows, image thumbnails are HTTP(S)-only,
  and all specialized tool branches tolerate absent/null/invalid argument
  containers and non-string display arguments. Tool-result indexing still uses
  the first non-empty result; task calls remain omitted from the generic panel.
- Clarification context appears once outside the processing panel. Other
  tool-bearing assistant progress stays visible in the panel. Thread preview
  rewriting, token-debug summaries and Markdown reveal behavior are retained.
- Ordinary/custom-agent chat layouts share the same provider stack, retaining
  the legacy import as a re-export. The server layout and static demo route
  generation are unchanged. This follows the shipped Next.js layouts/pages and
  server/client component guides captured from the dmdb Next package.
- Upload deletion encodes only the filename path component. The authenticated
  fetcher and Gateway file enforcement remain unchanged.
- GFM sets `singleTilde: false` for normal and animated Markdown. Double-tilde
  strikethrough, math, raw-HTML-free reasoning and preprocessing remain intact.
- The email employee is the sixth fixed employee, before the unchanged twelve
  contest entries. No employee, address or category is removed or rewritten.

## Deployment-fixture correction (explicit test assertion change)

`contest-agents.test.ts` already existed before the merge but expected the
production `cnoocclaw.cnooc.com.cn` host while captured pre-merge fixed cards
already used `cnoocclawtest.cnooc.com.cn`. Its strict host assertion is aligned
to the test host; uniqueness, original-employee retention and exact contest
array assertions remain. The new email assertion in `external-launch.test.ts`
receives the same correction plus an explicit sixth-position assertion.
Production direct-link tests remain, with additional test-host direct-link
coverage. This is a fixture correction, not a restored implementation claim
and not authorization to change deployment hosts.

## Narrow deferral

Captured incoming and pre-merge `core/api/api-client.ts` are byte-identical.
The incoming test module added three expectations with no matching runtime:

1. `hydrates active run input before replaying incremental events`
2. `continues reconnect when input hydration state is unavailable`
3. `hydrates ppt-master reconnect input from the isolated runtime`

The module is restored byte-for-byte to its pre-merge version (all 19 original
tests and assertions). No original module/case is removed or skipped. The three
incoming additions are explicitly deferred, not fixed, until checkpoint/input
merging and stream ordering are implemented and reviewed together. Original
terminal-run preflight, cancellation conflict handling, CSRF and isolated PPT
state/history/stream routing remain unchanged. This implements the previously
approved policy of separately deferring incoming tests lacking companion code.

## Acceptance

Locally validate patch forward/reverse checks, exact working/index guards,
backup recovery bytes, safe URL/argument helpers and targeted render regressions.
Native callback/SSR checks with stubbed UI dependencies are not full Rstest,
ESLint, TypeScript-project or Next-build acceptance. On dmdb rerun:

```sh
pnpm check
pnpm exec rstest run
pnpm build
```

Do not mark the final merge or deployment accepted until these succeed and
remaining skipped/integration checks are considered separately.
