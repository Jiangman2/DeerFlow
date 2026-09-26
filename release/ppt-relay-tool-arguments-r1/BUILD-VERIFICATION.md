# Build-only evidence

The user-supplied 2026-09-24T10:31:37Z runtime audit matches the immutable
R1 candidate source anchor, installed dependencies and unmasked adapter path.
Gateway is currently on ecs-zdz-pro-0022. This is a point-in-time observation;
the build controller rechecks it and the exact deployment specs.

The package reuses only generic offline OCI verification and resource controls,
with an independent pptrelay systemd/cgroup prefix. BUILD-INFRA-PROVENANCE.json
records the selected source hashes and methods. No Employee business code is shipped.

Adapter/provider/Agent Host/release regression rerun: **85 passed**, one existing
Starlette deprecation warning (45.08s). Independent build-boundary suite:
**20 passed** (23.48s), including cgroup v1/v2 limits, production-cgroup rejection,
residual-process cleanup, read-only setup, audit drift, source overlays, and
resource-canary failure stopping before the candidate build. Ruff and formatting
checks passed. Host scripts also parse with the Python 3.7 grammar. Windows
simulation is not a Linux/ARM64 BuildKit run. Actual resource limits, image build,
offline smoke inside the audited base, and real enterprise model roundtrips
remain server-side gates. No production changes or image imports performed.

Only the adapter's application source is replaced. Build metadata and smoke
helpers are copied under /tmp/ppt-relay-r1; no dependency installation occurs.
Runtime CMD/ENV/User and inherited layers are checked after export.
