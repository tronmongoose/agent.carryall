# Sovereign Life OS - Cross-Domain Access Policy
# Package: sovereign.cross_domain
#
# Rules for legitimate cross-domain access patterns

package sovereign.cross_domain

import future.keywords.in
import future.keywords.if
import data.sovereign.access

# All cross-domain access requires approval by default
default requires_approval := true

# Executive agent can coordinate across domains with purpose
allow_coordination if {
    input.agent_id == "executive-agent"
    input.action == "read"
    input.purpose != null
    input.purpose != ""
}

# Financial planning can reference startup runway
allow_financial_planning if {
    input.agent_id == "finance-agent"
    input.action == "read"
    "startup" in input.document.domain
    input.purpose == "financial-planning"
    input.fields_requested != null
    every field in input.fields_requested {
        field in ["runway", "burn-rate", "revenue"]
    }
}

# Startup planning can check financial runway
allow_startup_planning if {
    input.agent_id == "startup-agent"
    input.action == "read"
    "finance" in input.document.domain
    input.purpose == "planning"
    input.fields_requested == ["runway"]
}

# Health appointments visible to executive for scheduling
allow_scheduling if {
    input.agent_id == "executive-agent"
    input.action == "read"
    "health" in input.document.domain
    "appointments" in input.document.tags
    input.fields_requested != null
    every field in input.fields_requested {
        field in ["date", "time", "type", "location", "provider"]
    }
}

# Cross-domain requests must be logged
audit_required if {
    count([d | some d in input.document.domain; d != input.agent_primary_domain]) > 0
}

# Approval workflow
approval_workflow := {
    "type": "human",
    "timeout": "24h",
    "escalation": "deny"
} if {
    requires_approval
}

# Denied cross-domain patterns
denied if {
    # Health data never crosses to startup
    "health" in input.document.domain
    input.agent_id == "startup-agent"
}

denied if {
    # Personal data never crosses to startup/finance
    "personal" in input.document.domain
    input.agent_id in ["startup-agent", "finance-agent"]
}

denied if {
    # Restricted documents never cross domains without explicit allow
    input.document.sensitivity == "restricted"
    count([d | some d in input.document.domain; d != input.agent_primary_domain]) > 0
    input.document.allowed_agents == null
}

denied if {
    input.document.sensitivity == "restricted"
    count([d | some d in input.document.domain; d != input.agent_primary_domain]) > 0
    input.document.allowed_agents != null
    not input.agent_id in input.document.allowed_agents
}
