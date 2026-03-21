# Sovereign Life OS - Finance Domain Policy
# Package: sovereign.finance
#
# Additional rules specific to financial data protection

package sovereign.finance

import future.keywords.in
import future.keywords.if
import data.sovereign.access

# Financial transactions over threshold require approval
requires_approval if {
    input.action == "write"
    input.document.data_type == "transaction"
    input.document.amount > 1000
}

# Bulk transaction reports require approval
requires_approval if {
    input.action == "read"
    input.query.type == "report"
    input.query.transaction_count > 100
}

# Tax documents are always full audit
audit_required if {
    "taxes" in input.document.tags
}

# LLC documents require business entity context
denied if {
    input.document.business_entity == "huckle-llc"
    input.context.acting_as != "huckle-llc"
}

# Investment data cannot be shared externally
denied if {
    "investments" in input.document.tags
    input.action == "external_share"
}

# Cross-reference with startup runway is allowed for finance agent
allow_limited_startup if {
    input.agent_id == "finance-agent"
    input.action == "read"
    "startup" in input.document.domain
    input.fields_requested != null
    every field in input.fields_requested {
        field in ["runway", "burn-rate"]
    }
    input.purpose == "financial-planning"
}

# accountant-agent: read-only, finance/taxes subpath only
denied if {
    input.agent_id == "accountant-agent"
    input.action == "write"
}

denied if {
    input.agent_id == "accountant-agent"
    not startswith(input.resource, "slos://vaults/finance/taxes/")
}

# investment-agent: read-only, no external share of investment data
denied if {
    input.agent_id == "investment-agent"
    input.action == "write"
}

denied if {
    input.agent_id == "investment-agent"
    input.action == "external_share"
}
