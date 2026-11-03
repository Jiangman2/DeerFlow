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
replaces seven runtime files, and adds two runtime files. The other 972 inputs,
including the production CSS bytes, remain unchanged. Candidate source hashes
and immutable enterprise-parent snapshots are recorded separately in
`release/dashboard-navigation-20261103/SOURCE.json`. Historical inventories and
operator receipts are not rewritten. The additive verifier validates the current
candidate before exposing parent bytes to historical source checks.

This document and source qualification are not a production deployment receipt.
Production preload, rollout and browser acceptance require their own observations.

Local validation: `pnpm check`; 29 dashboard/heatmap unit checks; six responsive
dashboard viewports, range retention, keyboard navigation, failure pagination and
recovery, account/monitoring states, and chat-history labels. Initial cold dev
compilation exceeded assertions in two existing account checks; those checks
passed after compilation. Chat document titles now use React's title element to
avoid an imperative title update racing streamed page metadata. Source validation
includes six candidate checks, twelve enterprise-followup checks, fifteen November
2 checks, and six package integrity/admission checks. Build and production results
are recorded separately rather than implied by these local tests.
