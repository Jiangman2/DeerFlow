# Skill 2.1.8: readable final artifacts after Gateway publication

The operator's `log(35).txt` records real 2.1.7 sender invocations returning
`WEBHOOK_LOCAL_FAILURE / PermissionError`. The original request and request file
have the same supported webhook target hash. Build accepted four artifacts and
`present_files` returned success; the ordinary run ended normally. The failure
is not evidence that group webhook capability was removed or the Gateway
concurrency rollout interrupted this run.

`log(37).txt` resolves the two sender workspace literals: one real accepted
2.1.7 project, and one nonexistent path. The real project's RESULT and request
are UID/GID 1000; its delivery directory is also UID/GID 1000. This rules out
the initial non-owner request/directory-chmod hypothesis for that project.
The privileged inspector verifies all four published-copy hashes. It does not
measure each copy's mode or demonstrate access by the ordinary command user.

The verified 19,640-byte Gateway `present_file_tool.py` uses `shutil.copy2`.
Unchanged `common.save` writes JSON through a 0600 `mkstemp`. Root publication
therefore preserves 0600 on that JSON while changing the copied file's owner.
The prior sender tests used `copyfile`, which did not preserve that restrictive
mode and missed this boundary. Actual Linux UID switching reproduces the
publication failure without HTTP. The same root-copy test with 2.1.8 succeeds
with a mocked sender, and a second call verifies no duplicate send. An
unreadable legacy copy remains blocked, now with its phase and `.json` type.

Only the four accepted final report artifacts get 0644 at generation/reuse.
Private project state, caches, raw source evidence and webhook configuration
retain their private modes. Browser authentication, thread ownership and
artifact read authorization remain enforced by Gateway; this release does not
modify Gateway, Nginx, Provisioner, schedules, credentials or notification
bindings. It does not copy artifacts to bypass publication, change the webhook
payload, replay a run, send a test message or relax hash/current-thread checks.
The five acquisition/render/runtime sources remain byte-identical to 2.1.7.

The sender keeps its journal location and send-once semantics. Permission
failures name a controlled stage and artifact type without paths or tokens.
A failure after an outbound attempt is distinguished from a pre-send failure;
the existing durable SENDING record still blocks automatic resubmission.

The new standalone installer binds the exact accepted 2.1.7 install receipt and
110-file live tree, stages a private backup, reuses the public browser runtime,
and requires 62 sealed offline checks plus UID1000 ARM64 fixed/freight rendering
and the actual final/private mode assertions before switching this Skill.
It accepts only the recorded Gateway 419 spec, supported by the 419 config-only
patch receipt and unchanged context source qualification. It does not claim
the rolling 70-Pod fleet has converged. The obsolete 108-file visibility check
inside the old installer is corrected to 110 here.

Validation: 23 focused sender/artifact tests passed on Windows and Linux UID1000;
the exact packaged 62-check suite and delivery composition passed on Linux
UID1000; 12 installer boundaries passed on Windows/Linux; the actual sealed
host loads all assets and reads all three exact `/var/lib/...` receipt paths
on Python 3.7 before zero-query wrong-host refusal. The three actual mixed-owner
root-copy cases also passed in the cached production ARM64 Gateway image.
Those local checks use only synthetic reports and mocked transport. Production
installation, production mode verification and a real group delivery remain
pending operator execution and subsequent business acceptance.

```bash
sudo python3 -I -B -u /home/cnooc/install-energy-publication-fix-20261116-r1.py --install-publication-fix-once
```

Run once; preserve failure output and markers. Do not rerun a previous
installation/activation, rerun the failed report or send a manual webhook test.
