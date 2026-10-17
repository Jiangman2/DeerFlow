# UID1000 fdinfo compatibility recovery — bounded pilot only

## Confirmed diagnosis

The operator's Linux 4.19.90-23.72.v2101.ky10.aarch64 probe showed root/self
fdinfo PASS, then fork + setgroups/setgid/setuid1000 => dumpable0/fdinfo EACCES13.
The pinned pilot engine first reads fdinfo in sample(), immediately after its
SAMPLE_METADATA progress receipt and before business traversal/copy. This is
not evidence of an NFS write-permission failure or unreadable source documents.

The previous result SHA is
4343feaba4f016cad3175a83f46ffd1d2c62d65262a40a91a40a1f4ca548310b.
Its progress SHA is
8ecaf06e60140d127e07f36bf6112572b55ca9220ad0393659b478bf7d85fec0.
The original zero-timeout failure and its recovery stay immutable.

## Narrow fix

Fork, clear groups, drop all IDs, and **exec ordinary trusted Python**, instead
of continuing to run a formerly-root interpreter. Linux normally resets the
ordinary executable's dumpable attribute to1 on exec; verify the actual result
on the server, never assume it. Reference:
https://man7.org/linux/man-pages/man2/execve.2.html

No PR_SET_DUMPABLE, sysctl, chmod/chown, root business reads, capabilities,
root_squash changes or mount-identity bypass. The worker is a normal UID1000
process; no credentials or privileged process memory survive exec. It receives
only verified read-only code descriptors, source/target descriptors and IPC.
Its groups/capabilities/NoNewPrivs/dumpable/fdinfo are checked before traversal.
Core rlimit is0; this is not a claim about host piped crash-collector policy.

The original engine remains byte-identical. Only run_worker is replaced in a
fresh in-memory instance. Source stays private-bind RO; NFS stays hard v4.1.
At most5000 stable files /1GiB, combined copy-write/readback payload10MiB/s.
The export must be empty before sampling and again before exclusive pilot mkdir;
any partial or unrelated content blocks without deletion or overwrite.

## Operator steps

Upload only weknora-rwx-copy-exec-recovery-20261017-r1.py to /home/cnooc on
datax-03. Execute the complete RUN-DATAX03.txt block once. It verifies its SHA,
binds prior evidence/failed units, checks no old processes or mounts remain,
then runs a no-business fork/drop/exec self-test. Only after that passes does it
create a new exclusive state and launch a new Type=exec systemd service with
TimeoutStartSec=30s, Restart=no and private mount namespace.

Expected initial output: exec_selftest PASS, then
BACKGROUND_LAUNCH_ACKNOWLEDGED. This is NOT copy acceptance.

Read status after about1–2minutes; further status reads are safe:

    sudo python3 -I -B /var/lib/weknora-rwx-copy-exec-recovery-20261017-r1/recover.py --status

Wait for RESULT.json pilot=PASS, verified_files/verified_bytes and checksum
readback=PASS. A metadata scan can take several minutes; a heartbeat means the
parent is waiting, not that copying progressed. Hard NFS I/O can outlast the
cooperative deadline. Send status on failure or stalled progress; do not retry
--recover-once, restart/reset units, delete state/partials, unmount or force-kill.

## Boundaries

No full250GiB migration, production mount/config/workload change, SQL, model
request or replica increase. Cluster inventory is historical evidence, not a
fresh Kubernetes recheck. No source contents/filenames in reports. Successful
sample readback is not full-data consistency, rsync throughput, preservation of
all original metadata, independent cross-node readback, NFS HA or crash durability.
Offline qualification uses real Linux UID1000 fork/exec and synthetic file I/O;
RO source/NFS headroom are simulated. systemd243/NFS/kernel4.19 integration
requires server receipts. This tool does not delete retained test output.
