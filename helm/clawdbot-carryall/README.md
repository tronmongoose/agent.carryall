# Clawdbot + Carryall Helm Chart

Isolated Kubernetes deployment of Clawdbot with Carryall policy enforcement sidecar.

## Security Features

- **Network isolation**: Egress restricted to LLM API endpoints only
- **Read-only filesystem**: Containers run with immutable root
- **Non-root execution**: All containers run as UID 1000
- **No privilege escalation**: Capabilities dropped
- **Policy enforcement**: Carryall sidecar validates all data access
- **Audit logging**: All access decisions logged to persistent storage
- **Secrets management**: API keys injected via Kubernetes secrets

## Prerequisites

- Kubernetes 1.24+
- Helm 3.0+
- Network policy controller (Calico, Cilium, etc.)
- At least one LLM API key (Anthropic or OpenAI)

## Installation

### 1. Create namespace

```bash
kubectl create namespace clawdbot
```

### 2. Create a values file with your API key

```yaml
# my-values.yaml
secrets:
  anthropicApiKey: "sk-ant-..."
  # OR
  # openaiApiKey: "sk-..."

clawdbot:
  agent:
    model: "anthropic/claude-sonnet-4"  # or "openai/gpt-4o"
```

### 3. Install the chart

```bash
helm install clawdbot ./helm/clawdbot-carryall \
  -n clawdbot \
  -f my-values.yaml
```

### 4. Verify deployment

```bash
kubectl get pods -n clawdbot
kubectl logs -n clawdbot deployment/clawdbot-clawdbot-carryall -c clawdbot
```

## Usage

### Port-forward to the gateway

```bash
kubectl port-forward -n clawdbot svc/clawdbot-clawdbot-carryall 18789:18789
```

### Send a message to the agent

```bash
# From your local machine with clawdbot CLI
clawdbot agent --agent main --message "What skills do you have?"
```

### Check carryall audit logs

```bash
kubectl exec -n clawdbot deployment/clawdbot-clawdbot-carryall -c carryall -- \
  carryall audit query
```

## Configuration

| Parameter | Description | Default |
|-----------|-------------|---------|
| `clawdbot.version` | Clawdbot npm version | `2026.1.24-3` |
| `clawdbot.agent.model` | LLM model to use | `anthropic/claude-sonnet-4` |
| `clawdbot.gateway.port` | Gateway port | `18789` |
| `clawdbot.gateway.auth.enabled` | Enable gateway auth | `true` |
| `carryall.enabled` | Enable policy sidecar | `true` |
| `carryall.policy.defaultDeny` | Deny by default | `true` |
| `networkPolicy.enabled` | Enable network policies | `true` |
| `networkPolicy.denyAllEgress` | Block all egress by default | `true` |
| `persistence.audit.enabled` | Persist audit logs | `true` |
| `secrets.anthropicApiKey` | Anthropic API key | `""` |
| `secrets.openaiApiKey` | OpenAI API key | `""` |

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Kubernetes Pod                        │
│  ┌─────────────────┐      ┌─────────────────────────┐   │
│  │   Clawdbot      │      │      Carryall           │   │
│  │   Gateway       │◄────►│   Policy Sidecar        │   │
│  │   :18789        │      │   :8765                 │   │
│  └────────┬────────┘      └───────────┬─────────────┘   │
│           │                           │                  │
│           ▼                           ▼                  │
│  ┌─────────────────┐      ┌─────────────────────────┐   │
│  │  /home/clawdbot │      │  ~/.carryall/audit      │   │
│  │  (emptyDir)     │      │  (PersistentVolume)     │   │
│  └─────────────────┘      └─────────────────────────┘   │
└─────────────────────────────────────────────────────────┘
                    │
                    ▼ (NetworkPolicy: egress only to)
        ┌───────────────────────┐
        │  api.anthropic.com    │
        │  api.openai.com       │
        └───────────────────────┘
```

## Uninstall

```bash
helm uninstall clawdbot -n clawdbot
kubectl delete namespace clawdbot
```

## Security Considerations

1. **API keys**: Never commit values files with API keys. Use `--set secrets.anthropicApiKey=...` or external secrets manager.

2. **Network policies**: Ensure your cluster has a network policy controller installed.

3. **Egress filtering**: The default config only allows HTTPS to LLM APIs. Add additional `networkPolicy.allowedEgress` entries for SLOS or other backends.

4. **Audit retention**: Configure PVC size and backup strategy for audit logs.

5. **Gateway auth**: Enable `clawdbot.gateway.auth.enabled` in production.
