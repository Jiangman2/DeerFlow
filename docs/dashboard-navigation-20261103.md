# Dashboard navigation and chat history candidate

This frontend-only candidate starts from enterprise commit
`443cbd8ab6e2e74298075976fa68af079c1f7100`. It includes the previously pending
"最新 1000 条范围内筛选" copy. It does not update Gateway, database schemas,
model settings, OA configuration, Work DataLake, or PPT Runner.

The sidebar, chat page heading/breadcrumb, and browser title consistently say
"历史对话" ("Chat history" in English).

The dashboard has six sections: Overview, Run diagnostics, Users & employees,
Knowledge, Models, and OA API. Desktop uses one navigation row; narrow screens
use a two-row grid, with no horizontal navigation scrolling. The navigation stays
available while scrolling. Selecting a section returns its scroll area to the top.
The URL fragment supports links, reloads and browser Back/Forward; arrow keys
move focus and Enter/Space select a section.

Overview, Run diagnostics, Users & employees and Knowledge share the selected
preset/custom time range. Switching sections preserves that range, draft dates,
filters, pagination and expanded rows. Model and OA monitoring each retain their
own independent interval. Reloading the page retains the section through the URL,
but starts a fresh range/filter state. Custom date fields are expandable; preset
ranges and the applied range remain visible. The existing validation, progress,
bounded reads, manual refresh serialization and failure recovery are retained.

Inactive sections keep component state but disable their data queries. They do
not continue polling. Overview does not request all lower dashboard modules.
No backend API or database query is added by this change.

The local ARM64 build starts from the exact 979-file Frontend 239 build snapshot,
replaces seven runtime files, and adds three runtime files. The other 972 inputs,
including the production CSS bytes, remain unchanged. Candidate source hashes
and immutable enterprise-parent snapshots are recorded separately in
`release/dashboard-navigation-20261103-r2/SOURCE.json`. The earlier local r1
candidate and its source seal are retained. Historical inventories and
operator receipts are not rewritten. The additive verifier validates the current
candidate before exposing parent bytes to historical source checks.

This document and source qualification are not a production deployment receipt.
Production preload, rollout and browser acceptance have separate observations.

Local validation: `pnpm check`; 29 dashboard/heatmap unit checks; six responsive
dashboard viewports, range retention, keyboard navigation, failure pagination and
recovery, account/monitoring states, and chat-history labels. Initial cold dev
compilation exceeded assertions in two existing account checks; those checks
passed after compilation. The r1 production-image browser run passed 19 checks
but exposed the default root title replacing the chat-history title. The r2
candidate uses route-owned server metadata and keeps the interactive history
list in a client component. The r2 ARM64 image passed all 26 browser checks
(22 main scenarios plus four existing ranking/attribution regressions), including
the real production-build title check. Windows and Linux package checks each
passed six cases; both generated scripts passed Python 3.7 wrong-host admission
checks before any import or execution marker. The image, OCI export, compiled
asset hashes, package pins and local test summaries are recorded under
`release/dashboard-navigation-20261103-r2/qualification/`. These are local
qualification receipts; production evidence is recorded separately below.
Source validation
includes six candidate checks, twelve enterprise-followup checks, fifteen November
2 checks, and six package integrity/admission checks. Build and production results
are recorded separately rather than implied by these local tests. Nine formatter
qualification checks cover the additive navigation seal, both parent snapshots,
unregistered drift, and historical checkouts.

## November 4 operator preload and activation handoff

The operator supplied successful r2 preload receipts from `ecs-zdz-pro-0015`
and `datax-02`, recorded in `release/dashboard-navigation-20261103-r2/PRELOAD-FIELD.json`.
Both report completed import/unpack and CRI visibility for manifest
`sha256:74c2e66ecc1c2e9b483faac428fc9211ff43bc0e0ed59ee141de379c678fdfb5`,
with old references preserved and no production workload mutation. These are
operator-supplied summaries, not a remote reread of their private reports.

The separate `activation-20261104-r1/` handoff contains the exact generated
Frontend 239 -> 240 tool and its local qualification. The generator pins the
previous transport template, current image/compiled assets, 26 browser checks,
and both preload receipts. At execution it requires the freshly observed
Gateway 406 and Frontend 239 identities. It runs two temporary non-root
`imagePullPolicy: Never` checks, then makes at most one frontend image change
using UID, resource version and complete-spec comparisons. It verifies both
new frontend Pods, local static HTTP assets and service endpoints. Gateway,
other workload specs and service specs must remain unchanged. It does not
access the database or call model/OA providers. A changed baseline is refused;
there is no automatic retry or rollback after a dispatched patch.

Activation transport checks: Windows 18 passed; Linux 17 passed with one
Node-dependent fixture skipped (that fixture passed on Windows). The final
Python 3.7 entrypoint rejected the wrong host before mutation. The exact
candidate ARM64 image executed the final ten-file probe as UID 1000 with a
read-only filesystem and no network. Production activation and browser
acceptance are recorded separately from this local qualification.

## Frontend 240 runtime acceptance

The operator returned a successful activation on November 4, operation
`c0a556cc43e144438f186c436027066c`, in 47.553 seconds. Selected fields are
recorded in `release/dashboard-navigation-20261103-r2/PRODUCTION-FIELD.json`;
the original private report is 33,881 bytes with SHA256
`03da2dd4e70c4adc79d7119cee1137dfb068cd9524307029b8bf2caef6104087`.
Both temporary image checks passed, the single frontend patch was read back,
and both new frontend Pods passed source, seven static-asset HTTP checks and
stable service endpoint checks. Frontend is generation 240 with spec SHA256
`edfb8674c6e413124a5ed27f381f689ee9498853bb1f4be38ee8f90c15dab578`.
Gateway remained generation 406 with its pre-existing spec hash. The other
observed workload and service specs were preserved. No database access or
model/OA request was made by the activation tool. This runtime acceptance does
not establish new business-call acceptance. Desktop/mobile browser acceptance
remains pending. Do not rerun the completed preload or activation commands.
