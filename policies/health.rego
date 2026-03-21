# Sovereign Life OS - Health Domain Policy
# Package: sovereign.health
#
# Strictest access controls - health data is sacred

package sovereign.health

import future.keywords.in
import future.keywords.if
import data.sovereign.access

# Health domain ALWAYS requires full audit
audit_required := true

# No external API access for health data ever
denied if {
    "health" in input.document.domain
    input.action == "external_api"
}

# No bulk export of health data
denied if {
    "health" in input.document.domain
    input.action == "bulk_export"
}

# Health records cannot be shared without explicit approval
requires_approval if {
    "health" in input.document.domain
    input.action in ["external_share", "cross-domain-query"]
}

# Medical records have maximum retention
retention_policy := "permanent" if {
    input.document.data_type == "record"
    "medical" in input.document.tags
}

# Appointment data can be read by executive for scheduling
allow if {
    input.agent_id == "executive-agent"
    input.action == "read"
    input.document.data_type == "record"
    "appointments" in input.document.tags
    input.fields_requested != null
    every field in input.fields_requested {
        field in ["date", "time", "type", "location"]
    }
}

# Deny access to diagnosis/treatment details from non-health agents
denied if {
    input.agent_id != "health-agent"
    "diagnosis" in input.document.tags
}

denied if {
    input.agent_id != "health-agent"
    "treatment" in input.document.tags
}
