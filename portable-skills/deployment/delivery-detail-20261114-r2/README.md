# Exact-run delivery format inspection

Read-only supplement to the archived r1 operator report. The command is:

```bash
sudo python3 -I -B -u /home/cnooc/inspect-energy-delivery-detail-20261114-r2.py --inspect-readonly
```

Scope is fixed to conversation `8e27d29f-730b-4a89-9681-36fdddc87887`,
run SHA256 `03c66c03186bbf7365ee886846fc816c0b18ee85648c66af7629fb0e80be8ab5`,
and sequences 87–90 and 99–120 (26 events). The run must still occur in the
latest two bounded run metadata rows; otherwise inspection refuses before
reading event content. Database transactions remain read-only/repeatable-read,
with the prior statement, connection, lock and child time bounds.

The diagnostic emits only fixed enums, boolean field presence/type summaries,
bounded numeric codes/counts and hashes. It does not export raw error messages,
source snippets, webhook URLs, news or message bodies. It does not read sender
programs or payload files named inside events. Source hashing remains confined
to the three fixed Gateway files from r1; no application import is needed for
those hashes. The database configuration loader may resolve the existing
database credential for its read-only connection; values are not exported.

Static classification covers requests/httpx, common session clients,
http.client requests, and urllib Request/urlopen. It distinguishes `msg_type`
from `msgtype`, fixed message type enums, known `text`/`content`/`post`/`markdown`
field nesting, JSON versus data/body arguments, and literal string/bytes or
partially known serialized structures. It never evaluates AST expressions or
executes observed commands. Conditional scopes, shell expansion, unknown
variables, and external files cannot prove the actual wire payload. A request
object construction is reported separately from a matching urlopen call.

Business codes, HTTP codes, tool status, and static request proposals remain
separate evidence. Known error wording is classified into fixed enums only;
marker presence is not a verified root cause. Unknown content remains unknown.

Local verification: Windows Python 3.12 and offline read-only Linux Python 3.10
under UID 1000 each passed 18 tests. Actual Python 3.7 verified the sealed child,
compiled host and child, and refused the wrong-host invocation with zero
queries. No production execution or message delivery is claimed.
