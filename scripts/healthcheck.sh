#!/usr/bin/env bash
# MLOps cluster healthcheck — короткий вывод для человека и для AI-чата.
# Usage:
#   ./scripts/healthcheck.sh          # полный чек
#   ./scripts/healthcheck.sh --brief  # одна строка OK/FAIL + счётчики
#   ./scripts/healthcheck.sh --recover  # unseal Vault + ESO force-sync (нужен ключ)
set -euo pipefail

BRIEF=0
RECOVER=0
VAULT_KEYS="${VAULT_KEYS:-$HOME/.config/vault-lab-keys.json}"
NODE_IP="${NODE_IP:-192.168.3.248}"

for arg in "$@"; do
  case "$arg" in
    --brief) BRIEF=1 ;;
    --recover) RECOVER=1 ;;
    -h|--help)
      sed -n '2,7p' "$0"
      exit 0
      ;;
    *) echo "unknown arg: $arg" >&2; exit 2 ;;
  esac
done

fail=0
note() { printf '%s\n' "$*"; }
ok()   { printf 'OK   %s\n' "$*"; }
warn() { printf 'WARN %s\n' "$*"; }
bad()  { printf 'FAIL %s\n' "$*"; fail=1; }

if ! kubectl cluster-info >/dev/null 2>&1; then
  bad "kubectl: cluster unreachable"
  exit 1
fi

# --- Vault ---
vault_sealed="unknown"
if kubectl get pod -n vault vault-0 >/dev/null 2>&1; then
  vault_sealed=$(kubectl exec -n vault vault-0 -- vault status 2>/dev/null | awk '/Sealed/ {print $2}' || echo "error")
  if [[ "$vault_sealed" == "true" ]]; then
    bad "Vault sealed"
    if [[ "$RECOVER" -eq 1 && -f "$VAULT_KEYS" ]]; then
      unseal=$(python3 -c "import json; print(json.load(open('$VAULT_KEYS'))['unseal_keys_b64'][0])")
      kubectl exec -n vault vault-0 -- vault operator unseal "$unseal" >/dev/null
      vault_sealed=$(kubectl exec -n vault vault-0 -- vault status 2>/dev/null | awk '/Sealed/ {print $2}')
      [[ "$vault_sealed" == "false" ]] && ok "Vault unsealed" || bad "Vault unseal failed"
    fi
  else
    ok "Vault unsealed"
  fi
else
  bad "Vault pod not found"
fi

# --- ESO ---
if [[ "$RECOVER" -eq 1 && "$vault_sealed" == "false" ]]; then
  kubectl annotate clustersecretstore vault "force-sync=$(date +%s)" --overwrite >/dev/null 2>&1 || true
fi
eso_bad=$(kubectl get externalsecret -A --no-headers 2>/dev/null | awk '$7 != "True" {c++} END {print c+0}')
if [[ "${eso_bad:-0}" -eq 0 ]]; then
  ok "ESO: all ExternalSecrets synced"
else
  bad "ESO: $eso_bad ExternalSecret(s) not ready"
  if [[ "$RECOVER" -eq 1 ]]; then
    while read -r ns name; do
      kubectl annotate externalsecret -n "$ns" "$name" "force-sync=$(date +%s)" --overwrite >/dev/null 2>&1 || true
    done < <(kubectl get externalsecret -A --no-headers | awk '$7 != "True" {print $1, $2}')
    sleep 5
    eso_bad=$(kubectl get externalsecret -A --no-headers | awk '$7 != "True" {c++} END {print c+0}')
    [[ "${eso_bad:-0}" -eq 0 ]] && ok "ESO recovered" || bad "ESO still $eso_bad not ready"
  fi
fi

# --- GPU ---
gpu_alloc=$(kubectl get node -o jsonpath='{.items[0].status.allocatable.nvidia\.com/gpu}' 2>/dev/null || echo "0")
gpu_plugin=$(kubectl get pods -n kube-system --no-headers 2>/dev/null | grep nvidia-device-plugin | awk '{print $3}' | head -1)
if [[ "${gpu_alloc:-0}" != "0" && "$gpu_plugin" == "Running" ]]; then
  ok "GPU: allocatable=$gpu_alloc, plugin=$gpu_plugin"
else
  bad "GPU: allocatable=${gpu_alloc:-0}, plugin=${gpu_plugin:-missing}"
fi

# --- Triton ---
triton_ready=$(kubectl get deploy -n mlops-serving triton -o jsonpath='{.status.readyReplicas}' 2>/dev/null || echo "0")
triton_code=$(curl -s -o /dev/null -w '%{http_code}' -H 'Host: triton.mlops.local' "http://${NODE_IP}/v2/health/ready" 2>/dev/null || echo "000")
if [[ "${triton_ready:-0}" -ge 1 && "$triton_code" == "200" ]]; then
  ok "Triton: deploy ready + HTTP 200"
else
  bad "Triton: readyReplicas=${triton_ready:-0}, health=$triton_code"
fi

# --- Kafka ---
kafka_ready=$(kubectl get kafka -n kafka kafka-cluster -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}' 2>/dev/null || echo "")
if [[ "$kafka_ready" == "True" ]]; then
  ok "Kafka: Ready"
else
  bad "Kafka: not Ready"
fi

# --- Bad pods ---
bad_pods=$(kubectl get pods -A --no-headers 2>/dev/null | { grep -vE 'Running|Completed' || true; } | wc -l | tr -d ' ')
if [[ "${bad_pods:-0}" -eq 0 ]]; then
  ok "Pods: no non-Running/Completed"
else
  bad "Pods: $bad_pods not healthy"
  if [[ "$BRIEF" -eq 0 ]]; then
    kubectl get pods -A --no-headers | { grep -vE 'Running|Completed' || true; } | head -10
  fi
fi

# --- Argo OutOfSync (warn only) ---
argo_os=$(kubectl get applications -n argocd --no-headers 2>/dev/null | awk '$2=="OutOfSync" {c++} END {print c+0}')
[[ "${argo_os:-0}" -gt 0 ]] && note "INFO Argo: $argo_os app(s) OutOfSync (не блокер)"

# --- RAM ---
mem_pct=$(kubectl top node --no-headers 2>/dev/null | awk '{print $5}' | tr -d '%' || echo "?")
[[ "$mem_pct" != "?" && "$mem_pct" -gt 85 ]] && note "INFO Node memory ${mem_pct}% (высокая загрузка)"

if [[ "$BRIEF" -eq 1 ]]; then
  if [[ "$fail" -eq 0 ]]; then
    echo "OK | gpu=$gpu_alloc triton=$triton_code eso_bad=$eso_bad bad_pods=$bad_pods"
  else
    echo "FAIL | gpu=$gpu_alloc triton=$triton_code eso_bad=$eso_bad bad_pods=$bad_pods vault_sealed=$vault_sealed"
  fi
  exit "$fail"
fi

note "---"
[[ "$fail" -eq 0 ]] && note "SUMMARY: OK" || note "SUMMARY: FAIL ($fail issue(s))"
exit "$fail"
