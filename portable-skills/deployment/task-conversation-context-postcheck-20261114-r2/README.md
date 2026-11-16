# Gateway task-context read-only postcheck r2

The original r1 activation recorded one successful patch request and stopped at
`GATEWAY_SPEC_DRIFT`. The operator's subsequent read-only observation proves the
only difference is `/template/spec/volumes/9/configMap/defaultMode = 420` on the
new ConfigMap volume. r2 adds that exact value to the expected spec in memory,
binds the complete observed spec hash, and reuses the original sealed operator's
full fleet/source/configuration/endpoint verification. It does not repeat the
patch or create any Kubernetes object.

The unsealed template retains empty parent and field-proof assets. The sealer
requires the actual operator observation and passing local tests, then writes a
new release. Original r1 source, server PLAN, REPORT, journals and configuration
remain unchanged. The recorded field input is a normalized transcription of the
operator's JSON, not a byte-for-byte server receipt. `SOURCE.field_input_sha256`
binds that file including its newline; `FIELD-DEFAULTING.field_report_sha256`
binds canonical JSON without whitespace.

`--verify-readonly` writes nothing. `--verify-and-save-receipt` also performs
only cluster reads and saves a new root-owned local receipt after all checks
pass:

`/var/lib/portal-task-conversation-context-postcheck-20261114-r2/RUNTIME-ACCEPTANCE.json`

Both modes return immediately if rollout is incomplete. Neither waits for or
restarts rollout, retries activation, sends messages, reads credential values,
or calls market providers. Repeating these observation modes is safe.

At first receipt creation, the printed JSON and canonical saved receipt differ
only by the printed `runtime_acceptance_receipt` metadata. Remove that key and
serialize with sorted keys, compact separators, ASCII escaping and no trailing
newline to reconstruct the saved bytes and verify the printed size/hash.
Counters and observation time are finalized before saving and are not changed
by the normal finalizer. The metadata explicitly marks
`stdout_without_receipt_metadata_matches_saved=true`.

A later complete observation preserves the existing receipt and sets
`existing_acceptance_reused=true` and
`stdout_without_receipt_metadata_matches_saved=false`. Its new printed
observation must not be presented as a reconstruction of the older file.

Validation: 11 synthetic boundary tests cover precise defaulting scope,
immutable old evidence, mutation refusal, convergence entry, save-path isolation,
first-save reconstruction and repeated-save preservation. Cached Python 3.7.17
validated parent asset loading without invoking its main function, unbound
proof refusal, tamper refusal, and both sealed wrong-host entry modes with zero
commands. These tests establish local tool behavior, not production acceptance.
