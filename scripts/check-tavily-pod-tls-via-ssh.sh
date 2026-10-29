#!/usr/bin/env bash
# Run on the development machine. Only this local checkout needs the Python probe.
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: bash scripts/check-tavily-pod-tls-via-ssh.sh SSH_TARGET POD [options]

  SSH_TARGET          SSH user@host or an SSH config alias for the online server
  POD                 Exact Gateway Pod name; no automatic Pod selection
  --namespace NAME    Default: deer-flow
  --container NAME    Default: gateway
  --sudo-kubectl      Run sudo -n kubectl on the server (requires existing permission)
  --output FILE       Local result file; default: tavily-pod-tls-UTC_TIMESTAMP.txt

The online server needs kubectl and cluster access, but no repository or Python.
The probe is streamed through SSH into the Pod's existing Python environment.
No deployment changes, API keys, or search requests are made.
USAGE
}

if [[ ${1:-} == --help || ${1:-} == -h ]]; then
  usage
  exit 0
fi
if (( $# < 2 )); then
  usage >&2
  exit 2
fi
ssh_target=$1
pod=$2
shift 2
namespace=deer-flow
container=gateway
sudo_kubectl=false
output="tavily-pod-tls-$(date -u +%Y%m%dT%H%M%SZ).txt"
while (( $# )); do
  case $1 in
    --namespace|--container|--output)
      if (( $# < 2 )) || [[ -z $2 ]]; then
        printf 'Missing value for %s\n' "$1" >&2
        exit 2
      fi
      case $1 in
        --namespace) namespace=$2 ;;
        --container) container=$2 ;;
        --output) output=$2 ;;
      esac
      shift 2
      ;;
    --sudo-kubectl) sudo_kubectl=true; shift ;;
    *) printf 'Unknown option: %s\n' "$1" >&2; exit 2 ;;
  esac
done

# Remote command arguments are restricted to Kubernetes names, never shell text.
for name in "$pod" "$namespace" "$container"; do
  if [[ ! $name =~ ^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$ ]]; then
    printf 'Invalid Kubernetes name: %s\n' "$name" >&2
    exit 2
  fi
done
if [[ -z $ssh_target || $ssh_target == -* || $ssh_target =~ [[:space:]] ]]; then
  printf 'Invalid SSH target\n' >&2
  exit 2
fi
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
probe="$script_dir/inspect-tavily-pod-tls.py"
if [[ ! -r $probe ]]; then
  printf 'Cannot read local probe: %s\n' "$probe" >&2
  exit 2
fi
command -v ssh >/dev/null
command -v tee >/dev/null
kubectl_command=kubectl
if $sudo_kubectl; then
  kubectl_command='sudo -n kubectl'
fi
remote_command="exec $kubectl_command -n $namespace exec -i $pod -c $container -- /app/backend/.venv/bin/python -u -"
printf 'Inspecting %s/%s via %s; local result: %s\n' "$namespace" "$pod" "$ssh_target" "$output"
# No SSH/Kubernetes TTY: preserve stdin as the complete Python source.
# Do not merge remote stderr into the saved, redacted diagnostic report.
ssh -T -o ConnectTimeout=10 -o ServerAliveInterval=15 -o ServerAliveCountMax=3 \
  "$ssh_target" "$remote_command" < "$probe" | tee -- "$output"
printf 'Inspection finished; read TLS results in %s\n' "$output"
