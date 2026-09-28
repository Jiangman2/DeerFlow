# Production source alignment (2026-09-28)

This commit is based directly on `83a749fc8191ffb425625261d4cd6cce272b7dad`.
It aligns the ordinary Gateway and Frontend source files with the currently
deployed images, plus the captured Provisioner, MinerU adapter, shared Skills,
PPT Agent Host scripts, and ordinary AIO Markdown converter. The file mapping
and comparison counts are in `SOURCE-ALIGNMENT.json`.

The dedicated Runner was updated after the original core collection. Its
2026-09-28 R3 release package was verified against the local release archive
SHA-256 and manifest lock. That release changes exactly one file from the
captured R2 Runner image. The full resulting 543-file source tree is under
`docker/ppt-master-runner/production-source/`, and its base/current image
digests and source hashes are in `docker/ppt-master-runner/RUNNER-LOCK.json`.
It is separate from the context-fixed ordinary Gateway source. Build the R3
Runner overlay only from the verified R2 Runner base image using
`docker/ppt-master-runner/Dockerfile.production-r3-overlay`.
The active Runner Pod reported image ID
`sha256:a9cc1a5468064e51327f1ea6a43e7d8448b8b787f9ddfa56325402f1eedd3746`,
matching the R3 build receipt's config digest.

The production deployment image tags checked on 2026-09-28 are:

| Component | Image tag |
| --- | --- |
| Gateway | `20260927-ppt-context-fix-r1-gateway-5f2726f3daf0ed04` |
| PPT Runner | `20260928-ppt-receipt-r3-runner-c1c876c9b520a0fe` |
| Frontend | `20260927-ppt-runtime-r2-frontend-3959a039ee854dca` |
| Provisioner | `20260923-enterprise-arm64-rc15-ppt-master-v660-r16-split` |
| PPT Agent Host | `20260927-ppt-runtime-r2-agent-host-3959a039ee854dca` |
| SkillHub Server | `20260908-enterprise-arm64-rc15-skillhubsession2` |
| SkillHub Web | `20260907-enterprise-arm64-rc15-skillhubux-dashboard1-r2` |
| WeKnora App | `0.8.0-enterprise-proxy1-anydoc1-arm64-20260912` |
| WeKnora UI | `0.8.0-enterprise-sso2-r5-sidebarlabels1-arm64-20260918` |

The runtime source came from the server-side verified core snapshot committed as
`330ce1227e05f24b944f3efde36906234bd71196` on
`codex/production-baseline-20260928-verified-core`. The private R7 supplement
archive has SHA-256
`d6ae36377a196bd882f0a89c4433284217326c3b7706af0a70bcad49b`.
The external-service R8 archive has SHA-256
`264fd43a02caec2a6988fe20c9e4f587f37915e9e52dd9a588a636f50563bc75`.
The server collectors reported no production workload mutation.

The PPT Agent Host Skill is kept under `vendor/ppt-master-v6.6.0/` because the
running Agent Host uses that tree, while the shared Skills volume contains an
older `skills/public/ppt-master` tree. The sandbox Dockerfile uses the vendored
tree and the captured host scripts. `PPT_MASTER_UPSTREAM.env` records its
upstream tag, commit, and original Skill tree. The Dockerfile and dependency
list are reviewed build inputs from the local release materials; the source
files loaded at runtime were checked against R7 separately.

The current SkillHub Server image is an overlay on its preceding image. Its
session-bootstrap CSRF source is under `k8s/skillhub/server-overlay/`. The
current SkillHub Web asset patch is under `k8s/skillhub/web-overlay/`. The
WeKnora App AnyDoc build source is under `vendor/weknora-app-20260912/`; its
Dockerfile uses an upstream base image by digest. The 2026-09-20 WeKnora UI
runtime patch is in `k8s/weknora/ui-patches/patch-legacy-office-preview.sh`.
Supporting WeKnora model-network and spreadsheet-policy source is in
`external/`.

## Rebuild limits

This is a source-alignment commit, not a claim of byte-identical image builds.
The WeKnora UI source for the 2026-09-18 image was not present in the server
build directory; R8 captured its deployed compiled assets separately as private
evidence. The WeKnora App copy excludes the frontend tree, examples, selected
test/example files with token-shaped fixtures, and a demo SQL migration with a
token-shaped comment. These are not used by the captured App Dockerfile's Go
build and its named test packages, but the vendored directory is not a complete
upstream repository. The server's full Kubernetes resource JSON is also private
evidence and must not be copied into Git because literal configuration values
have not been cleared for publication. Live resource values, external base
images, build secrets, and the production ARM64 build environment are needed
for a complete release reproduction.

Do not infer deployment from this Git commit. Check each production workload's
current image digest and build provenance before a future rollout.
