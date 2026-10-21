# GLM-5.3-Flash contract audit (2026-10-21)

Authority: operator-supplied GLM-5.3-Flash interface document and its four Python
examples. This supersedes the provisional GLM-5.2 comparison.

| Area | Contract | Current implementation |
| --- | --- | --- |
| Endpoint/model | FCGLM-001/v1/chat/completions; GLM-5.3-Flash | Matches profile and adapter |
| Flash mode | Section 6.2 explicitly says thinking cannot be disabled | thinking_always_on true and forced enabled are correct |
| Effort | Omitted, high or max | Adapter supports these; generic low/medium/high selector disabled, normal requests use provider default |
| Vision | One base64 image, before text | supports_vision true; adapter reorders and rejects multiple images before HTTP |
| Tool continuation | Same model, assistant tool call and matching tool result ID | Reasoning and tool-call IDs preserved; October 19 field probe passed |
| Streaming | Separate reasoning/content; empty choices and errors | Shared adapter handles these; wire fixtures cover final text and usage |
| Time limits | Nonstream upstream roughly 300s ceiling; streamed requests need total bound | Streaming default; 120s chunk timeout and 600s async total deadline |

The operator's 2026-10-19 report recorded text, tool call, tool result and image
PASS. This is historical provider acceptance, not a fresh October 21 runtime
inventory or proof of every image/long-running request. Image support is distinct
from the independent GLM-OCR endpoint and limited to one image across request
history.

## Example inconsistencies

Section 11.4 resolves both: the nonstream example's disabled-thinking label is
historical and does not disable thinking; the function-call Python example's
second-turn GLM-5.2 is a copy/paste mismatch. The corrected document and our
integration keep GLM-5.3-Flash in both turns. Do not copy these example bugs.

## Stream acceptance limitation

The SDK consumes [DONE] internally. The current adapter does not independently
verify that raw marker; its frame guard rejects malformed-frame streams lacking
a terminal chunk, but is not a general clean-EOF truncation detector. Therefore
do not claim full compliance with section 6.5's strict raw [DONE] requirement
solely from chunk-level tests. A transport-level termination guard requires
separate implementation and qualification. The release canary does require a
valid finish reason and rejects length-truncated results.

## Withdrawn candidate

The switchable profile, five-call mode diagnostic and adapter change prepared
from the 5.2 comparison were withdrawn after reading 5.3 section 6.2. They were
never deployed. The original fixed-thinking adapter and its regression tests
are restored. The local unreleased diagnostic download is replaced by an inert
withdrawal notice. No frontend rebuild or production Gateway change is needed
merely to correct the interpretation of flash support.

DeepSeek-V4.1's explicitly requested synthetic 503 retry is independent and
retains its existing request format and five-call acceptance boundary.
