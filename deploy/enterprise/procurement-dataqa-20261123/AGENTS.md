# Markdown delivery update

This package changes `app/gateway/procurement_review/markdown_report.py`
and four Data QA-specific environment entries in one Deployment patch.
The existing function signature is retained for the installed service.
Do not validate report sections, input-name mentions, review rows, risk levels,
or compliance conclusions. Do not rewrite the Markdown or infer a conclusion.
Keep bounded nonempty UTF-8 text eligibility, the service's existing original-run
and owner/path checks, and snapshot/SSE integrity. An API success means delivery
success, not procurement compliance. Do not alter legacy DOCX handling.

The update creates an immutable ConfigMap and atomically replaces only this
file mount. Preserve every other mount, image, unrelated environment variable and database
schema. Existing failed job payloads are not rewritten or relaunched.

Data QA uses app-jeg6ud59 over the existing AppForge POST/SSE transport. Pin the
installed adapter source before relying on its config precedence. Never hardcode
the user's sample conversation ID or credentials. A fresh immutable Secret is
created from hidden terminal input via stdin only. Patch only the four named env
entries; never copy unrelated inline credentials into patch command arguments.
Keep smart QA and data QA timeouts unchanged. Runtime configuration verification
does not constitute live upstream protocol acceptance.

R2 clones the currently mounted config ConfigMap, changes only the four Data QA connection fields (api_url, app_id, conversation_prefix, api_key environment reference), and switches only Gateway config.yaml to the new volume in the same Deployment patch. Validate YAML semantic equivalence outside those fields and AppConfig env resolution; preserve the old object and every other configuration value. Do not infer that a resolved api_key is an inline credential: it can originate from an environment placeholder.

R3 waits up to 300 seconds for preflight readiness instead of rejecting the first unhealthy snapshot. Retain the full replica threshold and Pod checks; never unpause or alter replicas. Pin Deployment UID/spec during waiting, stop on drift, preserve bounded status-only evidence.

R4 derives the Secret name from a SHA-256 record-path digest, never from the raw tempfile suffix. Validate lowercase DNS-label names before any create. Redact both raw and base64 credentials from private failure diagnostics.
