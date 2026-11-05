# Alloy recovery and current platform follow-up

The operator's r3 retained relocation completed all 84 selected files in
645.756 seconds with zero skipped/error records. It reclaimed 6,575,296,512 bytes
(6.124 GiB). Root availability after completion was 74,494,357,504 bytes
(69.378 GiB). Report SHA256 is
`9f24e454a47499dac8540263c485e601e9c5334271fe4eafd09af098ef134368`.
This confirms that the first approximately 5 GiB file was progressing during
the earlier apparent pause. r2+r3 reclaimed approximately 22.40 GiB of original
allocation. Retain both off-root copies; original paths depend on them.

## Evidence limitations and current findings

`log(31).txt` starts inside node statistics, so its leading top-level JSON fields
are missing. It is not a complete r4 report. Complete trailing sections were
decoded independently without reconstructing or inventing missing fields.
`platform-health-truncated-field-20261028-r4.json` records this limitation.

At sample time 2026-10-28 11:12 Beijing:

- All 18 node condition records showed Ready, without pressure conditions.
- Alloy was ready on datax-02, datax-03 and ecs-zdz-pro-0022; datax-01 Pod
  `alloy-rxtqf`, UID `6305d4cf-b74e-4b41-9e0b-74cae1b8b2a3`, remained Pending
  with ImagePullBackOff. Grafana and Loki active Pods were ready.
- The three healthy Alloy instances reported config/image ID
  `sha256:8b089d2834ca96f22974b0ea75528dbd3806c1bf38d67faa4483c72a2e5cab1e`.
- Gateway `gateway-84b84764c8-zzp89` reported an OOMKilled restart at
  10:59:09 Beijing and was ready at collection. Other Gateway/adapter restart
  records included earlier dates. These are not authority to roll the deployment.
- Three sandbox Services had no ready endpoint at the sample. Their Pods were
  Running but not ready. A separate terminating sandbox was ready; normal task
  creation/removal can change this inventory quickly. Do not delete task Pods
  or declare durable failures from one sample.

r5 retains r4 field compatibility and adds controller resource requirements,
deduplicated PVC storage observations, warning grouping and compact service
display. It passed 25 Linux Python 3.7 tests. The operator was asked to capture
its output with tee so terminal scrollback cannot drop the beginning again.
Full current controller status, Alloy failure categories, backup schedule and
recent log signals remain pending this untruncated evidence.

## Verified Alloy offline package

Docker Hub was not reachable from the local preparation computer. The exact
linux/arm64 manifest was fetched through m.daocloud.io and then pinned by digest:

- Image: `docker.io/grafana/alloy:v1.11.0`
- Manifest: `sha256:87a8fcab1a8bb39047ee25ccd749b5b89de1eeb41f8f85dac7b906894d1a11b8`
- Config: `sha256:8b089d2834ca96f22974b0ea75528dbd3806c1bf38d67faa4483c72a2e5cab1e`
- OCI archive: 141,445,120 bytes, SHA256
  `ead5e8e157b563aeb6fe11efc008621676a14b2d6fe6b1c565bf9a5a9679f839`.

The manifest config matches all three healthy peer image IDs. Every compressed
blob and all five uncompressed layer diff IDs were checked; total uncompressed
layer bytes are 490,575,872. The published manifest/config/layer bytes are kept
unchanged; only the OCI transport index assigns the original Docker image name.
The local ARM64 image `--version` invocation succeeded: v1.11.0, revision
7bd7f7f, linux/arm64. This does not establish why the production registry request
failed; local network failure is not production network evidence.

The recovery script validates current node UID/platform/pressure, the original
waiting Pod UID and DaemonSet owner, requested v1.11.0, a usable local-image pull
policy, a matching healthy peer, immutable package contents, space and unchanged
preflight state. It imports only that OCI archive into k8s.io and observes the
existing Pod. It never patches a controller, deletes a Pod, pulls externally on
the server, changes TLS settings, restarts a running service or prunes images.
Existing reference mappings and the Gateway, frontend, Runner and SkillHub
Deployment specs are checked before/after. Normal collector startup can resume
log transmission according to its existing configuration.

An Always pull policy or changed target/version causes refusal before import.
The script records an exclusive once receipt at
`/var/lib/portal-alloy-image-recovery-20261028-r1`. A timeout/import uncertainty
must be inspected; do not blindly rerun or delete the control directory.
Production recovery acceptance is pending the operator's execution receipt.

14 Linux Python 3.7 tests passed, including real delivered-package transport,
compressed and uncompressed layer verification inside Linux, fault/refusal
cases, and a simulated once-only import/unchanged-workload recovery flow.
The actual containerd import on production is not claimed by these local tests.
