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

## Stream termination guard

The GLM adapter installs a response-local observer before OpenAI SDK 2.32.0
consumes SSE events. It requires a choice-zero terminal reason followed by the
exact `[DONE]` marker. Clean EOF, a partial marker, or `[DONE]` without a terminal
choice fails with `incomplete_glm_stream`. JSON content containing that text is
not a marker. The SDK still parses fragmented UTF-8, CRLF, comments and SSE data;
the observer stores only two booleans. It does not patch a shared client or
another provider. Unknown SDK stream shapes fail closed; SDK upgrades require
qualification of this private decoder seam, including structured-output wrappers.

Already streamed text can remain visible, but the error middleware produces a
terminal warning with no tool calls and no automatic retry. Caller cancellation
preserves cancellation and closes the upstream response. A terminal `length`
still reaches the existing truncation handling; `[DONE]` is transport completion,
not proof of task completion. The provider canary also rejects length truncation.

Offline raw-SSE tests cover sync/async, final text/usage, truncated tool calls,
structured output, cancellation and concurrent response isolation. These do not
constitute production acceptance. Package the standalone field canary with:

```sh
python deploy/enterprise/package-glm-stream-completion.py --output /path/to/verify-glm-stream.py
```

On datax-01, run `sudo python3 -I -B -u /home/cnooc/verify-glm-stream.py
--verify-glm-stream-once` as one command. The Python 3.7 controller checks a steady
Gateway, exact baseline source hashes and SDK version, then loads candidate
modules only in a short-lived process. It uses the existing environment key,
verified TLS and at most four synthetic requests (text, tool call, tool result,
single image); no business tools, Secret writes or Deployment updates. Preserve
its report and once marker after failure or uncertainty. A passing report is
candidate acceptance only; prepare the Gateway patch against the reported image
and freshly verify the rollout baseline before a separate update.

## Withdrawn candidate

The switchable profile, five-call mode diagnostic and adapter change prepared
from the 5.2 comparison were withdrawn after reading 5.3 section 6.2. They were
never deployed. The original fixed-thinking adapter and its regression tests
are restored. The local unreleased diagnostic download is replaced by an inert
withdrawal notice. No frontend rebuild or production Gateway change is needed
merely to correct the interpretation of flash support.

DeepSeek-V4.1's explicitly requested synthetic 503 retry is independent and
retains its existing request format and five-call acceptance boundary.
