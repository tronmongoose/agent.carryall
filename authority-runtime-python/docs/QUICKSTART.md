# Getting Started with Authority Runtime

Cryptographic IAM for AI agents -- scoped, signed, time-limited permissions with a tamper-evident audit trail.

## Prerequisites

```bash
pip install authority-runtime
```

Requires Python 3.9+. No external services needed -- everything runs locally.

---

## Example 1: Hello World

Generate a keypair, create a scoped envelope, and verify access.

```python
from authority_runtime import generate_key_pair, create_simple_envelope, check_envelope

# Generate Ed25519 identity
private_key, public_key = generate_key_pair()

# Create a scoped, signed, time-limited envelope
envelope = create_simple_envelope(
    agent_id="my-agent",
    scopes=["read:users", "write:users"],
    private_key=private_key,
)

# Check: does this envelope allow read:users?
check_envelope(envelope, public_key, required_scope="read:users")
print("Access granted: read:users")

# Check: does it allow delete:users? (it shouldn't)
try:
    check_envelope(envelope, public_key, required_scope="delete:users")
except Exception as e:
    print(f"Access denied: {e}")
```

---

## Example 2: EnforcedTool -- Runtime Blocking

Wrap functions so they refuse to execute without a valid envelope.

```python
from authority_runtime import (
    generate_key_pair, create_simple_envelope,
    EnforcedTool, PermissionDenied,
)

private_key, public_key = generate_key_pair()

envelope = create_simple_envelope(
    agent_id="support-bot",
    scopes=["read:users"],
    private_key=private_key,
)

# Wrap a function with enforcement
def lookup_user(user_id: str) -> str:
    return f"User {user_id}: Jane Doe"

secure_lookup = EnforcedTool(
    name="lookup_user",
    func=lookup_user,
    required_scope="read:users",
    public_key=public_key,
)

# Works -- envelope has read:users
result = secure_lookup(user_id="123", _envelope=envelope)
print(result)  # "User 123: Jane Doe"

# Wrap a dangerous function
def delete_user(user_id: str) -> str:
    return f"Deleted {user_id}"

secure_delete = EnforcedTool(
    name="delete_user",
    func=delete_user,
    required_scope="delete:users",
    public_key=public_key,
)

# Blocked -- envelope only has read:users, not delete:users
try:
    secure_delete(user_id="123", _envelope=envelope)
except PermissionDenied as e:
    print(f"Blocked: {e}")
```

---

## Example 3: Audit Trail and Compliance Report

Log authorized actions into a tamper-evident hash chain, then generate an HTML report.

```python
from authority_runtime import (
    generate_key_pair, create_simple_envelope, check_envelope,
    create_audit_entry, EnvelopeStore,
)
from authority_runtime.compliance import ComplianceReport
import tempfile, os, webbrowser

private_key, public_key = generate_key_pair()

envelope = create_simple_envelope(
    agent_id="academic-advisor",
    scopes=["vault:students:read"],
    private_key=private_key,
)

# Create a temporary database
db_path = os.path.join(tempfile.mkdtemp(), "demo.db")
store = EnvelopeStore(db_path)
store.save_envelope(envelope)

# Log some authorized actions
for student in ["alice", "bob", "carol"]:
    check_envelope(envelope, public_key, required_scope="vault:students:read")
    entry = create_audit_entry(
        action="read",
        envelope=envelope,
        public_key=public_key,
        result="success",
        resource=f"slos://vaults/students/{student}",
    )
    store.save_audit_entry(entry)

# Verify the tamper-evident audit chain
integrity = store.verify_audit_chain()
print(f"Audit integrity: {integrity['valid']} ({integrity['entries_checked']} entries)")

# Generate HTML compliance report
report_obj = ComplianceReport(store)
full_report = report_obj.generate_full_report(title="Student Records Policy")
html = report_obj.render_html(full_report)
report_path = os.path.join(tempfile.mkdtemp(), "compliance.html")
with open(report_path, "w") as f:
    f.write(html)
print(f"Report saved to: {report_path}")
webbrowser.open(f"file://{report_path}")
```

---

## Next Steps

- **EdTech Demo**: See `demo/` for a full FERPA-compliant multi-agent system with negative attestation
- **API Reference**: Explore `EnforcedToolkit` for decorator-style tool collections, `create_child_envelope` for delegated authority narrowing, and `ComplianceReport.negative_attestation` for proving agents never touched sensitive data
- **Deployment**: Use `EnvelopeStore` with a persistent SQLite path and `AgentKeyStore` for production key management
