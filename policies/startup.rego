# Sovereign Life OS - Startup Domain Policy
# Package: sovereign.startup
#
# Rules for startup/entrepreneurial data

package sovereign.startup

import future.keywords.in
import future.keywords.if
import data.sovereign.access

# Competitor analysis can be shared for research
allow_web_research if {
    input.agent_id == "startup-agent"
    input.action == "web_search"
    "competitors" in input.document.tags
}

# Ideas marked as confidential need extra protection
requires_approval if {
    input.document.sensitivity == "confidential"
    "ideas" in input.document.tags
    input.action == "external_share"
}

# Agent.carryall project has special handling
# Can be referenced by executive for cross-project coordination
allow if {
    input.agent_id == "executive-agent"
    input.action == "read"
    "agent-carryall" in input.document.tags
    input.purpose != null
}

# Market research can reference financial runway
allow_runway_check if {
    input.agent_id == "startup-agent"
    input.action == "read"
    "finance" in input.document.domain
    input.fields_requested == ["runway"]
    input.purpose == "planning"
}

# Protect pre-launch information
denied if {
    "pre-launch" in input.document.tags
    input.document.sensitivity == "restricted"
    input.action == "external_share"
}
