# GLM mode and vision audit

The supplied GLM-5.2 documentation and stream/nonstream examples explicitly use
`extra_body={"thinking":{"type":"disabled"}}`; the SDK flattens this into the
HTTP JSON body. Enabled mode permits omitted, high or max reasoning effort.
Disabled mode should return no reasoning content and should omit effort.

The deployed integration is GLM-5.3-Flash / FCGLM-001, a different endpoint.
The old profile sets `thinking_always_on: true`, and the adapter formerly rewrote
every request to enabled. Both must change to offer a working flash selector.
The adapter now preserves explicit enabled/disabled, defaults to enabled,
rejects invalid modes, and removes effort in flash mode. The original fixed
profile remains intact for deployments not yet qualified. The opt-in switchable
profile supplies explicit factory overrides for both modes; changing only the
frontend or hiding reasoning text is insufficient.

The existing profile already advertises `supports_vision: true`. The adapter
accepts one uploaded base64 image per request, moves the image before text, and
preserves tool-call/reasoning replay. The operator's 2026-10-19 canary reported
image PASS. This is historical one-image acceptance, not new multi-image support.
GLM-5.2's supplied documentation does not describe image input. GLM-OCR uses its
own `/ocr` endpoint and data/data_type/text payload; keep it separate.

Package `diagnose-glm-modes.py` with the candidate adapter, probe and switchable
profile using `package-glm-modes.py`. Run the artifact on datax-01 with Python
`-I -B -u` and `--diagnose-glm-modes-once`. It samples one ready Gateway, uses its
existing key only inside a short-lived process, checks the candidate factory's
mode payloads and attempts at most five synthetic calls: flash, thinking, flash
tool selection, flash tool-result continuation, one image in thinking mode.
It checks absence of reasoning in flash and presence in the thinking text call.
It does not execute business tools, change configuration, write Secrets or
restart Gateway. The `.once` marker and private report preserve uncertain calls;
do not delete them to repeat a failed or interrupted probe.

Passing this diagnostic permits preparing an adapter/config release, not claiming
production completion. Bind the actual current Gateway baseline and preserve
other operators' changes. Then check `/api/models`, both modes and image upload
through the product. The generic selector needs no frontend rebuild.
