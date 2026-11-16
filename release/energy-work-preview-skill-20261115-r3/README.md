# r3: correct the freight qualification context

The operator's r2 installation stopped before publication. The fixed ARM64 PNG
matched the accepted sample, but the subsequent dated-freight check raised a
Skill `Failure`. The old live Skill was not switched; the maintenance Pod and
ConfigMap were removed. The original report was reconstructed from the supplied
JSON and matches its exact 3,995 bytes and SHA256
`94c2c38a48ecf65b7121f80db20d07ee5839936d691bacf4c680c0fd3caecc1e`.

The exact sealed freight probe now reproduces the failure locally as
`DELIVERY_TEXT_INVALID`: after a successful build, the test supplied the newly
linked TXT to compatibility composition with an explicitly unlinked context.
The first line is now the reader, so that old test context could not validate
the report. The qualifier now passes its same current synthetic host context
and enables the documented reader header. It does not weaken the Skill's
cross-task URL or missing-context checks.

r3 contains exactly the same 107-file code ZIP as r2. Only the installer and
sealed qualifier change. It binds the failed r2 receipt before claiming a new
r3 operation, preserves all old receipts, staging and backup directories, and
keeps the same protected Deployment identities and live old-tree checks. A new
qualification must still pass all 39 regressions, the fixed real ARM64 PNG,
the dated-freight real render, missing-artifact/context checks, two 2000-character
preview truncations and the installed Gateway 418 notification functions.
Safe bounded Skill error codes are now reported instead of hiding them behind
the generic qualification error. No raw failure detail is exported.

Local verification includes 13 installer checks on Windows and Linux,
39 Skill cases and both exact sealed composition and freight probes on Python
3.10/UID1000. The local full-probe check mocks only PNG rendering; production
still requires real browser rendering before switching. Python 3.7 entry/asset
loading and wrong-host protections are checked separately. Five Gateway-preview
integration cases pass in the backend suite. The full repository suite was not
run because `make` is unavailable in this Windows workspace.

Use the r3 installer, never replay r2 or delete its marker:

```bash
sudo python3 -I -B -u /home/cnooc/install-energy-work-preview-skill-20261115-r3.py --install-work-preview-skill-once
```

This modifies only the shared Skill after qualification. Gateway, actual serving
Nginx, Provisioner, Secrets, provider data, historical reports and failed Work
notifications are untouched. The script sends no messages. A future authorized
scheduled task must still verify actual Work delivery and authenticated mobile
HTML access; synthetic formatter checks do not prove provider delivery.
