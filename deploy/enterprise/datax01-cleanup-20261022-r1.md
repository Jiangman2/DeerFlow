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

## Field receipt and preload continuation

The operator subsequently returned a matching 6,318-byte report, SHA-256
`44bed22e700b265adc7936cabe01819b5be9976b6a8120e25bacc50fb11ef4e8`,
archived in `datax01-cleanup-field-20261022-r1.json`. The outer archive was removed
after verifying eight files; all 19 logs were compressed and verified. Five OCI
archives were retained because compression saved less than the configured
minimum. Available space increased from 51,923,722,240 to 62,276,112,384 bytes
(net 9.64 GiB); summed file allocation savings were 10,420,715,520 bytes. These
differ because filesystem availability also reflects other concurrent writes.
No service restart, containerd command or database access was performed.

The datax-01 r2 preload embeds the original r1 script (SHA-256
`4b540f4e9cb1241ebfc6641c6fb9a1a7ee5dd91968f6e1f711fe9b46c776e2a1`)
with `resume-oa-compat-datax01.py` installed before its main entry and the flag
changed to `--resume-datax01-preload-once`. It verifies the exact previous
pre-import refusal and cleanup reports, restricts execution to datax-01, and
uses a new exclusive control directory. The original once marker remains.

The resource helper inherited a stale control-directory constant. r2 binds it
to the actual directory, restoring its existing 64 MiB accounting allowance.
All reserves and live admission checks remain; the corresponding observed
requirement becomes 53,993,575,461 bytes. Cleanup's historical free-space value
does not substitute for a fresh check. Image digest, embedded sources and
existing references are unchanged. Seven Linux Python 3.7 resume tests passed.

Generated r2 script SHA-256:
`9269eb40ebe54d84a6b1cbbd038dd1215977f689abaff0c42d8a6806b708c7f7`.
Runner preload on datax-01 and subsequent runtime qualification remain pending;
the other fourteen accepted preload receipts remain applicable.
