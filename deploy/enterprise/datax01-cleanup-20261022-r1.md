# datax-01 observed-file cleanup

Operator requested broader old-package cleanup after the compatibility preload
storage gate failed. Inventory from 2026-10-22 reports 52,040,716,288 available
bytes; original preload gate required 53,926,466,597 bytes. No inode shortage.
This is cleanup preparation, not evidence that cleanup or rollout succeeded.

The JSON plan pins device, inode, size, mtime and owner from the operator's
read-only inventory. The standalone Downloads script embeds that JSON via
`PLAN = json.loads(...)` in `cleanup-datax01-observed-files.py`.

Scope:

- A 3,818,332,160-byte old outer tar: remove only after every regular member
  matches an existing extracted copy. Missing, changed, linked or unsupported
  members stop deletion of the archive.
- Five old offline OCI tar files totaling 3,048,647,168 bytes: gzip in place,
  verify decompressed SHA-256 and length, then remove the original. Restore the
  original `.tar` path by decompressing before reusing an old preload script.
  Explicit rollback and retained-current-image packages are excluded.
- Nineteen historical logs totaling 6,728,051,052 bytes: same lossless procedure.
  All pinned mtimes precede 2026. Active/current log names are excluded.

Each file is checked against `/proc/*/fd`, pinned metadata and unchanged-source
checks. Existing `.gz` destinations are never overwritten. Incompressible files
are retained. Temporary compression requires source size plus 2 GiB available.
The complete batch is bounded to 30 minutes and journals verified-copy evidence
before source deletion. Re-execution is refused by an exclusive control directory.

No containerd commands, image pruning, database access or service restart.
Candidate file sizes are not a promise of reclaimed space. Review the actual
report before resuming preload; do not reduce the storage threshold.

Validation: 12 Linux Python 3.7 tests passed, covering complete-copy deletion,
mismatch/missing/symlink/unsafe-path rejection, lossless compression and metadata,
open-file refusal, changed pins, existing destinations, bounded reads,
incompressible files and journal failure. Packaged Python 3.7 syntax/import and
scope checks passed separately. Server execution is pending.
