# Current confirmation-enabled Runner build

Build from the repository root with
`Dockerfile.production-confirmation-r1`. Its Dockerfile-specific ignore file
exposes only the 19 source files and build verification inputs it copies.
`confirmation-build-manifest.json` describes those current source inputs; the
verifier checks their exact bytes and compiles Python without importing the app.

The base is the verified ARM64 R4 Runner manifest
`deerflow.local/deer-flow-gateway@sha256:eb23de08345bab65c78264f99ce33085c4ea3574ff8d6281b62418f644fc06cd`.
It must be available to the selected builder. This is an overlay on that base,
not a dependency rebuild. No `uv sync`, package download, or dependency upgrade
is part of this recipe. The captured `production-source` subtree is not a
standalone backend distribution: uncopied packages and installed dependencies
remain supplied by that fixed base. Do not run it as a replacement full backend
tree or resolve missing inherited packages by silently installing dependencies.

```sh
python3 docker/ppt-master-runner/verify_confirmation_build.py --source-root .
docker buildx build --platform linux/arm64 --pull=false \
  -f docker/ppt-master-runner/Dockerfile.production-confirmation-r1 \
  -t deer-flow-ppt-runner:confirmation-source .
```

When changing a copied file, deliberately update its bytes/SHA-256 in the
manifest after review. The verifier rejects missing, changed, duplicate, or
unexpected source mappings; it does not silently regenerate the lock.

The source closure includes the shared confirmation modules, persistence model
and migration, Runner agent, and the receiving `features.py` and
`ppt_confirmations.py` routers. The existing `app.py` already includes
`features.router`. Copying only `agent.py` does not deliver that closure.

The base has no entrypoint. Its default command starts the ordinary Gateway on
8001, so this recipe explicitly sets `/app/backend`, the `ppt-master-runner`
runtime profile, and the existing virtualenv's `python -m uvicorn` on 8003.
It preserves the base entrypoint and dependencies. Kubernetes may still supply
an explicit command. A real startup needs the operator's configuration, database
and authentication inputs; the build checks do not fabricate those inputs or
connect to a database.

`Dockerfile.production-r3-overlay` and `RUNNER-LOCK.json` describe the historical
R2-to-R3 release. Keep those historical files unchanged. They copy only one file
and contain historical hashes, and are not the current build entry point.

The original hotupdate candidates were built and qualified separately. This
source recipe adds the receiving routes missing from that frozen Runner payload
and therefore does not claim an old candidate's image identity. The new image
manifest/config digests must be recorded from an actual build. Local scratch
COPY checks and Python compilation are not a complete image build, ARM64 runtime
startup, database migration, or production release approval.
