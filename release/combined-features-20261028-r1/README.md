# Accepted combined release, October 28–29

Current state: Gateway 389 and Frontend 231 are runtime-accepted. V4.1 remains
disabled; Work is excluded. See
`../../docs/production-source-reconciliation-20261029.md` for the source mapping,
checks and pending business acceptance.

Read the evidence chronologically: SOURCE/build → preload → installed and
cross-node validation → scope decision → partial activation → diagnosis → final
resume. `RUNTIME-ACCEPTED-FIELD-20261029-r1.json` is the final operator summary.
Earlier candidate/not-deployed fields remain unchanged as historical evidence.

`qualification/TEST-BUNDLE.json` preserves the exact synthetic test bundle used
by both installed-image jobs. It contains synthetic credentials only. The V4.1
profile is an unactivated test fixture and is not a production model config.

`historical-tooling/` contains verbatim build/validation/activation sources and
their hashes for audit. Their original paths were `deploy/enterprise/`; they
were bound to specific historical local paths and cluster identities. Do not
run them from this archive or reapply a completed production rollout. Supporting
common build helpers remain tracked at their original repository paths. A future
release requires a new source seal, current cluster evidence and new tools.

`source-line-endings/` preserves three original CRLF build inputs. Git's LF copies
are verified by exact newline conversion, not by a broad formatting exception.
No production secrets, user documents, model responses or environment dumps are
included.
