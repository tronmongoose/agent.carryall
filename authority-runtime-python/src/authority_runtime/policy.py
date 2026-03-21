"""
Authority Runtime - YAML Policy Engine

Declarative agent permission configuration. A YAML file defines:
- Organization metadata and compliance frameworks
- Data classifications with PII fields, sensitivity, and retention
- Agent definitions with scopes, resources, and constraints

Usage:
    engine = PolicyEngine.load("policy.yaml")
    agent = engine.get_agent_policy("academic-advisor")
    envelope = engine.create_envelope_for_agent("academic-advisor", private_key)
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from .envelope import create_envelope
from .types import (
    Authority,
    AuthorityEnvelope,
    Context,
    ExecutionConfig,
    Skill,
    SkillParameters,
)


@dataclass
class DataClassification:
    """Classification for a data domain (vault)."""

    domain: str
    sensitivity: str  # internal, confidential, restricted
    pii_fields: List[str] = field(default_factory=list)
    retention_days: int = 0
    description: str = ""


@dataclass
class AgentPolicy:
    """Policy definition for a single agent."""

    agent_id: str
    description: str = ""
    scopes: List[str] = field(default_factory=list)
    resources: List[str] = field(default_factory=list)
    constraints: Dict[str, Any] = field(default_factory=dict)
    denied_resources: List[str] = field(default_factory=list)


@dataclass
class PolicyDefinition:
    """Complete policy document."""

    version: str
    organization: str
    compliance_frameworks: List[str] = field(default_factory=list)
    data_classifications: Dict[str, DataClassification] = field(default_factory=dict)
    agents: Dict[str, AgentPolicy] = field(default_factory=dict)


class PolicyValidationError(Exception):
    """Raised when a policy file is invalid."""
    pass


class PolicyEngine:
    """Loads, validates, and applies YAML policy files."""

    def __init__(self, policy: PolicyDefinition):
        self.policy = policy

    @classmethod
    def load(cls, path: str) -> "PolicyEngine":
        """Load and validate a YAML policy file."""
        policy_path = Path(path)
        if not policy_path.exists():
            raise PolicyValidationError(f"Policy file not found: {path}")

        with open(policy_path) as f:
            raw = yaml.safe_load(f)

        if not isinstance(raw, dict):
            raise PolicyValidationError("Policy file must be a YAML mapping")

        policy = cls._parse_policy(raw)
        cls._validate_policy(policy)
        return cls(policy)

    @classmethod
    def load_from_string(cls, content: str) -> "PolicyEngine":
        """Load and validate a YAML policy from a string."""
        raw = yaml.safe_load(content)
        if not isinstance(raw, dict):
            raise PolicyValidationError("Policy must be a YAML mapping")
        policy = cls._parse_policy(raw)
        cls._validate_policy(policy)
        return cls(policy)

    @classmethod
    def _parse_policy(cls, raw: dict) -> PolicyDefinition:
        """Parse raw YAML dict into PolicyDefinition."""
        # Data classifications
        classifications = {}
        for domain, dc_data in raw.get("data_classifications", {}).items():
            classifications[domain] = DataClassification(
                domain=domain,
                sensitivity=dc_data.get("sensitivity", "internal"),
                pii_fields=dc_data.get("pii_fields", []),
                retention_days=dc_data.get("retention_days", 0),
                description=dc_data.get("description", ""),
            )

        # Agents
        agents = {}
        for agent_id, agent_data in raw.get("agents", {}).items():
            # Merge denied_resources into constraints for enforcement
            constraints = dict(agent_data.get("constraints", {}))
            denied = agent_data.get("denied_resources", [])
            if denied:
                constraints["denied_resources"] = denied

            agents[agent_id] = AgentPolicy(
                agent_id=agent_id,
                description=agent_data.get("description", ""),
                scopes=agent_data.get("scopes", []),
                resources=agent_data.get("resources", ["slos://vaults/*"]),
                constraints=constraints,
                denied_resources=denied,
            )

        return PolicyDefinition(
            version=str(raw.get("version", "1.0")),
            organization=raw.get("organization", ""),
            compliance_frameworks=raw.get("compliance_frameworks", []),
            data_classifications=classifications,
            agents=agents,
        )

    @classmethod
    def _validate_policy(cls, policy: PolicyDefinition):
        """Validate policy consistency."""
        errors = []

        if not policy.version:
            errors.append("Missing 'version'")
        if not policy.organization:
            errors.append("Missing 'organization'")

        for agent_id, agent in policy.agents.items():
            if not agent.scopes:
                errors.append(f"Agent '{agent_id}' has no scopes")

        if errors:
            raise PolicyValidationError(
                f"Policy validation failed:\n" + "\n".join(f"  - {e}" for e in errors)
            )

    def get_agent_policy(self, agent_id: str) -> AgentPolicy:
        """Get policy for a specific agent."""
        if agent_id not in self.policy.agents:
            raise KeyError(f"Agent '{agent_id}' not found in policy")
        return self.policy.agents[agent_id]

    def list_agents(self) -> List[AgentPolicy]:
        """List all agent policies."""
        return list(self.policy.agents.values())

    def get_data_classification(self, domain: str) -> DataClassification:
        """Get data classification for a domain."""
        if domain not in self.policy.data_classifications:
            raise KeyError(f"Data classification '{domain}' not found in policy")
        return self.policy.data_classifications[domain]

    def list_data_classifications(self) -> List[DataClassification]:
        """List all data classifications."""
        return list(self.policy.data_classifications.values())

    def create_envelope_for_agent(
        self,
        agent_id: str,
        private_key: str,
        provider: str = "custom",
        step_number: int = 1,
        ttl_seconds: int = 3600,
    ) -> AuthorityEnvelope:
        """Create a pre-configured envelope from policy definition."""
        agent = self.get_agent_policy(agent_id)

        return create_envelope(
            agent_id=agent_id,
            provider=provider,
            step_number=step_number,
            root_policy_id=f"policy-{self.policy.organization}-{agent_id}",
            skill=Skill(
                id=f"skill-{agent_id}",
                name=f"{agent_id}-access",
                tool="policy-managed",
                parameters=SkillParameters(
                    allowed=["read", "write"],
                    constraints={},
                ),
            ),
            authority=Authority(
                scopes=agent.scopes,
                resources=agent.resources,
                constraints=agent.constraints,
            ),
            context=Context(included=["purpose", "student_id"], excluded=["ssn", "dob"]),
            execution=ExecutionConfig(provider_config={}),
            private_key=private_key,
            ttl_seconds=ttl_seconds,
        )

    def summary(self) -> dict:
        """Return a summary of the policy for display."""
        return {
            "version": self.policy.version,
            "organization": self.policy.organization,
            "compliance_frameworks": self.policy.compliance_frameworks,
            "agent_count": len(self.policy.agents),
            "classification_count": len(self.policy.data_classifications),
            "agents": [
                {
                    "id": a.agent_id,
                    "scopes": a.scopes,
                    "constraints": {k: v for k, v in a.constraints.items() if k != "denied_resources"},
                    "denied_resources": a.denied_resources,
                }
                for a in self.policy.agents.values()
            ],
            "data_classifications": [
                {
                    "domain": dc.domain,
                    "sensitivity": dc.sensitivity,
                    "pii_fields": dc.pii_fields,
                    "retention_days": dc.retention_days,
                }
                for dc in self.policy.data_classifications.values()
            ],
        }
