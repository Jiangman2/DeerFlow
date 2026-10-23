# Data QA progress and token-summary frontend release

This candidate includes the already qualified Data QA progress frontend plus
the five runtime files from token-display commit
`1c74cfe3a37a9d85cf38bdde4e002c5f722adf26`. It does not incorporate the rest of
the pending worktree. The full build context preserves all other prior source
bytes. Missing/all-zero in-flight usage is pending; received nonzero usage
updates immediately; absent terminal usage is unavailable. No estimates,
provider configuration, accounting or database changes are included.

- Image: `sha256:b6c17e31ebcadb3c514fd2c3f87752e0eb84ee3e28ccc921366bac6a0bf91cd5`
- Config: `sha256:f3177f221a6bdd05daef6a2997175ae982df3739e5f3dd6def0cb63089c84527`
- Archive: 127319040 bytes, SHA256 `6379f2658531913bbe9d8e7067336125eb210f2c8b2d3835bbeba6139d4d62d7`.
- Operator files: `C:/Users/15285/Downloads/portal-data-qa-token-20261022-r1`.

Qualification passed 25 token tests, 6 Data QA tests, targeted ESLint, production
TypeScript compilation, clean ARM64 base/layer verification, isolated runtime
HTTP/SSO-fixture/static-asset checks and full OCI blob/diff-ID auditing. Compiled
assets contain both token-status messages and the Data QA progress implementation;
their exact hashes are in `data-qa-token-frontend-assets-20261022-r1.json`.

The generated preloads passed 9 Windows tests and 9 isolated Linux Python 3.7.17
tests. These include the real OCI parser and resource budget, original function
preservation, wrong-host rejection, capacity rejection before the once claim,
old marker refusal, and preservation of both the current production image and
the previously preloaded Data QA image. No containerd/Kubernetes actions occur
in those tests.

The old 15d132-only activation packager remains superseded. The combined
activation packager requires new matching preload summaries, compiled asset
pins, and a successful Gateway feature receipt.

On 2026-10-23 the operator supplied all three successful receipts. Gateway 384
accepted 70 Pods; the two frontend nodes accepted the combined `b6c17e31...`
image. These are archived in `oa-feature-activation-field-20261023-r1.json` and
`data-qa-token-frontend-preloads-field-20261023-r1.json`; they are pasted
observations, not a new direct cluster read by the local agent.

The exact receipt-bound activator passed 22 offline tests against simulated
cluster responses and nine isolated Linux Python 3.7 package-loading/receipt
checks. Its package pin and procedure are recorded in
`data-qa-token-frontend-activation-package-20261023-r1.json` and
`data-qa-token-frontend-activation-20261023-r1.md`.

The operator subsequently returned successful frontend activation on
2026-10-23. Both Pods accepted the combined image and its two HTTP-served
JavaScript assets; frontend generation is 226, while Gateway 384 and Runner 55
remain preserved. The field summary is archived in
`data-qa-token-frontend-activation-field-20261023-r1.json`. Browser acceptance
and OA v2 business enablement remain pending. This frontend-only change does
not enable OA v2.
