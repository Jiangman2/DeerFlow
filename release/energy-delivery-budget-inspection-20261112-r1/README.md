# Read-only fixed-thread delivery and budget inspection

This operator inspects only thread `545097ca-bd47-4416-888e-b6579229c865` and one
owned Ready Gateway's non-secret runtime/configuration metadata. It sends no
message, does not replay a task, does not change a budget, and does not operate
Sandbox locks, Pod deletion or provider APIs. Run on datax-01 as root:

```bash
sudo python3 -I -B -u /home/cnooc/inspect-energy-daily-webhook-budget-20261112-r1.py --inspect-readonly
```

The deployment source, Pod identity and controller ownership are read before and
after inspection. Exactly seven Kubernetes reads/exec calls are permitted. Mount
metadata includes ConfigMap names/keys, paths and subPath/read-only flags; Secret
names and values and ConfigMap contents are not exported or requested separately.
The child loads the ordinary configured AppConfig once. That normal load may
resolve credentials; only whitelisted integer/boolean settings, fixed package
versions, configuration-path metadata and four fixed source hashes are returned.
These observations do not prove every process's loaded state or the exact
middleware graph used by an earlier run.

PostgreSQL uses read-only transactions and deadlines. At most two named-thread
run metadata rows are returned internally; the latest run's first eight and last
32 relevant events are sampled. At most 128 KiB of full contents or bounded
head/tail fragments is decoded. Oversized fragments cannot establish a complete
command, payload absence or execution. No checkpoint blob is read. Raw messages,
news, commands, Webhook URLs and credentials are not exported.

Static requests/httpx/curl POST candidates, actual tool receipts, file publication
and final AI text are separate evidence classes. The parser inspects only a
small pure JSON-shaped literal subset and never executes logged code. Dynamic
variables, outside payload files, unsupported serialization and non-POST curl
variants remain unknown. Literal payload representation size is not wire size;
HTTP 200 or an enclosing successful Bash alone does not establish delivery of an
embedded HTTP branch. Message-rendering semantics are not remotely tested.

The already-deployed link integration covers native Work/Feishu replies and
verified private-chat scheduled notifications. It does not rewrite arbitrary
task-owned group-robot Webhook sends. This package does not assume that is the
proven cause for this particular thread; it collects the scoped evidence needed
to distinguish construction/timing from rendering or transport issues.

Linux Python 3.10, UID 1000, network disabled, read-only bind and all capabilities
dropped: 38 checks passed (10 host, 19 child, nine runtime summary). Sealed parent
and combined child compilation, fingerprint checks, tamper rejection and the
zero-query wrong-host guard also passed with actual Python 3.7.17. No production
observation has yet been supplied for this package. Earlier sealed resources,
completed releases and operator receipts remain unchanged.
