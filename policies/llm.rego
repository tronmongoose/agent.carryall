# Sovereign Life OS - LLM Access Control Policy
# Package: sovereign.llm
#
# Controls access to local LLM inference (Ollama).
# SECURITY: All LLM calls go to localhost:11434 - data NEVER leaves the machine.

package sovereign.llm

import future.keywords.in
import future.keywords.if

# Default: allow LLM access for registered agents
default allow_inference := false

# Allow LLM inference for agents with llm capability
allow_inference if {
    input.action in ["llm_generate", "llm_chat", "llm_summarize", "llm_analyze"]
    data.agents[input.agent_id].capabilities.llm == true
}

# Executive agent always has LLM access
allow_inference if {
    input.agent_id == "executive-agent"
    input.action in ["llm_generate", "llm_chat", "llm_summarize", "llm_analyze"]
}

# Claude Code (default MCP client) has LLM access
allow_inference if {
    input.agent_id == "claude-code"
    input.action in ["llm_generate", "llm_chat", "llm_summarize", "llm_analyze"]
}

# Personal agent has LLM access for journaling, learning
allow_inference if {
    input.agent_id == "personal-agent"
    input.action in ["llm_generate", "llm_chat", "llm_summarize"]
}

# Startup agent has LLM access for research, planning
allow_inference if {
    input.agent_id == "startup-agent"
    input.action in ["llm_generate", "llm_chat", "llm_summarize", "llm_analyze"]
}

# Requires approval for LLM analysis of sensitive domains
requires_approval if {
    input.action == "llm_analyze"
    input.context_domains != null
    some domain in input.context_domains
    domain in ["health", "finance"]
}

# Audit all LLM calls that include document context
audit_required if {
    input.action == "llm_analyze"
    input.document_ids != null
    count(input.document_ids) > 0
}

# Audit summarization of specific documents
audit_required if {
    input.action == "llm_summarize"
    input.document_id != null
}

# Denied patterns
denied if {
    # Health agent cannot use LLM (too sensitive)
    input.agent_id == "health-agent"
    input.action in ["llm_generate", "llm_chat"]
}

denied if {
    # Finance agent restricted from general LLM chat (could leak financial data)
    input.agent_id == "finance-agent"
    input.action == "llm_chat"
}

# Decision response
decision := {
    "allow": allow_inference,
    "denied": denied,
    "requires_approval": requires_approval,
    "audit_required": audit_required
}
