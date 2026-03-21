# Sovereign Life OS - Base Access Control Policy
# Package: sovereign.access
#
# This is the foundational policy that all access decisions flow through.
# Default deny - explicit allow required.

package sovereign.access

import future.keywords.in
import future.keywords.if
import future.keywords.contains

default allow := false
default denied := false
default requires_approval := false
default audit_required := false
default purpose_required := false
default rate_limit_exceeded := false

# Sensitivity levels (higher = more restricted)
sensitivity_level := {
    "public": 0,
    "internal": 1,
    "confidential": 2,
    "restricted": 3
}

# Allow read if agent has explicit read access to domain
allow if {
    input.action == "read"
    some domain in input.document.domain
    domain in data.agents[input.agent_id].capabilities.read
    not denied
}

# Allow write if agent has explicit write access to domain
allow if {
    input.action == "write"
    some domain in input.document.domain
    domain in data.agents[input.agent_id].capabilities.write
    not denied
}

# Allow limited read for specific fields
allow if {
    input.action == "read"
    some limited in data.agents[input.agent_id].capabilities.read_limited
    some domain in input.document.domain
    domain == limited.domain
    input.fields_requested != null
    every field in input.fields_requested {
        field in limited.fields
    }
    not denied
}

# Deny takes precedence - agent restricted from domain
denied if {
    some domain in input.document.domain
    domain in data.agents[input.agent_id].restrictions.cannot_access
}

# Deny if document explicitly denies this agent
denied if {
    input.document.denied_agents != null
    input.agent_id in input.document.denied_agents
}

# Deny if sensitivity exceeds agent clearance
denied if {
    sensitivity_level[input.document.sensitivity] > data.agents[input.agent_id].clearance_level
}

# Deny if agent is restricted from seeing specific data types
denied if {
    data.agents[input.agent_id].restrictions.cannot_see != null
    some restricted in data.agents[input.agent_id].restrictions.cannot_see
    restricted in input.document.tags
}

# Deny bulk export if agent restricted
denied if {
    input.action == "bulk_export"
    data.agents[input.agent_id].restrictions.cannot_bulk_export == true
}

# Deny credential access if agent restricted
denied if {
    input.document.data_type == "credential"
    data.agents[input.agent_id].restrictions.cannot_access_credentials == true
}

# Deny external API calls if agent restricted
denied if {
    input.action == "external_api"
    data.agents[input.agent_id].restrictions.no_external_apis == true
}

# Cross-domain access requires approval
requires_approval if {
    input.action == "read"
    some limited in data.agents[input.agent_id].capabilities.read_limited
    some domain in input.document.domain
    domain == limited.domain
    limited.requires_approval == true
}

# Document-level approval requirements
requires_approval if {
    input.document.requires_approval != null
    input.action in input.document.requires_approval
}

# Agent-level approval requirements
requires_approval if {
    data.agents[input.agent_id].restrictions.requires_approval != null
    input.action in data.agents[input.agent_id].restrictions.requires_approval
}

# Purpose required check
purpose_required if {
    data.agents[input.agent_id].restrictions.requires_purpose == true
}

# Audit logging requirement - document level
audit_required if {
    input.document.audit_level == "full"
}

# Audit logging requirement - agent level
audit_required if {
    data.agents[input.agent_id].restrictions.audit_level == "full"
}

# Rate limit check
rate_limit_exceeded if {
    input.queries_this_hour > data.agents[input.agent_id].rate_limits.queries_per_hour
}

# Decision response object
decision := {
    "allow": allow,
    "denied": denied,
    "requires_approval": requires_approval,
    "audit_required": audit_required,
    "purpose_required": purpose_required,
    "rate_limit_exceeded": rate_limit_exceeded
}
