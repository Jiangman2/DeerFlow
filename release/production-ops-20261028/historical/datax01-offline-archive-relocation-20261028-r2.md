# Datax-01 offline archive relocation continuation r2

Prepared and executed 2026-10-28. Operator receipt:
`datax01-offline-archive-relocation-field-20261028-r2.json`.
All 61 archives were relocated, with no skips or reported errors. Original
allocation reclaimed is 17,477,685,248 bytes (16.277 GiB); observed root
availability increased from 50,534,113,280 to 67,976,716,288 bytes
(47.064 to 63.308 GiB). The difference reflects concurrent filesystem activity.
Remote detailed report SHA256:
`d4282c5256a00369054d59949b8b59788bb7b78c4833c7f59e6cd4d322317e10`.
The receipt reports zero service restart requests, containerd commands or
database access. This does not replace a fresh service-health check.

The r1 operator report records 61 skipped archives, all `SOURCE_BIND_MOUNTED`,
zero completed relocations, and report SHA256
`7321d078df1148f9ca26a649f033c187444b07ae32efd6ab521c3a391c6cb3ac`.
The subsequent read-only diagnosis found 40 namespace/root groups without read
failures. Only one group covered the archives: PID 1510, NetworkManager, a
read-only `/home` mirror with source root and mountpoint both `/home`. All 61
archives were covered by that directory mount; none was directly file-mounted.
The same `/opt` and r1 destination directory were visible in that process root.
This explains the r1 refusal; it does not prove these archives were actively used.

## Narrow correction

r2 preserves r1 and first verifies the exact prior no-relocation report. Its new
once-claim/destination is `/opt/portal-offline-archives-20261028-r2`.
Only the diagnosed structural case is accepted: a read-only `/home` mount at
`/home`, with the same host root, same `/opt`, same destination directory and
same original source inode visible from the process root. The actual copied
file's identity is also checked through that root immediately before the final
replacement. Namespace caching includes process-root identity so a chroot is
not conflated with another process in the same mount namespace.

Direct archive mounts, writable/relocated directory bind mounts, differing
process roots, invisible or mismatched destination files, open/memory-mapped
archives, changed files and insufficient space remain refusal conditions.
Process identity/name is diagnostic information, not an authorization bypass.
Open-file/mapping checks still run for each process even when mount metadata is
shared. This only changes the local archive tool; no service mount or sandbox
configuration is changed.

As with r1, this is retained off-root relocation, with the original pathname
becoming a symlink. It does not delete archive contents or reclaim containerd
objects. The source directory's mount flags are not copied onto the target
filesystem; source file owner/mode/xattrs are preserved. No running service is
known to use these offline packages, and an observed open archive is rejected.

The compact summary now includes failure counts and only reports original-path
symlinks when at least one relocation completed. Detailed results and a durable
before-replacement journal remain in the r2 directory. Keep both r1 and r2
directories; never remove successfully relocated copies as temporary cleanup.

## Operator command

```bash
sudo python3 -I -B -u /home/cnooc/relocate-portal-datax01-offline-archives-20261028-r2.py --relocate-offline-archives-once
```

Copy the standalone r2 script to datax-01 first. Do not rerun r1. Return the final
JSON; if interrupted or failed, retain output and reports without blind retries.
CPU nice 19, idle I/O priority, 16 MiB/s copy and 32 MiB/s verification are
unchanged. Actual free space is subject to concurrent task writes.

28 tests passed on Linux Python 3.7, including the original 18 transfer/refusal
cases and namespace visibility, chroot, file-mount, late-copy verification and
prior-receipt checks. Namespace cases use synthetic mount records; production
visibility is freshly rechecked by the script. No production transfer success
is claimed before the operator returns its receipt.
