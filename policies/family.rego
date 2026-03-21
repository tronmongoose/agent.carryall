# Sovereign Life OS - Family Domain Policy
# Package: sovereign.family
#
# Rules for family shared and individual spaces

package sovereign.family

import future.keywords.in
import future.keywords.if
import data.sovereign.access

# Shared family documents accessible by both Erik and Janelle
allow if {
    input.action == "read"
    "family" in input.document.domain
    input.document.family_member == "shared"
    input.user in ["erik", "janelle"]
}

# Individual family member spaces require ownership
denied if {
    "family" in input.document.domain
    input.document.family_member == "erik"
    input.user == "janelle"
    input.document.sensitivity in ["confidential", "restricted"]
}

denied if {
    "family" in input.document.domain
    input.document.family_member == "janelle"
    input.user == "erik"
    input.document.sensitivity in ["confidential", "restricted"]
}

# Agents cannot access family data by default
denied if {
    "family" in input.document.domain
    startswith(input.agent_id, "agent:")
    input.document.allowed_agents == null
}

denied if {
    "family" in input.document.domain
    startswith(input.agent_id, "agent:")
    input.document.allowed_agents != null
    not input.agent_id in input.document.allowed_agents
}

# Joint financial decisions require both parties
requires_approval if {
    "family" in input.document.domain
    input.document.family_member == "shared"
    input.action == "write"
    input.document.data_type == "transaction"
    input.document.amount > 500
}
