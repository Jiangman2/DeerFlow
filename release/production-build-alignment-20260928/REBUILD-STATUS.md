# Production image rebuild status (2026-09-28)

The 7·16 branch contains source recovered or compared against the production
runtime, but a source match is not proof that a fresh build creates the same OCI
image. `PRODUCTION-IMAGE-LOCK.json` is the image-reference and digest ledger.
Only an ARM64 candidate whose complete manifest and referenced blobs verify
against that lock establishes byte identity for an image. Runtime Pod `imageID`
values establish the running image configuration identity; they do not by
themselves establish the manifest or its complete layer graph.

The lock records 19 distinct custom image references in the long-running
Kubernetes workloads, including init containers. Five have a recorded full
manifest digest, and seven have a recorded configuration digest. The Sandbox
Agent Host and ordinary AIO images are additional runtime images created by
the Provisioner; they are outside those 19 workload references.

| Image group | Build input currently in this branch | Remaining proof or input |
| --- | --- | --- |
| Ordinary Gateway and Frontend | Captured runtime source, R4 Gateway overlay payload, and operator build/rollout receipts | Independently build ARM64 candidates from this checkout and compare full OCI manifests. |
| Isolated PPT Runner | Captured R3 base source and the exact R4 overlay payload in `r4-build/` | Build from the locked R3 base and compare the complete R4 image. |
| PPT Agent Host and ordinary AIO Sandbox | R7 runtime source, vendored `ppt-master` 6.6.0, and reviewed Dockerfiles | Independently rebuild against the exact base images and verify both OCI images. |
| Provisioner and MinerU adapter | Captured runtime source and current Pod configuration IDs | Pin the complete build inputs and collect full production manifest digests before byte-identity checks. |
| WeKnora UI | Complete R5 upstream frontend, enterprise R5 overlay, and production `sidebarlabels1` overlay | Build the two-stage ARM64 candidate and compare the final OCI manifest and blob graph. |
| SkillHub Web | Production overlay source and an R8 verified OCI image | Build from its exact preceding base image and compare the candidate. |
| SkillHub Server | Current CSRF patch source, server overlay Dockerfile, upstream commit, and JAR SHA-256 | Supply or reproducibly build the missing `skillhub-app.jar`; verify the resulting OCI image. |
| WeKnora App | Captured Go/AnyDoc build source and digest-pinned runtime base | Verify source/build-context completeness and build an ARM64 candidate with pinned toolchains. |
| WeKnora DocReader, custom PostgreSQL, and supporting SkillHub images | Deployment image references and partial upstream/build context | Capture each exact upstream source or base-image provenance and a complete production manifest; add a repeatable build recipe. |

The production Kubernetes resource JSON is private evidence because it may
contain environment values and Secret references. The public lock records only
image names and cryptographic identities. Build logs and server evidence must
be reviewed and redacted before any Git commit.

To claim that a colleague can rebuild a production image from this branch,
record all of the following for that image:

1. The exact source commit, build context, Dockerfile, dependency lock files,
   toolchain, architecture, build arguments, and base-image digests.
2. The current production image reference plus its full OCI manifest digest.
3. A fresh candidate built from the checked-out branch, verified by
   `verify_oci.py` against the production lock.
4. Any runtime-only configuration and mounted content needed for equivalent
   service behavior, separately from the image build.

The operator reports that R4 Gateway and Runner candidates were imported and
rolled out. Their Pod configuration IDs match the R4 Build2 locks. The files
in this branch have not yet produced an independently rebuilt ARM64 candidate
whose full OCI manifest and blob graph match those running images. Browser and
business Token acceptance also remain open.
