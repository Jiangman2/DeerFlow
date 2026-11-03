# PR #26 CI qualification corrections

This is an additive qualification of commit
`5ec9fa2059f846d6b13646f64aa457d8eec10230`. The November 2 source inventory,
published sources, historical baselines and operator receipts remain immutable.
No production deployment or business-request acceptance is claimed here.

- Resolve the release verifier's sibling module from its own path so backend
  tests can import the verifier from any working directory.
- Keep the accepted expanded settings dialog and compact Token display; update
  the old layout and account assertions, including the exact Token tooltip.
- Verify the concise attachment tooltip separately from upload-limit responses.
  Oversized uploads must still be rejected; valid files retain their order.
- Test one-row material rails with vertical overflow on desktop and mobile,
  retained input space, edge insets and deletion after scrolling.
- Format the two runtime frontend files and three E2E files with the pinned
  Prettier configuration. `frontend/scripts/verify-pr26-formatting.mjs` proves
  the two runtime changes equal formatter output from the preserved baseline.

`SOURCE.json` binds each corrected file to its original bytes in `baseline/`.
The release verifier checks that seal before reconstructing historical source
views. These test corrections do not relax the runtime source, database-head,
Work DataLake exclusion, or PPT Runner provenance requirements.
