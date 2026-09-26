# Capacity wave 1

- This package is preparation, not rollout. `cluster` and `node` must stay read-only
  except for a new report file. No Kubernetes write verbs, Secret data reads,
  arbitrary container commands, remote shell execution or credentials in output.
- `export` / `preload` require explicit acknowledgment and a fresh same-image plan.
  Preserve tag/digest, ARM64 and OCI checks. Never interpret an inspection error as
  image absence. Do not overwrite a differing existing tag.
- Node 0015 uses its separate `/mnt/data` filesystem; `/opt` is its small root disk.
  Never move containerd state or change its configuration as part of this package.
- Keep its cordon and current production replicas unchanged. Canary, traffic
  admission and rollout need a separate reviewed change bound to the live baseline.
- No dependency installs or network image pulls. Python 3.6 stdlib compatibility,
  LF/no-BOM manifests, no output overwrite, and bounded subprocesses are required.
- Tests use synthetic objects and archives, not production access. Distinguish unit
  tests, Linux integration, on-node preload, actual Pod startup and browser acceptance.
