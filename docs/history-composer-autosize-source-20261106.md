# History composer autosize source qualification

This candidate changes the history composer to grow from one to six visual text
rows, then scroll internally. Deleting text or sending shrinks it again. Existing
welcome and sidecar composers retain their own layout. The measurement accounts
for wrapped text, viewport-width changes, unsupported CSS field sizing and a
classic scrollbar without leaving a stale extra line.

The five-file source registration is in
`release/history-composer-autosize-source-20261106/SOURCE.json`. Three runtime
files and two browser test files are bound to exact LF-normalized hashes. The
three existing files have immutable parent copies taken from enterprise merge
`47a4fb82b2fae402c7fc95e6e9e3e93ad6187d50` (PR #39). The other two files are
new and have no invented parent. PR #39's stream reconciliation and artifact
behavior retain their independent provenance records.

The registration is `NOT_DEPLOYED`. It is not a production image receipt or a
new browser/business acceptance record. The combined frontend package and its
operator acceptance are recorded separately; this source seal must not be
rewritten to manufacture deployment acceptance.

Local verification before sealing: 36 actual browser checks passed across
history, welcome and sidecar composers; targeted lint/formatting and the frontend
type check passed. These were synthetic local checks and made no model requests.

Historical verification first checks every candidate source, test and parent
copy, then projects only these scoped parent bytes. It hides the two new files
and this new archive from the historical read view. The October and November
inventories and production receipts are preserved byte-for-byte. Rejection tests
cover missing/changed files, parent drift, scope/path expansion, raw-parent
substitution, forged readers and false deployment claims.

Run with Python 3.12 or newer:

```text
python -B deploy/enterprise/test_history_composer_source_20261106.py
python -B deploy/enterprise/verify-history-composer-source-20261106.py
python -B deploy/enterprise/verify-nov02-released-source-20261103.py
python -B deploy/enterprise/verify-nov01-released-source-20261102.py
python -B deploy/enterprise/verify-production-baseline-20261029.py
```

The default commands verify the working tree. After committing, use `--ref HEAD`
for the verifier commands to verify the committed candidate. Before committing,
`--ref HEAD` verifies the unchanged parent, not these local candidate files.
