# Fixed scheduled-run and capacity inspection

This operator is not an activation or reclamation command. It inspects the
operator-supplied identifier `887ea9f6-1347-4f0f-ab6d-1295ae3ead49`, then produces
a provisional Sandbox capacity review plan. Run only on datax-01 as root:

```bash
sudo python3 -I -B -u /home/cnooc/inspect-energy-scheduled-run-and-capacity-20261112-r1.py --inspect-scheduled-run-and-capacity
```

The fixed-ID lookup prefers the exact Graph run ID. Limited fallbacks distinguish
an occurrence ID from a task ID; a task's latest occurrence is explicitly not
proof of the reported 11:52 execution. The diagnostic projects scalar run data,
the recursion-limit scalar and at most the first eight plus last 24 relevant
events. Event content is capped at 8 KiB each and 62 KiB in total, plus a 2 KiB
run error budget. Only whitelisted classifications and fingerprints are returned.
An AI tool proposal is not an execution receipt. Neither repetition nor a missing
receipt alone establishes lack of progress. Checkpoint blobs and prompts are not
read. No task is retried, no recursion budget or Gate is changed, and no Work
message is sent.

The capacity plan compares up to 256 actual Sandbox Pod projections, trusted
platform owner/run metadata and existing execution/creation locks. Pending or
running records, waiting/unknown states, PPT history, nonempty run metadata,
unverified source/storage identities and terminal ages under 30 minutes are
excluded. Lock files are opened without creation or truncation; nonblocking
exclusive checks are brief but can temporarily contend with runtime activity.
Pod specifications and identities are rechecked before candidates are returned.
Candidate means only `NO_ACTIVE_RUN_AND_LOCKS_AVAILABLE_REQUIRES_REVIEW`.
Uploads, conversion and previews without execution leases are not proven idle.
The program does not support deletion or authorize a future deletion.

Validation: Linux Python 3.10, UID 1000, no network and dropped capabilities:
33 checks passed. Windows: 26 passed and seven Linux-specific checks skipped.
The sealed parent/child compile and resource fingerprints, tamper rejection,
fixed ID and zero-query wrong-host guard passed with actual Python 3.7.17.
This is local/offline qualification; no production observation has yet been
returned for this package. See the sibling `.package.json` for exact bytes and
hashes. Earlier operator receipts and completed release tools remain unchanged.
