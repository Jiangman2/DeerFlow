# Skill 2.1.6 — keep the report reader inside the runtime preview

The operator's named task succeeded, but its scheduled Work outbox failed at
revision 1. The stored `last_ai_message` is 4,720 bytes with no reader link; the
complete final event is 9,125 bytes with one valid reader. Replaying the installed
Gateway 418 formatter yields an empty notification from the stored preview and
the required single link from the full event. Connection metadata is connected
with a target; that does not establish actual provider-send health.

Local `RunJournal` and SQL run persistence truncate the convenience preview to
the first 2000 characters. Gateway's scheduler consumes that convenience field.
This release leaves the platform limit and deployed Gateway unchanged. The
Skill program emits the reader as the first standalone line of the complete TXT;
the body remains complete and the conversation link remains last. The Skill
requires that exact TXT as its one final response without moving the first line.
Gateway 418 already extracts that line and emits only its Markdown reader link.
No extra model decision, summary pass, collection or send tool is introduced.

Only `SKILL.md`, `scripts/daily.py` version/compatibility call sites and
`scripts/delivery_text.py` change. Acquisition, news evidence, calculations,
freight dates, HTML/PNG rendering and browser runtime are byte-identical to 2.1.5.
Legacy footer formatting remains supported; final-text compatibility is
idempotent, including Windows newlines, and refuses another task's header.

The installer is derived from the exact archived 2.1.5 publisher. It binds the
actual serving Nginx generation 3 and accepted Gateway 418 receipt, backs up the
current 108-file Skill, stages the candidate, then verifies it using the fixed
cached ARM64 image, UID 1000 and the real public-only mount. It runs 39 tests,
renders the full fixed PNG and dated-freight case, and tests its actual generated
TXT through two 2000-character truncations and the installed Gateway formatting
functions in isolation. Those are synthetic sends: no sender is invoked.
Only a qualified Skill is published; protected Deployment specs must remain
unchanged. It neither updates Nginx/Gateway/Provisioner nor reads Secrets,
collects provider data, rewrites old reports or replays a failed notification.

The unshipped first package failed its local version guard and is preserved.
Use only the finalized package in `release/energy-work-preview-skill-20261115-r2/`:

```bash
sudo python3 -I -B -u /home/cnooc/install-energy-work-preview-skill-20261115-r2.py --install-work-preview-skill-once
```

Windows and Linux/Python 3.10/UID 1000: 39 Skill regressions and 10 installer
boundary checks; five preview integration cases also live in `backend/tests/`.
The exact sealed fixture relocation/compose probe and actual Python 3.7 entry
protection passed. `make test` was attempted before and after; make is absent
from this Windows environment, so the full repository suite was not run. Real
ARM64 rendering is enforced before production publication; a future scheduled
Work delivery is still business acceptance. Historical failed notifications are
not automatically retried. Preserve failure output and do not replay activation.
