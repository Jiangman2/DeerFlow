# Exact fdinfo failure recovery

Recover only the pinned UID1000 SAMPLE_METADATA EACCES receipt. Older pilot and
startup-recovery scripts, units and evidence are immutable. No permission change,
PR_SET_DUMPABLE, root business I/O, mount-identity bypass, cleanup, signal, full
migration, workload change, or automatic retry belongs here.

Keep the old engine byte-identical. Replace only its worker launcher in a fresh
module: fork, clear groups, drop all IDs, exec trusted Python with no_new_privs.
Pass only verified read-only code descriptors and the necessary data/IPC FDs.
Check UID/GID, capabilities, dumpable and fdinfo before business traversal.
Run that exact bootstrap in a no-business self-test before creating the attempt.
Require empty NFS target in the unchanged engine; preserve RO source, private
namespace, root_squash, 5000 files/1GiB/10MiB combined I/O limits. Use a fresh
exclusive unit/state and explicit nonzero startup timeout. Status is read-only.

Test source and exact delivery including real Linux fork/drop/exec, synthetic
descriptor-relative copy, denied fdinfo and corrupt code rejection. Offline
systemd, NFS, read-only mount and kernel4.19 integration remain unqualified.
