# O_PATH inheritance recovery for datax-03

The most recent pilot failed in recover.py:291, launch_worker(), DROP_EXEC,
OSError/EBADF9, before any worker progress or source contents were read. The
fixed fdinfo identity self-test had passed on the real kernel4.19 server.

The missed case is the NFS directory's O_PATH descriptor. CPython3.7's
os.set_inheritable first uses ioctl(FIONCLEX); EBADF is returned directly,
without falling back to fcntl. Linux supports F_GETFD/F_SETFD on O_PATH. r2
therefore clears FD_CLOEXEC directly using fcntl and verifies the result; an
actually invalid descriptor still fails. References:
https://raw.githubusercontent.com/python/cpython/v3.7.17/Python/fileutils.c
https://man7.org/linux/man-pages/man2/open.2.html

Before launch, a no-business probe captures this server's legacy API behavior.
The actual worker bootstrap then inherits synthetic O_RDONLY and O_PATH handles
to /, drops all IDs/groups, execs trusted Python, verifies zero capabilities,
NoNewPrivs, dumpable1, fdinfo and O_PATH validity, and reopens the directory as
UID1000. The tool creates no new attempt until this complete self-test passes.

## Run once

Upload weknora-rwx-copy-exec-recovery-20261017-r2.py to /home/cnooc on datax-03.
Execute the complete RUN-DATAX03.txt block once. Earlier scripts/states/units
remain untouched. The tool pins both failed recovery receipts, requires no
old unit processes/mounts and launches a fresh r2 unit/state.

Expected self-test fields:

    identity_selftest=PASS
    opath_exec_inheritance=PASS
    opath_reopen_as_uid1000=PASS
    legacy_opath_api_probe.fcntl_opath_inheritance=PASS

The legacy API may report FAIL/errno9, which is diagnostic evidence rather than
a failed new self-test. BACKGROUND_LAUNCH_ACKNOWLEDGED is only launch receipt.
Read status after about1–2minutes and send the complete output:

    sudo python3 -I -B /var/lib/weknora-rwx-copy-exec-recovery-20261017-r2/recover.py --status

RESULT.json pilot=PASS and sample_checksum_readback=PASS are copy acceptance.
If still sampling, another read-only status after a minute is sufficient.
On failure, send status; retain state and partial output. Do not rerun launch,
restart/reset units, chmod/chown, delete state or force-terminate NFS workers.

## Preserved limits and remaining acceptance

At most5000 files/1GiB; combined copied and checksum-readback payload10MiB/s.
All source traversal/copy is UID1000 via a private RO source bind. NFS remains
hard v4.1/root_squash. Existing content blocks the pilot; it is never overwritten.
No full250GiB copy, production mount/config change, SQL/provider request or scale.
Cluster inventory remains historical. Partial output is retained for review.

Local qualification runs both Python3.12 and actual Python3.7.17. The latter
reproduces the original script's EBADF without emulation, then checks the fixed
fork/drop/exec plus synthetic file fsync/SHA256 readback. RO/NFS headroom are
modeled. Actual ARM/kernel4.19/systemd243/NFS integration awaits server receipts. Pilot throughput
is throttled/cached readback and cannot establish maximum throughput, full-data
consistency, HA, crash durability or metadata-preserving full migration.
