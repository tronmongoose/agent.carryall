"""
USER SCENARIO: AI-Powered DevOps Assistant

I'm a platform engineer at a mid-size company. We're building an AI assistant
that can help developers with common DevOps tasks:
- Check deployment status
- View logs
- Restart services
- Scale deployments
- Access secrets (read-only for debugging)

The problem: These actions have real consequences. A junior dev shouldn't be able
to scale production to 100 replicas. An intern shouldn't see production secrets.
But we want ONE agent that serves everyone, with permissions based on who's asking.

Let me try authority-runtime to see if it solves this.
"""

import json
from datetime import datetime
from typing import Optional

from authority_runtime import (
    generate_key_pair,
    create_envelope,
    EnforcedTool,
    EnforcedToolkit,
    EnvelopeStore,
    create_audit_entry,
    Authority,
    Context,
    Skill,
    SkillParameters,
    ExecutionConfig,
    PermissionDenied,
    validate_envelope,
)


# =============================================================================
# MY INFRASTRUCTURE (simulated)
# =============================================================================

DEPLOYMENTS = {
    "api-gateway": {"replicas": 3, "status": "healthy", "env": "production"},
    "user-service": {"replicas": 2, "status": "healthy", "env": "production"},
    "payment-service": {"replicas": 2, "status": "degraded", "env": "production"},
    "staging-api": {"replicas": 1, "status": "healthy", "env": "staging"},
}

LOGS = {
    "api-gateway": ["[INFO] Request handled", "[INFO] Health check OK", "[WARN] High latency detected"],
    "payment-service": ["[ERROR] Connection timeout to payment provider", "[ERROR] Retry failed", "[INFO] Circuit breaker opened"],
}

SECRETS = {
    "production": {"DB_PASSWORD": "prod-secret-123", "API_KEY": "sk-live-xxx"},
    "staging": {"DB_PASSWORD": "staging-pass", "API_KEY": "sk-test-xxx"},
}

AUDIT_LOG = []


# =============================================================================
# SETUP
# =============================================================================

print("="*70)
print("SETUP: Initializing authority-runtime")
print("="*70)

private_key, public_key = generate_key_pair()
store = EnvelopeStore("./devops_audit.db")
toolkit = EnforcedToolkit(public_key=public_key)

print("✓ Generated key pair")
print("✓ Created audit store")
print("✓ Created toolkit")


# =============================================================================
# DEFINE MY TOOLS
# =============================================================================

@toolkit.tool(scope="read:deployments", name="get_deployment_status")
def get_deployment_status(service_name: str) -> dict:
    """Get the status of a deployment."""
    if service_name not in DEPLOYMENTS:
        return {"error": f"Service '{service_name}' not found"}
    return {"service": service_name, **DEPLOYMENTS[service_name]}


@toolkit.tool(scope="read:logs", name="get_logs")
def get_logs(service_name: str, lines: int = 10) -> dict:
    """Get recent logs for a service."""
    if service_name not in LOGS:
        return {"service": service_name, "logs": ["No logs available"]}
    return {"service": service_name, "logs": LOGS[service_name][-lines:]}


@toolkit.tool(scope="write:deployments", name="restart_service")
def restart_service(service_name: str) -> dict:
    """Restart a service. Requires write:deployments scope."""
    if service_name not in DEPLOYMENTS:
        return {"error": f"Service '{service_name}' not found"}
    # Simulate restart
    DEPLOYMENTS[service_name]["status"] = "restarting"
    return {"service": service_name, "action": "restart", "status": "initiated"}


@toolkit.tool(scope="write:deployments", name="scale_service")
def scale_service(service_name: str, replicas: int) -> dict:
    """Scale a service to N replicas. Requires write:deployments scope."""
    if service_name not in DEPLOYMENTS:
        return {"error": f"Service '{service_name}' not found"}
    if replicas > 10:
        return {"error": "Cannot scale beyond 10 replicas without admin approval"}
    old_replicas = DEPLOYMENTS[service_name]["replicas"]
    DEPLOYMENTS[service_name]["replicas"] = replicas
    return {
        "service": service_name,
        "action": "scale",
        "old_replicas": old_replicas,
        "new_replicas": replicas
    }


@toolkit.tool(scope="read:secrets", name="get_secret")
def get_secret(env: str, secret_name: str) -> dict:
    """Get a secret value. SENSITIVE - requires read:secrets scope."""
    if env not in SECRETS:
        return {"error": f"Environment '{env}' not found"}
    if secret_name not in SECRETS[env]:
        return {"error": f"Secret '{secret_name}' not found in {env}"}
    # Return masked value for audit safety
    value = SECRETS[env][secret_name]
    return {
        "env": env,
        "secret": secret_name,
        "value": value,  # In real system, might mask this
        "warning": "This value is sensitive - do not share"
    }


@toolkit.tool(scope="admin:deployments", name="delete_service")
def delete_service(service_name: str, confirm: str) -> dict:
    """DELETE a service entirely. DANGEROUS - requires admin:deployments."""
    if confirm != f"DELETE-{service_name}":
        return {"error": "Confirmation string mismatch"}
    if service_name in DEPLOYMENTS:
        del DEPLOYMENTS[service_name]
        return {"service": service_name, "action": "deleted", "status": "success"}
    return {"error": f"Service '{service_name}' not found"}


print("✓ Registered 6 tools with different scope requirements")


# =============================================================================
# HELPER: Create envelopes for different user roles
# =============================================================================

def create_envelope_for_role(role: str, user_id: str) -> "AuthorityEnvelope":
    """
    Create an envelope based on user role.
    In production, this would be done by an auth gateway based on OAuth/SSO.
    """
    role_permissions = {
        "viewer": {
            "scopes": ["read:deployments", "read:logs"],
            "description": "Can view status and logs only"
        },
        "developer": {
            "scopes": ["read:deployments", "read:logs", "write:deployments"],
            "description": "Can view and restart/scale services"
        },
        "senior_dev": {
            "scopes": ["read:deployments", "read:logs", "write:deployments", "read:secrets"],
            "description": "Can also view secrets for debugging"
        },
        "admin": {
            "scopes": ["read:deployments", "read:logs", "write:deployments", "read:secrets", "admin:deployments"],
            "description": "Full access including destructive operations"
        }
    }

    if role not in role_permissions:
        raise ValueError(f"Unknown role: {role}")

    perms = role_permissions[role]

    return create_envelope(
        agent_id="devops-assistant",
        provider="openai",
        step_number=1,
        root_policy_id=f"session-{user_id}-{datetime.now().strftime('%Y%m%d%H%M%S')}",
        skill=Skill(
            id="devops-ops",
            name="DevOps Operations",
            tool="DevOps toolkit",
            parameters=SkillParameters(allowed=["service_name", "replicas", "lines", "env", "secret_name"], constraints={})
        ),
        authority=Authority(
            scopes=perms["scopes"],
            resources=["deployments/*", "logs/*", "secrets/*"]
        ),
        context=Context(
            included=["service_name", "status", "replicas", "env"],
            excluded=[]  # Secrets exclusion handled by scope
        ),
        execution=ExecutionConfig(provider_config={}),
        private_key=private_key,
        ttl_seconds=3600  # 1 hour session
    )


# =============================================================================
# HELPER: Execute with audit logging
# =============================================================================

def execute_tool(envelope, tool_name: str, user_id: str, **kwargs) -> dict:
    """Execute a tool and log the result."""
    print(f"\n  [{user_id}] Calling: {tool_name}({kwargs})")

    try:
        result = toolkit.execute(tool_name, envelope, **kwargs)

        audit = create_audit_entry(
            action=tool_name,
            envelope=envelope,
            public_key=public_key,
            result="success",
            metadata={"user_id": user_id, "args": kwargs}
        )
        store.save_audit_entry(audit)

        print(f"  [{user_id}] Result: {json.dumps(result)}")
        return result

    except PermissionDenied as e:
        audit = create_audit_entry(
            action=tool_name,
            envelope=envelope,
            public_key=public_key,
            result="denied",
            metadata={"user_id": user_id, "args": kwargs, "error": str(e)}
        )
        store.save_audit_entry(audit)

        print(f"  [{user_id}] DENIED: {e}")
        return {"error": str(e)}


# =============================================================================
# SCENARIO 1: Intern (viewer role) tries to help with an incident
# =============================================================================

print("\n" + "="*70)
print("SCENARIO 1: Intern Sarah tries to help with payment-service incident")
print("="*70)

sarah_envelope = create_envelope_for_role("viewer", "sarah-intern")
store.save_envelope(sarah_envelope)

print(f"\nSarah's permissions: {sarah_envelope.authority.scopes}")
print("Sarah heard payment-service is down and wants to help...")

# She can check status
execute_tool(sarah_envelope, "get_deployment_status", "sarah-intern", service_name="payment-service")

# She can view logs
execute_tool(sarah_envelope, "get_logs", "sarah-intern", service_name="payment-service")

# She tries to restart it (will fail)
print("\nSarah: 'I'll just restart it real quick...'")
execute_tool(sarah_envelope, "restart_service", "sarah-intern", service_name="payment-service")

# She tries to check secrets to debug (will fail)
print("\nSarah: 'Maybe I can check if the API key is wrong...'")
execute_tool(sarah_envelope, "get_secret", "sarah-intern", env="production", secret_name="API_KEY")


# =============================================================================
# SCENARIO 2: Developer fixes the incident
# =============================================================================

print("\n" + "="*70)
print("SCENARIO 2: Developer Mike takes over the incident")
print("="*70)

mike_envelope = create_envelope_for_role("developer", "mike-dev")
store.save_envelope(mike_envelope)

print(f"\nMike's permissions: {mike_envelope.authority.scopes}")

# Mike checks status
execute_tool(mike_envelope, "get_deployment_status", "mike-dev", service_name="payment-service")

# Mike restarts the service (succeeds)
execute_tool(mike_envelope, "restart_service", "mike-dev", service_name="payment-service")

# Mike tries to check secrets (fails - not senior)
print("\nMike: 'Restart didn't help, let me check the API key...'")
execute_tool(mike_envelope, "get_secret", "mike-dev", env="production", secret_name="API_KEY")


# =============================================================================
# SCENARIO 3: Senior dev investigates with secrets access
# =============================================================================

print("\n" + "="*70)
print("SCENARIO 3: Senior Dev Alice investigates with secrets access")
print("="*70)

alice_envelope = create_envelope_for_role("senior_dev", "alice-senior")
store.save_envelope(alice_envelope)

print(f"\nAlice's permissions: {alice_envelope.authority.scopes}")

# Alice checks the secret
execute_tool(alice_envelope, "get_secret", "alice-senior", env="production", secret_name="API_KEY")

# Alice scales up the service to handle load
execute_tool(alice_envelope, "scale_service", "alice-senior", service_name="payment-service", replicas=5)

# Alice tries to delete a broken staging service (fails - not admin)
print("\nAlice: 'While I'm here, let me clean up that broken staging deploy...'")
execute_tool(alice_envelope, "delete_service", "alice-senior", service_name="staging-api", confirm="DELETE-staging-api")


# =============================================================================
# SCENARIO 4: Admin does cleanup
# =============================================================================

print("\n" + "="*70)
print("SCENARIO 4: Admin Bob does cleanup")
print("="*70)

bob_envelope = create_envelope_for_role("admin", "bob-admin")
store.save_envelope(bob_envelope)

print(f"\nBob's permissions: {bob_envelope.authority.scopes}")

# Bob can delete services
execute_tool(bob_envelope, "delete_service", "bob-admin", service_name="staging-api", confirm="DELETE-staging-api")


# =============================================================================
# SCENARIO 5: Expired session
# =============================================================================

print("\n" + "="*70)
print("SCENARIO 5: Testing session expiration")
print("="*70)

# Create a very short-lived envelope (minimum 60 seconds)
short_envelope = create_envelope(
    agent_id="devops-assistant",
    provider="openai",
    step_number=1,
    root_policy_id="short-session",
    skill=Skill(id="test", name="Test", tool="Test", parameters=SkillParameters(allowed=[], constraints={})),
    authority=Authority(scopes=["read:deployments"], resources=["*"]),
    context=Context(included=[], excluded=[]),
    execution=ExecutionConfig(provider_config={}),
    private_key=private_key,
    ttl_seconds=60  # Minimum allowed
)

print(f"Created envelope expiring at: {short_envelope.expires_at}")
print("(In production, expired envelopes would be rejected)")


# =============================================================================
# AUDIT TRAIL REVIEW
# =============================================================================

print("\n" + "="*70)
print("AUDIT TRAIL: What happened during this incident?")
print("="*70)

trail = store.get_audit_trail(limit=20)
print(f"\nTotal audit entries: {len(trail)}")

# Group by result
successes = [e for e in trail if e.get("result") == "success"]
denials = [e for e in trail if e.get("result") == "denied"]

print(f"Successful actions: {len(successes)}")
print(f"Denied actions: {len(denials)}")

print("\n--- Denied Actions (security events) ---")
for entry in denials:
    meta = entry.get("metadata", {})
    if isinstance(meta, str):
        meta = json.loads(meta) if meta else {}
    print(f"  User: {meta.get('user_id', 'unknown')}")
    print(f"  Action: {entry.get('action')}")
    print(f"  Time: {entry.get('timestamp')}")
    print()


# =============================================================================
# USER FEEDBACK
# =============================================================================

print("\n" + "="*70)
print("USER FEEDBACK: My experience with authority-runtime")
print("="*70)

print("""
WHAT WORKED WELL:
-----------------
1. Role-based envelope creation was intuitive
   - Map role -> scopes, create envelope, done
   - The scope format (action:resource) is clear

2. Permission denials are clear and immediate
   - "Action requires scope 'write:deployments' but envelope only grants..."
   - Easy to understand what's missing

3. Audit trail captures both successes AND denials
   - Can see exactly what Sarah tried to do
   - Important for security reviews

4. Toolkit decorator pattern is clean
   - @toolkit.tool(scope="read:logs") just works
   - No boilerplate in the tool implementation


FRICTION POINTS / SUGGESTIONS:
------------------------------
1. Envelope creation is verbose
   - Need to specify Skill, SkillParameters, ExecutionConfig even for simple cases
   - Would like: create_simple_envelope(agent_id, scopes, ttl_seconds)

2. No built-in role system
   - Had to build my own role -> scopes mapping
   - Would be nice to have create_envelope_from_role() or policy files

3. Resource-level enforcement is unclear
   - I set resources=["deployments/*"] but tools don't check it
   - Is this supposed to be enforced? How?

4. Error message for expired envelopes
   - Would be nice to know WHEN it expired, not just that it did

5. Audit trail query is limited
   - Can't easily query "all actions by user X"
   - get_audit_trail(agent_id=...) uses envelope's agent_id, not my user_id


QUESTIONS FOR THE MAINTAINERS:
------------------------------
1. How do I enforce resource paths?
   - I have resources=["deployments/production/*"]
   - But nothing stops me from accessing staging

2. How does narrowing work in practice?
   - If Alice delegates to a sub-agent, how do I create the child envelope?
   - Is there a helper for this?

3. What's the recommended key management strategy?
   - One key pair per service? Per environment?
   - How do I rotate keys?

4. How do I handle multi-tenant scenarios?
   - Customer A's agent shouldn't see Customer B's resources
   - Is this just resource path enforcement?


OVERALL VERDICT:
----------------
authority-runtime solves a real problem. The core concept is sound:
- Signed envelopes with scopes
- Enforced at tool layer
- Full audit trail

But it feels like an alpha/beta product:
- Core functionality works
- Missing convenience helpers
- Resource enforcement unclear
- Documentation gaps

I'd use this for: Internal tools where I control both the agent and the tools.
I'd hesitate for: Multi-tenant SaaS where resource isolation is critical.

Rating: 7/10 - Promising, needs polish
""")
