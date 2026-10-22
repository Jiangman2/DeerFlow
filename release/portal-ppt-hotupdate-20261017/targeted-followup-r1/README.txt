Portal PPT targeted read-only follow-up, revision 1

This tool is preparation for review. It is not rollout approval and does not
replace the frozen 70-Gateway baseline or a full source/build archive.

Operator interface (root on datax-01 only):
  python3 -B collector.py --collect --baseline /absolute/REPORT.json --out /absolute/NEW-private-directory

The baseline bytes must have SHA256
d7e53f8fb380984a2055c30f5e17bc685867716f847720fa480a8d31a811ba99.
The adjacent refresh_baseline_v3.py must have SHA256
ac6d909f579bddef02675eb248b662eba44c5b5eba1cdd65a24bf7889100a92b.
Both are checked before use. All input ancestors must be root-owned without
group/other writes. The output directory must be new; files are 0600, directory
0700. Existing evidence is never overwritten. Stdout is a bounded safe summary;
REPORT.json is private evidence, with no DSN, environment values, raw command
arguments, raw Kubernetes objects, crictl raw metadata, or exception/stderr text.

Collection scope:
- kubectl GET only for four named Deployments and their selector/owner-UID-scoped
  ReplicaSets/Pods, using frozen v3 identity logic. Every current owned identity
  must match the exact captured baseline, including all 70 Gateway identities.
  Recheck before each exec and after completion; drift stops later probes.
- Up to three noninteractive fixed Python execs, using -I -B: one Runner
  three-file hash probe, one Gateway seven-file hash probe, and one Gateway DB
  metadata probe. Each uses one lexically selected Ready Pod, recorded by UID,
  image/container identity and source scope. New source hashes are NOT a claim
  about all 70 Gateway replicas. All other container files are excluded.
- DB probe first verifies the exact known config parser source hashes without
  importing the app. It supports only /app/backend/config.yaml and no dotenv
  ambiguity. A nonempty explicit DEER_FLOW_CONFIG_PATH must equal that path;
  this branch is independent of kubectl exec's working directory/project root.
  Without an explicit path, a precisely known Gateway launcher plus /app image
  or Deployment working directory, or that same known launcher's /app exec cwd,
  must prove its application starts in backend through its literal cd backend;
  a different project root is rejected. Unknown launchers remain unproven rather
  than treating exec cwd as the application cwd. It reads only this
  bounded config file into memory, resolves only database.backend/postgres_url
  and a whole-value $ENV reference, then directly uses installed asyncpg.
  It opens a session with default_transaction_read_only=on and a repeatable-read
  read-only transaction, with connection/query/lock/idle/overall timeouts. Fixed
  SELECTs collect only read-only settings, alembic_version rows (max 16), and
  ppt_confirmation_tasks / ppt_confirmation_gates existence in current search
  path. It never imports business persistence, initializes tables, migrates,
  accesses business rows, or emits configuration or credentials. Unsupported
  resolution, missing driver, failure, or ambiguous metadata is NOT_COLLECTED.
  Even an explicit config path requires a known exact Gateway launcher that
  does not rewrite DB/config environment. An unknown launcher records
  application_environment_not_proven and performs NO DB exec/connection; an exec
  environment must not be mistaken for the running application's environment.
  The exact captured Gateway argv is verified offline against the baseline's
  d6908b47b1378e66f96dda63884a28507a7969fa96cd44e52b1a70d89903cabd
  command hash: the home/hotfix manifest's fixed --workers 1 launcher. Only its
  known literal and the captured image default are accepted; dynamic worker
  expansion is rejected. There is no arbitrary shell parsing or /proc read.
  Nonempty UV_ENV_FILE is rejected without reading it, because uv could otherwise
  give the application DB variables that a kubectl exec process does not inherit.
  Nonempty UV_WORKING_DIR, UV_PROJECT, and UV_CONFIG_FILE likewise refuse the DB
  probe because they override the known cwd/project/config startup chain.
- Local crictl inspecti only, four exact image references from the bound baseline.
  No pull/import/build. Runtime digest must match reported image ID or repository
  digest. Image ID/config digest and repository digest mapping comes from CRI
  metadata; raw image manifests/config bytes are not independently exported or
  hashed. A repository digest may designate an image manifest or an index.
  Architecture/OS and default launcher are a safe projection; arbitrary command,
  args, environment and raw metadata never leave memory. This inspects datax-01's
  local cache only, not every worker cache or candidate-image architecture.
- Provisioner exposes only the two explicit image env positions and validated
  literal image references. Secret/ConfigMap references remain undereferenced,
  with type and reference hash only. It never creates a Sandbox.

Success COMPLETE_WITH_DECLARED_GAPS means the evidence remained identity-bound
and mandatory DB metadata plus all three Runner/seven Gateway hashes were
collected. Any mandatory NOT_COLLECTED/MISSING receipt makes the overall result
INCOMPLETE. Optional local CRI/reference gaps remain separately visible. It
does not mean a database migration is applied, image source is complete, or any
release has permission to run. Agent Host/Sandbox runtime source, shared storage,
Nginx, full image bytes and new-file fleet coverage remain explicitly uncollected.

Offline qualification uses fake Kubernetes, image metadata and asyncpg, plus
the exact captured baseline bytes. Linux tests also execute the exact embedded
Python probes without networking. No production invocation is part of testing.
