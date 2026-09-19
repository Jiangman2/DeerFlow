# DeerFlow LLM Direct Transport R1

This package fixes production DeerFlow conversations that fail with
`openai.APIConnectionError: Connection error` while the operator-owned raw
model probe succeeds.

The production diagnosis proved that the real `RobustChatOpenAI` async client
inherits `HTTP_PROXY` / `HTTPS_PROXY` and fails in `httpcore`'s HTTP proxy
transport. The same factory client succeeds on both Gateway replicas when the
exact model API hostname is added to `NO_PROXY` and `no_proxy`.

## Scope

- Appends only `aiapi.cnooc.com.cn` to the effective Gateway `NO_PROXY` and
  `no_proxy` values.
- Preserves the R11 Gateway image and two-replica rolling-update strategy.
- Does not mutate a database, model/API-key configuration, WeKnora,
  `ppt-master-runner`, Frontend, Nginx, or existing Sandboxes.
- Repeats plain-stream and tool-bound requests through DeerFlow's real model
  factory on both Gateway replicas.
- Captures exact prior environment entries and automatically restores them if
  rollout validation fails.

The rollout restarts the two Gateway Pods one at a time. It therefore requires
zero active DeerFlow runs and zero active OA jobs.

## Preflight

```bash
cd /opt/deerflow-hotfixes/deerflow-llm-direct-transport-20260919-r1
export KUBECONFIG=/etc/kubernetes/admin.conf
unset SSL_CERT_FILE REQUESTS_CA_BUNDLE CURL_CA_BUNDLE
unset DEERFLOW_LLM_DIRECT_TRANSPORT_ACK
set -o pipefail

./run-deerflow-llm-direct-transport-datax-01.sh \
  --preflight-only 2>&1 | \
  tee /home/cnooc/deerflow-llm-direct-transport-r1-preflight.log

PREFLIGHT_RC="${PIPESTATUS[0]}"
echo "preflight_exit_code=${PREFLIGHT_RC}"
test "${PREFLIGHT_RC}" -eq 0
```

Proceed only when the output contains:

```text
temporary_direct_transport_factory_probe=PASS
deerflow_llm_direct_transport_preflight=PASS
maintenance_gate_ready=yes
```

## Rollout

```bash
cd /opt/deerflow-hotfixes/deerflow-llm-direct-transport-20260919-r1
export KUBECONFIG=/etc/kubernetes/admin.conf
unset SSL_CERT_FILE REQUESTS_CA_BUNDLE CURL_CA_BUNDLE
export DEERFLOW_LLM_DIRECT_TRANSPORT_ACK=YES
set -o pipefail
LOG=/home/cnooc/deerflow-llm-direct-transport-r1-rollout.log

./run-deerflow-llm-direct-transport-datax-01.sh \
  --rollout 2>&1 | tee "${LOG}"

ROLLOUT_RC="${PIPESTATUS[0]}"
unset DEERFLOW_LLM_DIRECT_TRANSPORT_ACK
echo "rollout_exit_code=${ROLLOUT_RC}"
echo "rollout_log=${LOG}"
test "${ROLLOUT_RC}" -eq 0
```

Success requires `deerflow_llm_direct_transport_cutover=PASS`. Save the printed
`rollback_state` path.

## Status

```bash
./status-deerflow-llm-direct-transport-datax-01.sh
```

## Manual rollback

Use only the exact state path printed by rollout:

```bash
./rollback-deerflow-llm-direct-transport-datax-01.sh \
  --state /opt/deerflow-hotfix-evidence/deerflow-llm-direct-transport-20260919-r1/<timestamp>/state.env
```
