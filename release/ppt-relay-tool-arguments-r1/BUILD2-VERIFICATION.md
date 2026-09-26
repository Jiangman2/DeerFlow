
## Build2 — layer-graph correction

User metadata-only diagnostic confirms 345 layers, 345 valid Diff IDs, matching
runtime identity, Linux ARM64 and validated metadata hashes. It did NOT verify
all layer content. Build1 rejected this at its custom 128-layer work limit.

Build2 changes only the host-side layer graph check, adds precise non-secret
diagnostics, and preserves the original complete blob verification. A new
1024-layer bound remains; the separate 128-entry index fanout is unchanged.
Synthetic regressions cover 128/129/345/347/1024 layers and failures including
missing/corrupt final layers, mismatched counts, malformed Diff IDs, incorrect
identity/architecture, external references and resource-bound overflows.
These tests do not replace a real ARM64 build or snapshotter capability check.

Local verification on 2026-09-24: 43 tests passed in 176.26 seconds (23 layer
graph/package regressions and 20 existing build safety regressions, both against
the assembled Build2 payload). Ruff check and formatting checks passed for the
new helper, packager and affected tests. The package scope test verifies all
manifest entries, Python 3.7 syntax compatibility of shipped host scripts and
byte-for-byte preservation of the application payload and build controls.

The frozen Gateway payload and all existing build controls are unchanged from
Build1. Neither the actual Build2 candidate build nor live-model acceptance has
run on production hardware yet. No production rollout is authorized by this package.
