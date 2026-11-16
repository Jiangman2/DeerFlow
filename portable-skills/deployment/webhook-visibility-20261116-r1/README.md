# Read-only completion of the already installed 2.1.7 Skill

Operator receipt `1ed48e84ae104d2abd3645c01221cf34` confirms the 110-file
Skill switch, all 57 tests, both actual UID1000 ARM64 PNG cases, and unchanged
Gateway/Frontend/Provisioner specs. The post-publication visibility probe still
required 108 files and therefore reported `ORDINARY_SKILL_TREE_MISMATCH` even if
the new hash matched. This is a checker packaging defect, not evidence by itself
of a stale mount. The actual consumer view still needs observation.

This tool does not reinstall. It binds the exact 9,782-byte original server
receipt and expected publication inventory, corrects the count to 110, reports
actual hashes/differences, and samples at most three distinct nodes (prioritizing
the originally flagged Pod/node if still present). The selected Pods must be
ready with the existing ordinary public PVC mount. Reads run as UID1000 and
check stable Pod identity, the exact Skill tree and browser manifest/readability.

It executes at most ten fixed read-only Kubernetes commands. No Pod is created,
no file/config/receipt is changed, no browser or supplier call starts, and no
message or report is retried. It does not verify real webhook delivery or
authenticated public HTML access.

```bash
sudo python3 -I -B -u /home/cnooc/verify-energy-webhook-skill-20261116-r1.py --verify-readonly
```

Do not rerun the installer or delete any release directory/once marker.
