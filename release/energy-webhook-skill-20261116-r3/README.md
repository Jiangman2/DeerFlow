# r3 installer-only POSIX-path correction

The operator's r2 attempt `cdf8c9bb0f0e48f98afc3db90b1da23c` stopped with
`FileNotFoundError` before once-claim or staging. Its output reports one read-only
Kubernetes command, no Skill switch and no Gateway/Provisioner mutation.

The Windows build serialized `str(Path('/var/lib/.../REPORT.json'))` into a
backslash path. That string is a relative filename on Linux. The previous test
normalized the value with Windows `Path.as_posix()` and mocked the reader without
checking its argument, hiding this packaging defect.

r3 renders an explicit POSIX literal, rejects backslash filesystem literals,
adds a specific missing-receipt error and names the preflight stage. It uses a
fresh r3 control/backup directory. No old receipt, failed release directory or
once marker is deleted or rewritten. The 2.1.7 code ZIP, verifier, fixtures and
57 Skill regressions are exactly unchanged from r2. Gateway remains read-only.

Validation checks the actual unmodified sealed receipt path in an isolated Linux
container: the accepted fixture is created at that exact `/var/lib/...` path and
the original reader loads and validates it. The wrong-host entry then refuses
without Kubernetes commands. This exercises the filesystem path rather than a
path-ignoring mock. Both Windows/Linux installer tests retain prior boundaries.

```bash
sudo python3 -I -B -u /home/cnooc/install-energy-webhook-skill-20261116-r3.py --install-webhook-skill-once
```

Do not rerun r2. This update stages/backs up only the Skill and qualifies actual
UID1000 rendering before switching it. It sends no real bot message and does not
rerun a report or mutate a schedule. Production installation, real PNG
qualification and subsequent business webhook delivery remain pending.
