# Channel/PDF R2 verification boundary

R2 is rebased on the user-provided live source audit of 2026-09-23, not the dirty
checkout or its HEAD. The reviewed functional delta and icon are frozen from R1
archive SHA256 `8b0025010f5bc1a5b946fd43e5be6077ae9ef354ec2205baf25a8ba630c6e16b`.

The three known mismatches are resolved without importing unrelated updates:
the production manager has no AsyncKeyedLockTable import, while both production
locales already have the current enterprise product name. Preserve those facts.

Source replay checks both complete production source sets (11 Python files and
8 TypeScript/TSX files), exact results, read-only checks and idempotence. Every
target entry carries before/after SHA256 locks; no-op entries retain previously
deployed branding. Unknown source drift is an error, not a fuzzy merge.

The server first validates both source trees inside their real images under
`--network=none`, then runs native PDFium positive/negative smoke tests and the
frontend production build. The complete baseline OCI config and manifest are
checked against the runtime image ID and linux/arm64 platform before BuildKit.

R1's PDF security regressions and UI tests are supporting local evidence, not
proof of this production image's dependencies. ARM64 builds, candidate startup,
network routes, image preloading, rollback readiness and browser acceptance
remain server-side release gates. There is deliberately no rollout script.

R2 uses new artifact and isolated BuildKit directories. R1's partial exports,
build input fingerprint, package and logs remain untouched. No source from the
audit, credentials, user PDFs or business documents is included in the package.

Local R2 results (2026-09-23):
- 19/19 actual source files replayed with check-only, apply and repeated apply.
- 10 release regression tests, including late mismatch/no partial mutation,
  audited scope preservation, OCI identity/platform/hash rejection and TS syntax.
- 22 retained R1 tooling/selector/audit regression tests passed.
- 11 compressed-PDF security tests passed against the actual rebased production
  artifact module using local dependencies. The supplied quote.pdf also rendered
  to PNG without changing its SHA256. Neither the report nor the PDF is shipped.
- ARM64 image builds and production browser validation are NOT_RUN here.
