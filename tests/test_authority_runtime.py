"""
Comprehensive Test Suite for authority-runtime
Tests external API functionality based on actual implementation.
"""
import os
import tempfile
import time
from datetime import datetime

# Core imports
from authority_runtime import (
    # Key generation & envelope
    generate_key_pair,
    create_envelope,
    validate_envelope,
    verify_signature,

    # Types
    Authority,
    Context,
    Skill,
    SkillParameters,
    ExecutionConfig,
    AuthorityEnvelope,

    # Enforcement
    EnforcedTool,
    EnforcedToolkit,

    # Errors
    PermissionDenied,
    InvalidSignature,
    EnvelopeExpired,
    ValidationError,

    # Storage & Audit
    EnvelopeStore,
    create_audit_entry,
    export_audit_trail,

    # Validation helpers
    check_envelope,
    check_context_field,
)


class TestResults:
    """Track test results."""
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.errors = []

    def success(self, name: str):
        self.passed += 1
        print(f"  PASS: {name}")

    def fail(self, name: str, error: str):
        self.failed += 1
        self.errors.append((name, error))
        print(f"  FAIL: {name} - {error}")

    def summary(self):
        total = self.passed + self.failed
        print(f"\n{'='*60}")
        print(f"Results: {self.passed}/{total} passed, {self.failed} failed")
        if self.errors:
            print("\nFailed tests:")
            for name, error in self.errors:
                print(f"  - {name}: {error}")
        return self.failed == 0


results = TestResults()


# =============================================================================
# TEST 1: KEY GENERATION & ENVELOPE CREATION
# =============================================================================
print("\n" + "="*60)
print("TEST SUITE 1: Key Generation & Envelope Creation")
print("="*60)

# Test 1.1: Key pair generation
try:
    private_key, public_key = generate_key_pair()
    assert private_key is not None
    assert public_key is not None
    assert len(private_key) > 0
    assert len(public_key) > 0
    results.success("Key pair generation")
except Exception as e:
    results.fail("Key pair generation", str(e))

# Test 1.2: Basic envelope creation
try:
    envelope = create_envelope(
        agent_id="test-agent-001",
        provider="openai",
        step_number=1,
        root_policy_id="policy-001",
        skill=Skill(
            id="skill-001",
            name="read_user",
            tool="Read user data",
            parameters=SkillParameters(allowed=["user_id"], constraints={})
        ),
        authority=Authority(
            scopes=["read:user", "write:user"],
            resources=["users/*"]
        ),
        context=Context(
            included=["user_id", "email", "name"],
            excluded=["password", "ssn"]
        ),
        execution=ExecutionConfig(provider_config={}),
        private_key=private_key,
    )
    assert envelope.envelope_id is not None
    assert envelope.agent_id == "test-agent-001"
    assert envelope.provider == "openai"
    results.success("Basic envelope creation")
except Exception as e:
    results.fail("Basic envelope creation", str(e))

# Test 1.3: Envelope with TTL (minimum 60 seconds)
try:
    envelope_with_ttl = create_envelope(
        agent_id="ttl-test-agent",
        provider="claude",
        step_number=1,
        root_policy_id="policy-002",
        skill=Skill(
            id="skill-002",
            name="temp_access",
            tool="Temporary access",
            parameters=SkillParameters(allowed=[], constraints={})
        ),
        authority=Authority(scopes=["read:temp"], resources=["*"]),
        context=Context(included=["session"], excluded=[]),
        execution=ExecutionConfig(provider_config={}),
        private_key=private_key,
        ttl_seconds=300  # 5 minutes
    )
    assert envelope_with_ttl.expires_at is not None
    results.success("Envelope with TTL")
except Exception as e:
    results.fail("Envelope with TTL", str(e))

# Test 1.4: Different providers (with valid scope format)
providers = ["openai", "claude", "gemini", "custom"]
for provider in providers:
    try:
        env = create_envelope(
            agent_id=f"{provider}-agent",
            provider=provider,
            step_number=1,
            root_policy_id="policy-multi",
            skill=Skill(id="s1", name="test", tool="Test",
                       parameters=SkillParameters(allowed=[], constraints={})),
            authority=Authority(scopes=["read:test"], resources=["*"]),  # Valid scope format
            context=Context(included=[], excluded=[]),
            execution=ExecutionConfig(provider_config={}),
            private_key=private_key,
        )
        assert env.provider == provider
        results.success(f"Provider: {provider}")
    except Exception as e:
        results.fail(f"Provider: {provider}", str(e))


# =============================================================================
# TEST 2: SIGNATURE VALIDATION
# =============================================================================
print("\n" + "="*60)
print("TEST SUITE 2: Signature Validation")
print("="*60)

# Test 2.1: Validate valid envelope (returns dict with validation info)
try:
    validation_result = validate_envelope(envelope, public_key=public_key)
    assert validation_result is not None
    assert isinstance(validation_result, dict)
    results.success("Validate valid envelope")
except Exception as e:
    results.fail("Validate valid envelope", str(e))

# Test 2.2: Verify signature directly
try:
    verified = verify_signature(envelope, public_key)
    assert verified == True
    results.success("Verify signature directly")
except Exception as e:
    results.fail("Verify signature directly", str(e))

# Test 2.3: Reject wrong public key
try:
    wrong_private, wrong_public = generate_key_pair()
    try:
        verified = verify_signature(envelope, wrong_public)
        if verified:
            results.fail("Reject wrong public key", "Should have rejected")
        else:
            results.success("Reject wrong public key")
    except InvalidSignature:
        results.success("Reject wrong public key (raised InvalidSignature)")
except Exception as e:
    results.fail("Reject wrong public key", str(e))


# =============================================================================
# TEST 3: TOOL ENFORCEMENT
# =============================================================================
print("\n" + "="*60)
print("TEST SUITE 3: Tool Enforcement")
print("="*60)

# Create fresh envelope for enforcement tests
enforcement_envelope = create_envelope(
    agent_id="enforcement-agent",
    provider="openai",
    step_number=1,
    root_policy_id="enforcement-policy",
    skill=Skill(id="s-enforce", name="data_ops", tool="Data Operations",
               parameters=SkillParameters(allowed=["user_id", "query"], constraints={})),
    authority=Authority(scopes=["read:user", "read:data"], resources=["users/*", "data/*"]),
    context=Context(included=["user_id", "query"], excluded=["password"]),
    execution=ExecutionConfig(provider_config={}),
    private_key=private_key,
)

# Test 3.1: EnforcedTool with valid scope
try:
    def get_user(user_id: str) -> dict:
        return {"id": user_id, "name": "Test User"}

    secure_get_user = EnforcedTool(
        name="get_user",
        func=get_user,
        required_scope="read:user",
        public_key=public_key,
        description="Get user by ID"
    )
    result = secure_get_user(user_id="123", _envelope=enforcement_envelope)
    assert result["id"] == "123"
    results.success("EnforcedTool with valid scope")
except Exception as e:
    results.fail("EnforcedTool with valid scope", str(e))

# Test 3.2: EnforcedTool without envelope (should fail)
try:
    try:
        result = secure_get_user(user_id="456")
        results.fail("EnforcedTool without envelope", "Should have raised PermissionDenied")
    except PermissionDenied:
        results.success("EnforcedTool without envelope (raised PermissionDenied)")
    except TypeError as e:
        if "_envelope" in str(e):
            results.success("EnforcedTool without envelope (requires _envelope)")
        else:
            raise
except Exception as e:
    results.fail("EnforcedTool without envelope", str(e))

# Test 3.3: EnforcedTool with wrong scope
try:
    def delete_user(user_id: str) -> bool:
        return True

    secure_delete = EnforcedTool(
        name="delete_user",
        func=delete_user,
        required_scope="delete:user",  # Not in envelope scopes
        public_key=public_key,
    )
    try:
        result = secure_delete(user_id="123", _envelope=enforcement_envelope)
        results.fail("EnforcedTool with wrong scope", "Should have raised PermissionDenied")
    except PermissionDenied:
        results.success("EnforcedTool with wrong scope (raised PermissionDenied)")
except Exception as e:
    results.fail("EnforcedTool with wrong scope", str(e))

# Test 3.4: EnforcedTool with invalid signature
try:
    other_private, other_public = generate_key_pair()
    bad_envelope = create_envelope(
        agent_id="bad-agent",
        provider="openai",
        step_number=1,
        root_policy_id="bad-policy",
        skill=Skill(id="s-bad", name="bad", tool="Bad",
                   parameters=SkillParameters(allowed=[], constraints={})),
        authority=Authority(scopes=["read:user"], resources=["*"]),
        context=Context(included=[], excluded=[]),
        execution=ExecutionConfig(provider_config={}),
        private_key=other_private,  # Different key!
    )
    try:
        result = secure_get_user(user_id="123", _envelope=bad_envelope)
        results.fail("EnforcedTool with invalid signature", "Should have raised error")
    except (InvalidSignature, PermissionDenied, ValidationError):
        results.success("EnforcedTool with invalid signature (rejected)")
except Exception as e:
    results.fail("EnforcedTool with invalid signature", str(e))

# Test 3.5: EnforcedToolkit with decorator pattern
try:
    toolkit = EnforcedToolkit(public_key=public_key)

    @toolkit.tool(scope="read:data", name="fetch_data")
    def fetch_data(query: str) -> str:
        return f"Data: {query}"

    # Execute through toolkit
    result = toolkit.execute("fetch_data", enforcement_envelope, query="test")
    assert "test" in result
    results.success("EnforcedToolkit decorator and execute")
except Exception as e:
    results.fail("EnforcedToolkit decorator and execute", str(e))

# Test 3.6: EnforcedToolkit scope check
try:
    scope = toolkit.get_required_scope("fetch_data")
    assert scope == "read:data"
    results.success("EnforcedToolkit get_required_scope")
except Exception as e:
    results.fail("EnforcedToolkit get_required_scope", str(e))


# =============================================================================
# TEST 4: PERSISTENCE & STORAGE
# =============================================================================
print("\n" + "="*60)
print("TEST SUITE 4: Persistence & Storage")
print("="*60)

with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
    test_db_path = f.name

try:
    # Test 4.1: Create store
    try:
        store = EnvelopeStore(test_db_path)
        results.success("Create EnvelopeStore")
    except Exception as e:
        results.fail("Create EnvelopeStore", str(e))
        store = None

    if store:
        # Test 4.2: Save envelope
        try:
            store.save_envelope(envelope)
            results.success("Save envelope to store")
        except Exception as e:
            results.fail("Save envelope to store", str(e))

        # Test 4.3: Retrieve envelope
        try:
            retrieved = store.get_envelope(envelope.envelope_id)
            assert retrieved is not None
            assert retrieved.envelope_id == envelope.envelope_id
            assert retrieved.agent_id == envelope.agent_id
            results.success("Retrieve envelope from store")
        except Exception as e:
            results.fail("Retrieve envelope from store", str(e))

        # Test 4.4: Get envelope chain
        try:
            chain = store.get_envelope_chain(envelope.envelope_id)
            assert chain is not None
            assert len(chain) >= 1
            results.success("Get envelope chain")
        except Exception as e:
            results.fail("Get envelope chain", str(e))

        # Test 4.5: Create and save audit entry
        try:
            audit = create_audit_entry(
                action="get_user",
                envelope=envelope,
                public_key=public_key,
                result="success"
            )
            store.save_audit_entry(audit)
            results.success("Create and save audit entry")
        except Exception as e:
            results.fail("Create and save audit entry", str(e))

        # Test 4.6: Query audit trail
        try:
            trail = store.get_audit_trail(agent_id=envelope.agent_id)
            assert trail is not None
            assert len(trail) >= 1
            results.success("Query audit trail by agent")
        except Exception as e:
            results.fail("Query audit trail by agent", str(e))

        # Test 4.7: Export audit trail (takes list of AuditEntry objects)
        try:
            # export_audit_trail expects List[AuditEntry], not dicts from get_audit_trail
            # Create a list of AuditEntry objects directly
            audit_entries = [audit]  # Use the audit entry we created earlier
            exported = export_audit_trail(audit_entries)
            assert exported is not None
            assert isinstance(exported, dict)
            results.success("Export audit trail")
        except Exception as e:
            results.fail("Export audit trail", str(e))

finally:
    if os.path.exists(test_db_path):
        os.unlink(test_db_path)


# =============================================================================
# TEST 5: AUTHORITY NARROWING (Manual approach since narrow_authority not exported)
# =============================================================================
print("\n" + "="*60)
print("TEST SUITE 5: Authority Narrowing (Manual)")
print("="*60)

# Create root envelope with broad permissions
root_envelope = create_envelope(
    agent_id="root-agent",
    provider="openai",
    step_number=1,
    root_policy_id="root-policy",
    skill=Skill(id="root-skill", name="all_ops", tool="All Operations",
               parameters=SkillParameters(allowed=["user_id", "email", "name", "admin"], constraints={})),
    authority=Authority(
        scopes=["read:user", "write:user", "delete:user", "admin:user"],
        resources=["users/*", "admin/*"]
    ),
    context=Context(
        included=["user_id", "email", "name", "role", "permissions"],
        excluded=["password", "ssn", "credit_card"]
    ),
    execution=ExecutionConfig(provider_config={}),
    private_key=private_key,
)

# Test 5.1: Create child envelope with subset of parent scopes
try:
    child_envelope = create_envelope(
        agent_id="child-agent",
        provider="openai",
        step_number=2,
        root_policy_id="root-policy",
        skill=Skill(id="child-skill", name="limited_ops", tool="Limited Operations",
                   parameters=SkillParameters(allowed=["user_id"], constraints={})),
        authority=Authority(
            scopes=["read:user"],  # Subset of parent
            resources=["users/*"]  # Subset of parent
        ),
        context=Context(
            included=["user_id", "email"],  # Subset of parent
            excluded=["password", "ssn", "credit_card"]
        ),
        execution=ExecutionConfig(provider_config={}),
        private_key=private_key,
        parent_envelope_id=root_envelope.envelope_id,
    )
    assert child_envelope.parent_envelope_id == root_envelope.envelope_id
    assert child_envelope.step_number == 2
    results.success("Create child envelope with narrowed authority")
except Exception as e:
    results.fail("Create child envelope with narrowed authority", str(e))

# Test 5.2: Validate parent-child chain
try:
    validation = validate_envelope(child_envelope, parent_envelope=root_envelope, public_key=public_key)
    assert validation is not None
    results.success("Validate parent-child envelope chain")
except Exception as e:
    results.fail("Validate parent-child envelope chain", str(e))

# Test 5.3: Child envelope enforcement respects narrowed scope
try:
    def admin_action() -> str:
        return "Admin action performed"

    secure_admin = EnforcedTool(
        name="admin_action",
        func=admin_action,
        required_scope="admin:user",  # Not in child's scope
        public_key=public_key,
    )

    try:
        result = secure_admin(_envelope=child_envelope)
        results.fail("Child cannot exceed parent scope", "Should have raised PermissionDenied")
    except PermissionDenied:
        results.success("Child cannot exceed parent scope (PermissionDenied)")
except Exception as e:
    results.fail("Child cannot exceed parent scope", str(e))


# =============================================================================
# TEST 6: ENVELOPE EXPIRATION
# =============================================================================
print("\n" + "="*60)
print("TEST SUITE 6: Envelope Expiration")
print("="*60)

# Test 6.1: Minimum TTL enforcement (60 seconds minimum)
try:
    try:
        invalid_ttl_envelope = create_envelope(
            agent_id="invalid-ttl-agent",
            provider="openai",
            step_number=1,
            root_policy_id="invalid-ttl-policy",
            skill=Skill(id="s-inv", name="invalid", tool="Invalid",
                       parameters=SkillParameters(allowed=[], constraints={})),
            authority=Authority(scopes=["read:data"], resources=["*"]),
            context=Context(included=[], excluded=[]),
            execution=ExecutionConfig(provider_config={}),
            private_key=private_key,
            ttl_seconds=10  # Too short!
        )
        results.fail("Minimum TTL enforcement", "Should have rejected TTL < 60")
    except (ValueError, ValidationError) as e:
        results.success("Minimum TTL enforcement (rejected < 60s)")
except Exception as e:
    results.fail("Minimum TTL enforcement", str(e))

# Test 6.2: Valid minimum TTL (60 seconds)
try:
    min_ttl_envelope = create_envelope(
        agent_id="min-ttl-agent",
        provider="openai",
        step_number=1,
        root_policy_id="min-ttl-policy",
        skill=Skill(id="s-min", name="min", tool="Min",
                   parameters=SkillParameters(allowed=[], constraints={})),
        authority=Authority(scopes=["read:data"], resources=["*"]),
        context=Context(included=[], excluded=[]),
        execution=ExecutionConfig(provider_config={}),
        private_key=private_key,
        ttl_seconds=60  # Minimum valid
    )
    assert min_ttl_envelope.expires_at is not None
    results.success("Valid minimum TTL (60s)")
except Exception as e:
    results.fail("Valid minimum TTL (60s)", str(e))


# =============================================================================
# TEST 7: CONTEXT FIELD CHECKING
# =============================================================================
print("\n" + "="*60)
print("TEST SUITE 7: Context Field Checking")
print("="*60)

context_envelope = create_envelope(
    agent_id="context-agent",
    provider="openai",
    step_number=1,
    root_policy_id="context-policy",
    skill=Skill(id="s-ctx", name="context_test", tool="Context Test",
               parameters=SkillParameters(allowed=[], constraints={})),
    authority=Authority(scopes=["read:user"], resources=["*"]),
    context=Context(included=["user_id", "email"], excluded=["password", "ssn"]),
    execution=ExecutionConfig(provider_config={}),
    private_key=private_key,
)

# Test 7.1: Check allowed context field (should not raise)
try:
    check_context_field(context_envelope, "email")
    results.success("Check allowed context field (no exception)")
except PermissionDenied:
    results.fail("Check allowed context field", "Should not have raised for allowed field")
except Exception as e:
    results.fail("Check allowed context field", str(e))

# Test 7.2: Check excluded context field (should raise)
try:
    try:
        check_context_field(context_envelope, "password")
        results.fail("Check excluded context field", "Should have raised for excluded field")
    except PermissionDenied:
        results.success("Check excluded context field (raised PermissionDenied)")
except Exception as e:
    results.fail("Check excluded context field", str(e))

# Test 7.3: check_envelope with required scope
try:
    check_envelope(context_envelope, public_key, required_scope="read:user")
    results.success("check_envelope with valid scope")
except Exception as e:
    results.fail("check_envelope with valid scope", str(e))

# Test 7.4: check_envelope with invalid scope
try:
    try:
        check_envelope(context_envelope, public_key, required_scope="write:admin")
        results.fail("check_envelope with invalid scope", "Should have raised")
    except PermissionDenied:
        results.success("check_envelope with invalid scope (raised PermissionDenied)")
except Exception as e:
    results.fail("check_envelope with invalid scope", str(e))


# =============================================================================
# TEST 8: EDGE CASES & VALIDATION
# =============================================================================
print("\n" + "="*60)
print("TEST SUITE 8: Edge Cases & Validation")
print("="*60)

# Test 8.1: Scope format validation (must be action:resource)
try:
    try:
        bad_scope_envelope = create_envelope(
            agent_id="bad-scope-agent",
            provider="openai",
            step_number=1,
            root_policy_id="bad-scope-policy",
            skill=Skill(id="s-bad", name="bad", tool="Bad",
                       parameters=SkillParameters(allowed=[], constraints={})),
            authority=Authority(scopes=["invalid"], resources=["*"]),  # Invalid format
            context=Context(included=[], excluded=[]),
            execution=ExecutionConfig(provider_config={}),
            private_key=private_key,
        )
        results.fail("Scope format validation", "Should reject invalid scope format")
    except (ValueError, ValidationError):
        results.success("Scope format validation (rejected 'invalid')")
except Exception as e:
    results.fail("Scope format validation", str(e))

# Test 8.2: Empty scopes validation
try:
    try:
        empty_scope_envelope = create_envelope(
            agent_id="empty-scope-agent",
            provider="openai",
            step_number=1,
            root_policy_id="empty-policy",
            skill=Skill(id="s-empty", name="empty", tool="Empty",
                       parameters=SkillParameters(allowed=[], constraints={})),
            authority=Authority(scopes=[], resources=[]),
            context=Context(included=[], excluded=[]),
            execution=ExecutionConfig(provider_config={}),
            private_key=private_key,
        )
        results.fail("Empty scopes validation", "Should reject empty scopes")
    except (ValueError, ValidationError):
        results.success("Empty scopes validation (rejected)")
except Exception as e:
    results.fail("Empty scopes validation", str(e))

# Test 8.3: Valid scope with path-style resource
try:
    path_envelope = create_envelope(
        agent_id="path-agent",
        provider="openai",
        step_number=1,
        root_policy_id="path-policy",
        skill=Skill(id="s-path", name="path", tool="Path",
                   parameters=SkillParameters(allowed=[], constraints={})),
        authority=Authority(
            scopes=["read:users", "write:documents"],
            resources=["users/*/profile", "documents/**"]
        ),
        context=Context(included=[], excluded=[]),
        execution=ExecutionConfig(provider_config={}),
        private_key=private_key,
    )
    results.success("Scope with path-style resource")
except Exception as e:
    results.fail("Scope with path-style resource", str(e))

# Test 8.4: Step number progression
try:
    for step in [1, 2, 3, 10, 100]:
        step_envelope = create_envelope(
            agent_id="step-agent",
            provider="openai",
            step_number=step,
            root_policy_id="step-policy",
            skill=Skill(id="s-step", name="step", tool="Step",
                       parameters=SkillParameters(allowed=[], constraints={})),
            authority=Authority(scopes=["read:test"], resources=["*"]),
            context=Context(included=[], excluded=[]),
            execution=ExecutionConfig(provider_config={}),
            private_key=private_key,
        )
        assert step_envelope.step_number == step
    results.success("Step number progression")
except Exception as e:
    results.fail("Step number progression", str(e))

# Test 8.5: Multiple scopes with various actions
try:
    multi_scope_envelope = create_envelope(
        agent_id="multi-scope-agent",
        provider="openai",
        step_number=1,
        root_policy_id="multi-policy",
        skill=Skill(id="s-multi", name="multi", tool="Multi",
                   parameters=SkillParameters(allowed=[], constraints={})),
        authority=Authority(
            scopes=["read:users", "write:users", "delete:users", "list:users", "create:users"],
            resources=["users/*"]
        ),
        context=Context(included=["id", "name", "email"], excluded=["password"]),
        execution=ExecutionConfig(provider_config={}),
        private_key=private_key,
    )
    assert len(multi_scope_envelope.authority.scopes) == 5
    results.success("Multiple scopes with various actions")
except Exception as e:
    results.fail("Multiple scopes with various actions", str(e))


# =============================================================================
# TEST 9: MINIMAL WORKING EXAMPLE FROM DOCS
# =============================================================================
print("\n" + "="*60)
print("TEST SUITE 9: Minimal Working Example")
print("="*60)

try:
    # 1. Setup
    doc_private_key, doc_public_key = generate_key_pair()

    # 2. Create envelope
    doc_envelope = create_envelope(
        agent_id="test-agent",
        provider="openai",
        step_number=1,
        root_policy_id="test-policy",
        skill=Skill(id="s1", name="read", tool="Read",
                    parameters=SkillParameters(allowed=[], constraints={})),
        authority=Authority(scopes=["read:data"], resources=["*"]),
        context=Context(included=["query"], excluded=[]),
        execution=ExecutionConfig(provider_config={}),
        private_key=doc_private_key
    )

    # 3. Wrap function
    def fetch_data(query: str) -> str:
        return f"Data for: {query}"

    secure_fetch = EnforcedTool(
        name="fetch_data",
        func=fetch_data,
        required_scope="read:data",
        public_key=doc_public_key
    )

    # 4. Execute with enforcement
    result = secure_fetch(query="users", _envelope=doc_envelope)
    assert result == "Data for: users"
    results.success("Minimal working example from docs")
except Exception as e:
    results.fail("Minimal working example from docs", str(e))


# =============================================================================
# FINAL SUMMARY
# =============================================================================
print("\n")
success = results.summary()
if success:
    print("\nAll tests passed!")
else:
    print("\nSome tests failed. Review output above.")
    exit(1)
