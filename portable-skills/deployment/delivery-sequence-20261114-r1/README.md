# Fixed conversation delivery sequence inspection

This is an operator-run read-only diagnostic for conversation
`8e27d29f-730b-4a89-9681-36fdddc87887`. It does not repair, retry, publish,
send messages, create workloads, or change task budgets.

The sealed standalone entry is
`release/energy-delivery-sequence-inspection-20261114-r1/inspect-energy-delivery-sequence-20261114-r1.py`.
Run only on datax-01 as root:

```bash
sudo python3 -I -B -u /home/cnooc/inspect-energy-delivery-sequence-20261114-r1.py --inspect-readonly
```

## Scope and evidence

- Reads current Gateway identity and one ready owned Pod, verifies ReplicaSet
  ownership, and rereads Pod and Deployment identities after inspection. The
  known Deployment UID is required; no old generation or image is assumed.
- Hashes only three fixed Gateway files: sandbox tools, present_file_tool,
  and conversation_url. File contents and environment values are not exported.
- Uses a read-only repeatable-read database transaction for the named
  conversation. Selects the latest run from two bounded metadata rows, then
  its first eight and last 64 AI/tool/error events. The event content budget is
  512 KiB, with at most 32 KiB per full event. Large events expose only bounded
  fragments to the classifier; raw content never leaves the diagnostic.
- Reports ordered proposals and paired tool receipts for Skill build, accept,
  final-text, present_files, projected host receipt writes, and HTTP POST
  candidates. Reports whether final-text supplied --publication-receipt and
  whether observable text contains the exact current conversation URL, the
  requested link label, or an unconfirmed-publication/link message.
- A proposed send, paired tool result, HTTP 200, provider code, file publication
  receipt, and final AI message remain separate evidence. Dynamic payloads and
  omitted event windows are not treated as proof of absence or delivery.

Configuration loading may resolve existing database credentials for the
read-only database connection. The program never exports credentials or reads
the Kubernetes Secret API. It does not load the application to hash source
files, start models, inspect another conversation, read news bodies for output,
or call the provider or Work. The child audit hook blocks writes, child
processes, locks, and non-database networking. Temporary host command spools are
closed and cleaned; no durable report is written.

## Local verification

`test_sequence.py` reruns the historical diagnostic safety fixtures against the
new child without editing the historical suite, plus sequence-specific tests.
27 tests passed under Windows Python 3.12 and Linux Python 3.10 / UID 1000.
Actual Python 3.7 compiled the sealed host and verified embedded child and
refused execution outside datax-01/root with zero queries. Containers had no
network, a read-only repository, and a temporary writable /tmp.

No production execution or business acceptance is claimed by these tests.
