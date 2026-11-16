# Field-bound read-only postcheck

This release binds the operator's proven single ConfigMap defaulting difference
to the immutable r1 activation. See
`../../portable-skills/deployment/task-conversation-context-postcheck-20261114-r2/README.md`
for exact validation, mode and receipt semantics.

The sealed file is 307415 bytes, SHA256
`2981acbafeb57493e6b3ab8d70c6d37c2abd7e0d25f703be7bc031d28a65431c`.
No production invocation was performed during preparation. `--verify-readonly`
does not write files; `--verify-and-save-receipt` writes only the new root-owned
local acceptance after full checks. Neither mode modifies Kubernetes resources.
