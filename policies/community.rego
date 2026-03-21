# Sovereign Life OS - Community Domain Policy
# Package: sovereign.community
#
# Rules for 6529 NFT community intelligence data

package sovereign.community

import future.keywords.in
import future.keywords.if
import data.sovereign.access

# Community data is internal — no external sharing
denied if {
    input.action == "external_share"
    some domain in input.document.domain
    domain == "community"
}

# Taste model is author-managed (erik), agent can only read
requires_approval if {
    input.action == "write"
    input.document.data_type == "config"
    "taste-model" in input.document.tags
    input.agent_id != "erik"
}

# Raw snapshots can be freely written by community-agent
allow if {
    input.agent_id == "community-agent"
    input.action == "write"
    some domain in input.document.domain
    domain == "community"
    input.document.data_type in ["rep-snapshot", "tdh-snapshot", "artist-record", "oracle-record", "brief"]
}
