# Migration evidence only

Bind the successful cross-node receipt, but do not import previous mutators.
datax-01 uses bounded kubectl GET only. datax-03 uses bounded metadata-only
directory traversal without symlink/mount following or business-file reads.
No mount, copy, chmod, chown, rsync server, install, signal, SQL, model call or
production change. Only private diagnostic receipts and the launcher log are
written. Never print business filenames, file contents, credentials or raw API
responses. File counts are not a point-in-time snapshot while ingestion runs.

Enumerate declared Kubernetes storage consumers, including inactive controller
templates and hostPath/PV aliases. This cannot prove actual write activity or
exclude non-Kubernetes writers; never call this a quiescence gate. Truncated
scans and denied reads must stay incomplete, not zero. No scaling/copy approval.
Ship a new immutable delivery after offline Linux fixture tests, Python3.7
grammar checks and launcher validation. Keep all older packages unchanged.
