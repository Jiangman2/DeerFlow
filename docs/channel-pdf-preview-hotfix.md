# Channel branding and generated PDF preview

## Scope

This change is limited to the Gateway and Frontend. Feishu keeps its `feishu`
provider ID and existing identity/credential flow; the displayed name is
`海能 Work`, with the supplied enterprise icon. Connection instructions and bot
product references use `中国海油通用智能体`.

Generated PDFs that use compressed object streams (including WeasyPrint output)
are not intrinsically active PDFs. For the outputs-only PNG preview endpoint,
expand these objects with the already-installed PDFium, under the same serial
lock and off the event loop. Inspect the expanded, bounded byte snapshot again
with the existing policy and render that exact snapshot. Do not initialize PDF
forms, execute actions, replace the original, or relax uploaded-file/download
policy. Encrypted, active, malformed, or oversized documents remain rejected.

The browser checks the gallery HTTP status and content type before mounting a
scriptless, opaque-origin `srcDoc` iframe. Both the response and the gallery
itself carry restrictive CSP. HTTP errors must show the ordinary preview-error
and retry UI, never raw JSON. Keep the existing 30-second loading bound.

## Verified locally

- Supplied quote.pdf: SHA256
  `70532d4c74046506d9cf2d16c070400bfc89b8d68cb14a06d1651dae43c0428b`.
  Its only finding was `compressed object streams`; it renders as one PNG page
  after inspection, without modifying the original. The user file is not part
  of the release payload.
- Regression tests include hidden JavaScript, Launch, additional actions,
  embedded files, XFA, encryption, malformed objects, expansion limits, and
  original upload exclusion.
- Browser tests cover PDF, DOCX/PPTX companions, error/retry and channel branding.

## Production boundary

The user confirmed these live images on 2026-09-23:

- Gateway: `deerflow.local/deer-flow-gateway:20260923-enterprise-arm64-rc15-ppt-master-v660-r15`
- Frontend: `deerflow.local/deer-flow-frontend:20260921-enterprise-arm64-rc15-conversation-runtime4`

Do not infer that Gateway returned to the earlier image from the fact that a
PPT rollout was rolled back. Build exact, fail-closed source deltas on the above
images, preserving their other content. Record the resolved OCI digests at build
time and retain both rollback archives. No database, channel configuration,
Secret, Runner, Provisioner, Sandbox, WeKnora, or Redis change belongs here.

For containerd OCI exports, match the complete image name from
`io.containerd.image.name`; use `org.opencontainers.image.ref.name` only as a
legacy full-name fallback when the containerd annotation is absent. Modern
exports normally use the latter field for the tag alone. Never select an
arbitrary first descriptor, accept a tag-only match, or skip the blob hash.
The R1 OCI-FIX1 control-only repair backs up the original builder and package
manifest, checks the original script SHA256, and changes only this lookup and
its error detail. It does not modify or repeat the exported image contents.

The candidate build bundle is not a production rollout authorization or proof
of ARM64 runtime acceptance. Build offline in resource-bounded, isolated
BuildKit on `/opt`, never colocated with Gateway. Validate the resulting images,
preload candidate AND rollback images on eligible nodes, and perform a fresh
activity/health audit before issuing rollout commands.

## R2 actual-source rebase

The 2026-09-23 production audit found three context mismatches out of 19 targets.
Gateway's manager has no local HEAD `AsyncKeyedLockTable` import; both Frontend
locales already carry the enterprise product-name update. R2 derives its narrow
edits from those actual sources and frozen R1 payload, preserving the manager's
existing implementation and already-deployed branding. It does not merge the
dirty checkout or silently retry with fuzzy matching.

All targets carry exact before/after hashes (including a no-op constant check).
Both base image trees complete read-only source-check stages before native smoke
and frontend build. The OCI config/manifest must match the runtime identity in
the source audit. R2 uses independent artifact and BuildKit paths, preserving
R1's original package, fingerprint, logs and partial outputs. Generated baseline
locks contain hashes, not the audit's full source or the user's PDF.
