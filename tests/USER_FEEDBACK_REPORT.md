# Authority-Runtime: User Feedback Report

**Evaluator**: Platform Engineer building AI DevOps Assistant
**Use Case**: Multi-role agent for deployment management, log viewing, secrets access
**Test Date**: 2026-01-09

---

## Executive Summary

I spent several hours evaluating authority-runtime for our DevOps AI assistant use case. The library successfully solves the core problem of cryptographic permission enforcement for AI agents. However, there are usability gaps that would slow adoption.

**Verdict**: 7/10 - Production-viable for controlled environments, needs polish for broader adoption.

---

## What I Built

A DevOps assistant with 6 tools:
- `get_deployment_status` (read:deployments)
- `get_logs` (read:logs)
- `restart_service` (write:deployments)
- `scale_service` (write:deployments)
- `get_secret` (read:secrets)
- `delete_service` (admin:deployments)

Four user roles with progressive permissions:
- **Viewer**: read only
- **Developer**: read + restart/scale
- **Senior Dev**: + secrets access
- **Admin**: + destructive operations

---

## What Worked Well

### 1. Scope Enforcement is Solid
```
Sarah (viewer) tries to restart service:
DENIED: Action requires scope 'write:deployments' but envelope only grants: ['read:deployments', 'read:logs']
```
Clear, immediate, no ambiguity.

### 2. Signature Verification Catches Tampering
Tried using an envelope with a toolkit that has different keys:
```
✓ Tampered envelope rejected: InvalidSignature
```

### 3. Parent-Child Validation Works
```python
validation = validate_envelope(child, parent_envelope=parent, public_key=key)
# Result: {'valid': False, 'errors': ["Child has scopes not in parent: {'admin:dangerous'}"]}
```
Scope expansion attacks are caught **if you call validation**.

### 4. Performance is Acceptable
```
100 tool calls in 0.056s
Average: 0.56ms per call (including signature verification)
```
Negligible overhead for most use cases.

### 5. Toolkit Decorator Pattern is Clean
```python
@toolkit.tool(scope="read:logs", name="get_logs")
def get_logs(service_name: str) -> dict:
    # No auth boilerplate here!
    return {"logs": [...]}
```

### 6. Audit Trail Captures Denials
Important for security reviews - seeing what users TRIED to do, not just what succeeded.

---

## Friction Points

### 1. Envelope Creation is Verbose
Every envelope requires:
```python
create_envelope(
    agent_id="...",
    provider="...",
    step_number=1,
    root_policy_id="...",
    skill=Skill(id="...", name="...", tool="...",
                parameters=SkillParameters(allowed=[...], constraints={})),
    authority=Authority(scopes=[...], resources=[...]),
    context=Context(included=[...], excluded=[...]),
    execution=ExecutionConfig(provider_config={}),
    private_key=key,
    ttl_seconds=300
)
```

**Want**: `create_simple_envelope(agent_id, scopes, ttl_seconds, private_key)`

### 2. No Built-in Role System
Had to build my own role → scopes mapping:
```python
role_permissions = {
    "viewer": {"scopes": ["read:deployments", "read:logs"]},
    "developer": {"scopes": ["read:deployments", "read:logs", "write:deployments"]},
    ...
}
```

**Want**: Policy files or `create_envelope_from_role(role_name)`

### 3. Resource Paths Not Enforced
```python
authority=Authority(
    scopes=["read:deployments"],
    resources=["deployments/production/*"]  # This does nothing!
)
```
Set resources but the tool can still access anything. Tool implementer must manually check resource paths.

**Want**: Built-in resource matching utilities or automatic enforcement.

### 4. Child Envelope Validation is Opt-In
```python
# This creates an envelope with expanded scopes - no error!
child = create_envelope(..., parent_envelope_id=parent.envelope_id,
                        authority=Authority(scopes=["MORE", "THAN", "PARENT"]))

# Have to manually call validation
validate_envelope(child, parent_envelope=parent)  # NOW it catches it
```

**Want**: Option to validate at creation time, or a `create_child_envelope()` helper that enforces narrowing.

### 5. Audit Metadata Nested Weirdly
```python
create_audit_entry(..., metadata={"user_id": "alice"})
# Retrieved as: {"metadata": {"metadata": {"user_id": "alice"}}}
```
Double-nested. Have to dig through to get my data.

### 6. Can't Query Audit by Custom Fields
```python
store.get_audit_trail(agent_id="...")  # Works
store.get_audit_trail(user_id="...")   # Not supported!
```
Had to pass user_id in metadata but can't query by it.

---

## Questions I Couldn't Answer from Docs

1. **Key rotation**: How do I rotate keys without invalidating existing envelopes?
2. **Multi-tenant isolation**: Is resource path enforcement coming? How should I handle it now?
3. **Clock skew**: What happens if client and server clocks differ for TTL checks?
4. **Envelope revocation**: Can I revoke an envelope before it expires?
5. **Schema versioning**: What's the migration path when envelope schema changes?

---

## Security Assessment

| Check | Result |
|-------|--------|
| Signature verification | ✓ Tampered envelopes rejected |
| Scope enforcement | ✓ Missing scopes denied |
| Scope expansion attack | ✓ Caught by validate_envelope() |
| Envelope reuse | ✓ Works within TTL (expected) |
| TTL enforcement | ✓ Minimum 60s enforced |
| Wrong key rejection | ✓ InvalidSignature raised |

**Gap**: Resource path enforcement is documentation-only, not code-enforced.

---

## Performance Observations

- Envelope creation: <1ms
- Signature verification: <1ms per call
- SQLite audit writes: <1ms
- No noticeable latency impact for typical agent workloads

---

## Would I Use This?

### Yes, for:
- Internal tools where I control both agent and tools
- Proof-of-concept agent authorization
- Audit trail requirements for compliance
- Single-tenant deployments

### Hesitant, for:
- Multi-tenant SaaS (resource isolation unclear)
- High-security environments (want more validation defaults)
- Teams unfamiliar with cryptographic auth (learning curve)

---

## Feature Requests (Priority Order)

1. **create_simple_envelope()** - Reduce boilerplate for common cases
2. **Resource path enforcement** - Either built-in or clear utilities
3. **create_child_envelope(parent, narrowed_scopes)** - Enforce narrowing at creation
4. **Query audit by metadata** - `get_audit_trail(metadata={"user_id": "..."})`
5. **Policy file support** - Define roles/scopes in YAML/JSON
6. **Envelope revocation** - Invalidate before expiry

---

## Comparison: What I'd Need Without authority-runtime

To achieve similar functionality manually:
- JWT library + custom claims validation
- Build scope checking logic for every tool
- Implement audit logging infrastructure
- Handle key management
- Build parent-child validation

**Estimate**: 2-3 weeks of work vs. getting started in hours.

authority-runtime saves significant time, even with its rough edges.

---

## Final Thoughts

The core abstractions are right:
- Envelopes with signed permissions
- Scopes for tool access control
- Parent-child narrowing for delegation
- Audit trail for compliance

The execution needs polish:
- Too much ceremony for simple cases
- Validation should be more aggressive by default
- Resource paths are half-implemented

For early adopters willing to work around the gaps, this solves a real problem. For mainstream adoption, the DX needs work.

**Recommendation**: Ship helpers and validation defaults before marketing heavily.
