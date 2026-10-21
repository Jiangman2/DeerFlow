# datax-03 additive test export

The user selected keeping existing files on datax-03, served to two App nodes.
This stage creates only one empty synthetic directory on the existing XFS disk,
a root-managed bind-export anchor and two host-specific test exports. Do not
export the existing knowledge/docreader roots, copy business data, change an
existing permission, restart services, change firewall/Kubernetes or scale here.

/opt is UID1000-owned: never silently relax generic root-private checks. Open
its component chain without following links and bind the exact operator-pinned
device/inode/UID/GID/mode tuple. Hold the source-parent descriptor; create the
new synthetic child relative to it. Bind from the open child FD using mount(2)
to a new root-controlled anchor outside /opt. Verify mount identity/flags and
path pins again before exporting. Preserve existing exports/configurations.

Use the verified read-only audit helper, exclusive root-private state and durable
intent before every mutation. Only freshly created directory FDs may be chowned
or chmodded. Keep root_squash, sync, secure, sec=sys, mountpoint and exact two
clients. No global export reload or removal. Status only reads; uncertain or
interrupted operations retain evidence and are not replayed/rolled back.

Qualify source and exact delivery under Python3.7 as well as modern Python.
Use real Linux descriptor/ownership tests with mocked mount/export syscalls;
do not claim actual kernel NFS, bind mount, remote I/O or reboot persistence from
offline tests. The next stage must bind this receipt before new cross-node probes.
