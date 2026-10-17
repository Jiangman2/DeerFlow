# Python3.7 O_PATH inheritance recovery

This module recovers the exact r1 exec worker DROP_EXEC/EBADF9 result, SHA
f22c5ab2afa77f9dd6da674a9237353da13aa25682a3d957302c0d374158db13.
Keep all three earlier attempts, their units and delivered code immutable.
Bind their receipts and require no prior live worker or persistent mounts.

Use fcntl F_GETFD/F_SETFD to clear and verify FD_CLOEXEC on the five inherited
descriptors. Do not ignore EBADF or remove O_PATH/root_squash. Before creating
the new state, exercise the real O_PATH directory handle through the same
fork/drop/exec path and reopen it as UID1000 against a synthetic root directory.
Retain identity, capability, fdinfo and mount checks and the byte-pinned engine.
Never call an old start()/recover() entry point or modify a prior unit/state.

Scope remains at most5000 stable ordinary files/1GiB, combined copy/readback
10MiB/s, private namespace, RO source, empty target, exclusive new pilot output.
No chmod/chown, cleanup, automatic retry, full migration or production change.
Status is read-only. Tests must reproduce the old CPython3.7 ioctl decision
with real O_PATH syscalls, then exercise corrected inheritance and actual
UID1000 synthetic copy. Do not claim actual Python3.7/kernel4.19/NFS integration
from a Python3.12 offline container. Keep source and exact-delivery checks.
