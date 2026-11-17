# Exact personal notification inspection, November 16 r2

This read-only tool is bound to chat a5f059b0-1858-4687-8d16-2de8f853c418
and the operator-reported personal Work notice at 16:13 Asia/Shanghai.
It observes the actual current Gateway baseline rather than assuming the
previous rollout is still current. The new observer reads bounded, owner-bound
run, event, task, occurrence, connection-state and outbox metadata in a read-only
transaction. It exports reader-link facts and hashes, never raw messages,
webhook targets, notification ciphertext or credentials. It does not execute
unknown notification formatter source or read project files. It cannot replay
tasks, send messages or mutate workloads. It uses the existing private temporary
command transport and checks stable Pod and Deployment identities afterward.

Validation: 35 tests passed on Linux Python 3.10 / UID 1000; Windows Python 3.12
passed 34 with one Linux-only skip. Actual Python 3.7 verified the sealed child
resource and zero-query wrong-host refusal. Docker checks used no network.
These are local checks, not production evidence for the newly reported chat.

Copy the sealed release script to /home/cnooc/ on datax-01 and execute:

    sudo python3 -I -B -u /home/cnooc/inspect-energy-personal-notification-20261116-r2.py --inspect-readonly

No prior activation or scheduled task needs to be rerun.
