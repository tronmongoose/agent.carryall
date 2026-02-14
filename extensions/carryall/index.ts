// Carryall Clawdbot Plugin
// Thin HTTP client — all auth logic stays in the carryall server.
// No secrets, keys, or envelope data are stored in this plugin.

interface PluginConfig {
  carryallUrl?: string;
  defaultLlmProvider?: string;
  defaultTtlSeconds?: number;
}

const plugin = {
  id: "carryall",
  name: "Carryall Authority Runtime",
  description: "Cryptographic permission envelopes for AI agents",

  register(api: any) {
    const cfg = (api.pluginConfig ?? {}) as PluginConfig;
    const baseUrl = cfg.carryallUrl || "http://localhost:8765";
    const defaultProvider = cfg.defaultLlmProvider || "openai";
    const defaultTtl = cfg.defaultTtlSeconds || 300;

    async function callCarryall(
      toolName: string,
      body: Record<string, unknown>,
    ) {
      const url = `${baseUrl}/tools/${toolName}`;
      let response: Response;
      try {
        response = await fetch(url, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
      } catch (err: any) {
        return {
          content: [
            {
              type: "text" as const,
              text: JSON.stringify({
                error: true,
                message: `Carryall unreachable at ${baseUrl}: ${err.message}`,
              }),
            },
          ],
        };
      }

      if (!response.ok) {
        const errorBody = await response.text();
        return {
          content: [
            {
              type: "text" as const,
              text: JSON.stringify({
                error: true,
                status: response.status,
                message: errorBody,
              }),
            },
          ],
        };
      }

      return response.json();
    }

    // ── Tool 1: carryall_compile_policy (primary) ──────────────
    api.registerTool({
      name: "carryall_compile_policy",
      label: "Compile Policy",
      description:
        "Compile natural language intent into a minimal permission envelope. " +
        "Translates requests like 'read the Q4 finance report' into the " +
        "fewest cryptographic scopes needed. Returns a signed envelope.",
      parameters: {
        type: "object",
        properties: {
          agent_id: {
            type: "string",
            description: "Agent requesting permissions",
          },
          intent: {
            type: "string",
            description:
              "Natural language description of what the agent needs to do",
          },
          available_scopes: {
            type: "array",
            items: { type: "string" },
            description:
              "Scopes the agent may request (e.g. vault:finance:read)",
          },
          available_resources: {
            type: "array",
            items: { type: "string" },
            description:
              "Resource patterns the agent can access (e.g. slos://vaults/finance/*)",
          },
          ttl_seconds: {
            type: "integer",
            description: "Envelope lifetime in seconds (default: 300)",
            minimum: 60,
            maximum: 86400,
          },
          llm_provider: {
            type: "string",
            enum: ["openai", "anthropic"],
            description: "LLM provider for compilation",
          },
        },
        required: ["agent_id", "intent", "available_scopes", "available_resources"],
      },
      async execute(_toolCallId: string, params: Record<string, unknown>) {
        return callCarryall("carryall_compile_policy", {
          ...params,
          ttl_seconds: params.ttl_seconds ?? defaultTtl,
          llm_provider: params.llm_provider ?? defaultProvider,
        });
      },
    });

    // ── Tool 2: carryall_check_access ──────────────────────────
    api.registerTool({
      name: "carryall_check_access",
      label: "Check Access",
      description:
        "Check if an authority envelope allows a specific action on a resource. " +
        "Returns allow, deny, or require_approval.",
      parameters: {
        type: "object",
        properties: {
          envelope: {
            type: "object",
            description: "AuthorityEnvelope to validate",
          },
          action: {
            type: "string",
            description: "Action to check: read, write, or delete",
          },
          resource: {
            type: "string",
            description: "Resource URI (e.g. slos://vaults/finance/doc-001)",
          },
        },
        required: ["envelope", "action", "resource"],
      },
      async execute(_toolCallId: string, params: Record<string, unknown>) {
        return callCarryall("carryall_check_access", params);
      },
    });

    // ── Tool 3: carryall_list_vaults ───────────────────────────
    api.registerTool({
      name: "carryall_list_vaults",
      label: "List Vaults",
      description:
        "List available SLOS vaults. Requires a valid authority envelope.",
      parameters: {
        type: "object",
        properties: {
          envelope: {
            type: "object",
            description: "AuthorityEnvelope for authentication",
          },
        },
        required: ["envelope"],
      },
      async execute(_toolCallId: string, params: Record<string, unknown>) {
        return callCarryall("carryall_list_vaults", params);
      },
    });

    // ── Tool 4: carryall_get_metadata ──────────────────────────
    api.registerTool({
      name: "carryall_get_metadata",
      label: "Get Metadata",
      description:
        "Get metadata for a document in a SLOS vault. " +
        "Requires envelope with read scope for the vault.",
      parameters: {
        type: "object",
        properties: {
          envelope: {
            type: "object",
            description: "AuthorityEnvelope for authentication",
          },
          uri: {
            type: "string",
            description: "Document URI (e.g. slos://vaults/finance/doc-001)",
          },
        },
        required: ["envelope", "uri"],
      },
      async execute(_toolCallId: string, params: Record<string, unknown>) {
        return callCarryall("carryall_get_metadata", params);
      },
    });

    // ── Tool 5: carryall_audit_log ─────────────────────────────
    api.registerTool({
      name: "carryall_audit_log",
      label: "Audit Log",
      description:
        "Query the Carryall audit log. " +
        "Requires envelope with audit:read scope.",
      parameters: {
        type: "object",
        properties: {
          envelope: {
            type: "object",
            description: "AuthorityEnvelope with audit:read scope",
          },
          agent_id: {
            type: "string",
            description: "Filter by agent ID",
          },
          limit: {
            type: "integer",
            description: "Max entries to return (default 100)",
            minimum: 1,
            maximum: 1000,
          },
        },
        required: ["envelope"],
      },
      async execute(_toolCallId: string, params: Record<string, unknown>) {
        return callCarryall("carryall_audit_log", params);
      },
    });
  },
};

export default plugin;
