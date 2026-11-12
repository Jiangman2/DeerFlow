# Model-selected DataLake routing

The current conversation model selects between the two existing active tools:
`datalake_data_qa` for master data, production, metrics, attributes and details;
`datalake_smart_qa` for narrative document evidence. There is no separate
classifier model request and no regex/keyword eligibility gate.

When the trusted run context has `datalake_enabled is True`, both configured
active tools remain eligible for every phrasing. “KL6-1-2井的套管程序是什么”,
“KL6-1-2井的套管程序是多少” and elliptical follow-ups do not need magic words.
Tool availability is not a mandate to query: the model decides from the full
conversation whether to retrieve, answer generally, process uploaded files,
or ask for genuinely missing information. Tool examples are not a whitelist.

The source scope now records actual model-selected DataLake calls, including
mixed parallel batches. Once selected, external tools/bash are blocked for the
rest of that human turn; `process_local_data` remains available for local work.
Empty/error results do not release that boundary. Same-run compacted state keeps
it, and trusted delegated `datalake_query_only` restrictions remain enforced.
A new human turn starts a fresh model choice; linguistic follow-up rules no
longer inherit scope. Legacy keyword checkpoints are ignored. Before a DataLake
call is selected, enterprise-source choice is prompt guidance, not a semantic
hard gate. No claim is made that the model will always classify intent correctly.

Authorization is still server-owned and checked at execution. False, missing,
string and numeric values do not grant access. Retired tools remain hidden.
The model must distinguish disabled access, missing configuration, empty data,
upstream errors and timeouts instead of always asking the user to re-enable
access. Exact facts must be supported by returned records, not inferred to fill
a missing tool response. No endpoint, key, tool registration or global Bash
permission is changed.

## Current-run authorization clarity (November 12 candidate)

Every model invocation receives a `current_datalake_access` system section built
from the server-validated runtime flag and the filtered tool list. This section
supersedes historical permission claims; user confirmation and query outcomes
never grant or revoke access. Authorized-but-missing tools are configuration
failures, not a reason to ask the user to enable the button again.

Framework-injected memory messages receive a historical-status notice in the
model request copy only. Stored messages and business context are unchanged;
no keyword-based deletion or destructive memory migration is performed. The
memory updater is instructed to omit transient authorization/tool availability
and remove those assertions from existing summaries when updating them. This
is model guidance, not a guarantee of perfect semantic memory extraction.

`DATALAKE_ACCESS` logs the run ID, requested/effective flags and a bounded reason
code; `DATALAKE_MODEL_ACCESS` logs the run ID, effective flag and active tool names.
Neither record includes cookies, tokens, questions, memory or returned data.
The frontend contract is unchanged. These logs distinguish an authorization
transport failure from model misinterpretation without exposing credentials.

Regression tests cover false/true/false transitions, repeated identical questions,
four mode contexts, old memory, missing tools, unsuccessful receipts and a real
checkpointed synthetic graph. Live model behavior and production acceptance
remain to be verified after deployment; synthetic replies do not prove them.

Tests verify visibility, sync/async graph execution, denied access, same-batch
fallback prevention, compaction and task switching with synthetic tools. They
do not prove live model routing or enterprise answer accuracy. After image QA
and deployment, validate both casing-program phrasings, contextual follow-ups,
ordinary writing/uploaded files and the disabled-access case with the real model.
