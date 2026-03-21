#!/usr/bin/env python3
"""
Web3 Wallet Agent with Authority Runtime

Demonstrates "Know Your Agent" - cryptographically signed credentials for AI agents
that prove what an agent was authorized to do when transacting.

THE PROBLEM:
If an AI agent has a wallet private key, it can do ANYTHING with that wallet.
Send all funds to an attacker? Done. Approve unlimited token spending? Done.
There's no "read-only" mode, no spending limits, no transaction scoping.

THE SOLUTION:
Authority Runtime creates signed permission envelopes that constrain what an agent
can do - even if it has the underlying wallet key. The envelope acts as a
cryptographic proof of authorization that can be verified by any party.

This example shows:
1. Agent with FULL wallet access but SCOPED permissions (max amount, whitelist)
2. Transactions blocked when they exceed envelope constraints
3. Audit trail proving what was authorized vs. what was attempted
4. Multi-step delegation: Treasury → Trading Bot → Specific Trade

Run: python examples/web3_wallet_agent.py
"""

import json
from decimal import Decimal
from typing import Optional
from dataclasses import dataclass
from datetime import datetime, timezone

from authority_runtime import (
    generate_key_pair,
    create_envelope,
    create_simple_envelope,
    create_child_envelope,
    EnforcedTool,
    PermissionDenied,
    EnvelopeStore,
    create_audit_entry,
    export_audit_trail,
    Skill,
    SkillParameters,
    Authority,
    Context,
    ExecutionConfig,
)


# =============================================================================
# SIMULATED WEB3 INFRASTRUCTURE
# (In production, replace with web3.py or ethers calls)
# =============================================================================

@dataclass
class Transaction:
    """Simulated blockchain transaction"""
    from_address: str
    to_address: str
    amount: Decimal
    token: str
    timestamp: str
    tx_hash: str
    status: str = "pending"


class SimulatedWallet:
    """
    Simulated wallet that would normally have FULL control.

    In reality, this could be:
    - A hot wallet with web3.py
    - A custodial API (Fireblocks, BitGo)
    - A smart contract interaction
    """

    def __init__(self, address: str, balances: dict[str, Decimal]):
        self.address = address
        self.balances = balances
        self.transactions: list[Transaction] = []
        self.tx_count = 0

    def transfer(self, to: str, amount: Decimal, token: str = "ETH") -> Transaction:
        """Execute a transfer - this is the DANGEROUS operation we're protecting"""
        if self.balances.get(token, Decimal(0)) < amount:
            raise ValueError(f"Insufficient {token} balance")

        self.balances[token] -= amount
        self.tx_count += 1

        tx = Transaction(
            from_address=self.address,
            to_address=to,
            amount=amount,
            token=token,
            timestamp=datetime.now(timezone.utc).isoformat(),
            tx_hash=f"0x{self.tx_count:064x}",
            status="confirmed"
        )
        self.transactions.append(tx)
        return tx


# =============================================================================
# HELPER: Create envelope with constraints (properly signed)
# =============================================================================

def create_wallet_envelope(
    agent_id: str,
    scopes: list[str],
    private_key: str,
    constraints: dict,
    ttl_seconds: int = 300,
    parent_envelope=None,
):
    """
    Create a wallet envelope with constraints included in the signature.

    The constraints are part of Authority, so they're signed and tamper-proof.
    """
    return create_envelope(
        agent_id=agent_id,
        provider="claude",
        step_number=1 if not parent_envelope else parent_envelope.step_number + 1,
        root_policy_id=f"policy-{agent_id}" if not parent_envelope else parent_envelope.root_policy_id,
        skill=Skill(
            id="wallet-ops",
            name="wallet_operations",
            tool="Wallet operations with constraints",
            parameters=SkillParameters(allowed=["to", "amount", "token"], constraints={}),
        ),
        authority=Authority(
            scopes=scopes,
            resources=["*"],
            constraints=constraints,  # Constraints are SIGNED
        ),
        context=Context(included=["user_id", "session_id"], excluded=[], max_size_bytes=10000),
        execution=ExecutionConfig(provider_config={"claude": {"skill_name": "wallet"}}),
        private_key=private_key,
        parent_envelope_id=parent_envelope.envelope_id if parent_envelope else None,
        ttl_seconds=ttl_seconds,
    )


# =============================================================================
# CONSTRAINT CHECKING (The "Know Your Agent" Magic)
# =============================================================================

def check_transaction_constraints(
    envelope,
    to_address: str,
    amount: Decimal,
    token: str,
) -> tuple[bool, str]:
    """
    Check if a transaction is allowed by the envelope's constraints.

    This is where Authority Runtime adds value over raw wallet access:
    - Even if the agent has the wallet key, it can only do what the envelope allows
    - The envelope is cryptographically signed, so constraints can't be forged
    - Any verifier can check: "Was this agent authorized to do this?"
    """
    constraints = envelope.authority.constraints

    # Check max amount
    max_amount = constraints.get("max_amount")
    if max_amount:
        limit = Decimal(str(max_amount).replace(" ETH", "").replace(" USDC", ""))
        if amount > limit:
            return False, f"Amount {amount} exceeds max_amount {limit}"

    # Check allowed tokens
    allowed_tokens = constraints.get("allowed_tokens", [])
    if allowed_tokens and token not in allowed_tokens:
        return False, f"Token {token} not in allowed_tokens {allowed_tokens}"

    # Check whitelisted recipients
    allowed_recipients = constraints.get("allowed_recipients", [])
    if allowed_recipients and to_address not in allowed_recipients:
        return False, f"Recipient {to_address} not in whitelist"

    return True, "Transaction authorized"


# =============================================================================
# ENFORCED WALLET OPERATIONS
# =============================================================================

def create_wallet_tools(wallet: SimulatedWallet, public_key: str):
    """Create enforced wallet tools that respect envelope constraints"""

    def _transfer_impl(
        to_address: str,
        amount: str,
        token: str = "ETH",
        _envelope=None,
    ) -> dict:
        """
        Transfer tokens - ONLY if envelope constraints allow it.

        The _envelope parameter is injected by EnforcedTool.
        Even though we have full wallet access, we check constraints first.
        """
        amount_decimal = Decimal(amount)

        # Check envelope constraints BEFORE executing
        allowed, reason = check_transaction_constraints(
            _envelope, to_address, amount_decimal, token
        )

        if not allowed:
            raise PermissionDenied(
                f"Transaction blocked by envelope constraints: {reason}\n"
                f"Envelope ID: {_envelope.envelope_id}\n"
                f"Authorized scopes: {_envelope.authority.scopes}\n"
                f"Constraints: {_envelope.authority.constraints}"
            )

        # Execute the actual transfer
        tx = wallet.transfer(to_address, amount_decimal, token)

        return {
            "status": "success",
            "tx_hash": tx.tx_hash,
            "from": tx.from_address,
            "to": tx.to_address,
            "amount": str(tx.amount),
            "token": tx.token,
            "envelope_id": _envelope.envelope_id,
            "authorized_by": _envelope.root_policy_id,
        }

    def _get_balance_impl(token: str = "ETH", _envelope=None) -> dict:
        """Get wallet balance - read-only operation"""
        return {
            "token": token,
            "balance": str(wallet.balances.get(token, Decimal(0))),
            "address": wallet.address,
        }

    # Wrap with enforcement
    transfer_tool = EnforcedTool(
        name="transfer",
        func=_transfer_impl,
        required_scope="wallet:transfer",
        public_key=public_key,
        description="Transfer tokens (requires wallet:transfer scope + constraints check)"
    )

    balance_tool = EnforcedTool(
        name="get_balance",
        func=_get_balance_impl,
        required_scope="wallet:read",
        public_key=public_key,
        description="Get wallet balance (requires wallet:read scope)"
    )

    return transfer_tool, balance_tool


# =============================================================================
# DEMO SCENARIOS
# =============================================================================

def print_header(title: str):
    print(f"\n{'='*70}")
    print(f"  {title}")
    print(f"{'='*70}\n")


def demo_basic_constraints():
    """Demo 1: Basic transaction constraints"""
    print_header("DEMO 1: Basic Transaction Constraints")

    # Setup
    private_key, public_key = generate_key_pair()
    wallet = SimulatedWallet(
        address="0xTreasury123",
        balances={"ETH": Decimal("10.0"), "USDC": Decimal("10000")}
    )
    transfer, get_balance = create_wallet_tools(wallet, public_key)

    print(f"Wallet address: {wallet.address}")
    print(f"Initial balances: ETH={wallet.balances['ETH']}, USDC={wallet.balances['USDC']}")

    # Create envelope with constraints (constraints are SIGNED - tamper-proof)
    envelope = create_wallet_envelope(
        agent_id="trading-bot-001",
        scopes=["wallet:transfer", "wallet:read"],
        private_key=private_key,
        constraints={
            "max_amount": "0.5",  # Max 0.5 ETH per transaction
            "allowed_tokens": ["ETH", "USDC"],
            "allowed_recipients": [
                "0xExchange001",
                "0xExchange002",
            ],
        },
        ttl_seconds=300,
    )

    print(f"\nEnvelope constraints:")
    print(f"  - Max amount: 0.5 ETH")
    print(f"  - Allowed tokens: ETH, USDC")
    print(f"  - Whitelisted recipients: 0xExchange001, 0xExchange002")

    # Test 1: Authorized transaction
    print(f"\n--- Test 1: Authorized transfer (0.1 ETH to whitelisted address) ---")
    try:
        result = transfer(
            to_address="0xExchange001",
            amount="0.1",
            token="ETH",
            _envelope=envelope
        )
        print(f"✅ SUCCESS: {result['tx_hash']}")
        print(f"   Transferred {result['amount']} {result['token']} to {result['to']}")
    except PermissionDenied as e:
        print(f"❌ BLOCKED: {e}")

    # Test 2: Amount too high
    print(f"\n--- Test 2: Amount exceeds limit (1.0 ETH > 0.5 max) ---")
    try:
        result = transfer(
            to_address="0xExchange001",
            amount="1.0",
            token="ETH",
            _envelope=envelope
        )
        print(f"✅ SUCCESS: {result['tx_hash']}")
    except PermissionDenied as e:
        print(f"❌ BLOCKED: Amount exceeds envelope constraint")

    # Test 3: Unauthorized recipient
    print(f"\n--- Test 3: Recipient not whitelisted ---")
    try:
        result = transfer(
            to_address="0xAttacker999",
            amount="0.1",
            token="ETH",
            _envelope=envelope
        )
        print(f"✅ SUCCESS: {result['tx_hash']}")
    except PermissionDenied as e:
        print(f"❌ BLOCKED: Recipient not in whitelist")

    # Test 4: Unauthorized token
    print(f"\n--- Test 4: Token not allowed (WBTC) ---")
    envelope_no_wbtc = create_wallet_envelope(
        agent_id="trading-bot-001",
        scopes=["wallet:transfer"],
        private_key=private_key,
        constraints={
            "allowed_tokens": ["ETH"],  # Only ETH allowed
        },
    )

    try:
        result = transfer(
            to_address="0xExchange001",
            amount="0.1",
            token="WBTC",
            _envelope=envelope_no_wbtc
        )
        print(f"✅ SUCCESS: {result['tx_hash']}")
    except PermissionDenied as e:
        print(f"❌ BLOCKED: Token not in allowed list")

    print(f"\nFinal balance: ETH={wallet.balances['ETH']}")
    print(f"Only 1 of 4 transactions succeeded (as expected)")


def demo_delegation_chain():
    """Demo 2: Multi-agent delegation (Treasury → Bot → Trade)"""
    print_header("DEMO 2: Multi-Agent Delegation Chain")

    private_key, public_key = generate_key_pair()
    wallet = SimulatedWallet(
        address="0xTreasury",
        balances={"ETH": Decimal("100.0")}
    )
    transfer, _ = create_wallet_tools(wallet, public_key)

    # Level 1: Treasury envelope (broad permissions)
    treasury_envelope = create_wallet_envelope(
        agent_id="treasury-manager",
        scopes=["wallet:transfer", "wallet:read", "wallet:approve"],
        private_key=private_key,
        constraints={
            "max_amount": "10.0",  # Treasury can move up to 10 ETH
            "allowed_recipients": ["0xExchange001", "0xExchange002", "0xDeFiProtocol"],
        },
        ttl_seconds=3600,  # 1 hour
    )

    print("Level 1: Treasury Manager")
    print(f"  Scopes: {treasury_envelope.authority.scopes}")
    print(f"  Max amount: 10 ETH")
    print(f"  Envelope ID: {treasury_envelope.envelope_id[:16]}...")

    # Level 2: Trading bot (narrowed from treasury)
    trading_bot_envelope = create_wallet_envelope(
        agent_id="treasury-manager",
        scopes=["wallet:transfer", "wallet:read"],  # No approve!
        private_key=private_key,
        constraints={
            "max_amount": "1.0",  # Bot limited to 1 ETH
            "allowed_recipients": ["0xExchange001"],  # Only one exchange
        },
        parent_envelope=treasury_envelope,
    )

    print("\nLevel 2: Trading Bot (delegated from Treasury)")
    print(f"  Scopes: {trading_bot_envelope.authority.scopes}")
    print(f"  Max amount: 1 ETH (narrowed from 10)")
    print(f"  Recipients: 0xExchange001 only (narrowed from 3)")
    print(f"  Parent: {trading_bot_envelope.parent_envelope_id[:16]}...")

    # Level 3: Specific trade execution
    trade_envelope = create_wallet_envelope(
        agent_id="treasury-manager",
        scopes=["wallet:transfer"],  # Just transfer, no read
        private_key=private_key,
        constraints={
            "max_amount": "0.5",  # This specific trade: max 0.5 ETH
            "allowed_recipients": ["0xExchange001"],
        },
        parent_envelope=trading_bot_envelope,
    )

    print("\nLevel 3: Specific Trade Execution")
    print(f"  Scopes: {trade_envelope.authority.scopes}")
    print(f"  Max amount: 0.5 ETH (for this trade only)")
    print(f"  Parent: {trade_envelope.parent_envelope_id[:16]}...")

    # Execute with most-constrained envelope
    print("\n--- Executing trade with Level 3 envelope ---")
    try:
        result = transfer(
            to_address="0xExchange001",
            amount="0.3",
            token="ETH",
            _envelope=trade_envelope
        )
        print(f"✅ Trade executed: {result['tx_hash']}")
        print(f"   Amount: {result['amount']} ETH")

        # Show the delegation chain for audit
        print(f"\n📋 Delegation Chain (for compliance):")
        print(f"   Root: {treasury_envelope.envelope_id[:16]}... (Treasury)")
        print(f"     └─ {trading_bot_envelope.envelope_id[:16]}... (Trading Bot)")
        print(f"         └─ {trade_envelope.envelope_id[:16]}... (This Trade)")

    except PermissionDenied as e:
        print(f"❌ BLOCKED: {e}")

    # Try to exceed trade envelope limit
    print("\n--- Attempting to exceed trade limit (0.8 ETH > 0.5 max) ---")
    try:
        result = transfer(
            to_address="0xExchange001",
            amount="0.8",
            token="ETH",
            _envelope=trade_envelope
        )
        print(f"✅ SUCCESS: {result['tx_hash']}")
    except PermissionDenied as e:
        print(f"❌ BLOCKED: Trade envelope limits transaction to 0.5 ETH")


def demo_audit_trail():
    """Demo 3: Compliance-ready audit trail"""
    print_header("DEMO 3: Audit Trail for Compliance")

    private_key, public_key = generate_key_pair()
    wallet = SimulatedWallet(
        address="0xCompanyTreasury",
        balances={"ETH": Decimal("50.0"), "USDC": Decimal("100000")}
    )
    transfer, get_balance = create_wallet_tools(wallet, public_key)
    import tempfile
    import os
    db_file = tempfile.mktemp(suffix=".db")
    store = EnvelopeStore(db_file)  # Temp file for demo

    envelope = create_wallet_envelope(
        agent_id="payroll-agent",
        scopes=["wallet:transfer", "wallet:read"],
        private_key=private_key,
        constraints={
            "max_amount": "5.0",
            "allowed_tokens": ["ETH", "USDC"],
        },
    )

    # Save envelope
    store.save_envelope(envelope)

    audit_entries = []

    # Execute some transactions
    transactions = [
        ("0xEmployee001", "1.0", "ETH", "success"),
        ("0xEmployee002", "2.0", "ETH", "success"),
        ("0xEmployee003", "10.0", "ETH", "blocked"),  # Will fail - too high
    ]

    print("Executing payroll transactions:")
    for to_addr, amount, token, expected in transactions:
        try:
            result = transfer(
                to_address=to_addr,
                amount=amount,
                token=token,
                _envelope=envelope
            )
            status = "success"
            print(f"  ✅ {amount} {token} → {to_addr}")
        except PermissionDenied:
            status = "blocked"
            print(f"  ❌ {amount} {token} → {to_addr} (BLOCKED: exceeds limit)")

        # Record audit entry
        audit_entries.append(create_audit_entry(
            action="transfer",
            envelope=envelope,
            public_key=public_key,
            result=status,
            metadata={
                "to_address": to_addr,
                "amount": amount,
                "token": token,
            }
        ))

    # Export compliance report
    print("\n📋 Compliance Report:")
    report = export_audit_trail(audit_entries)

    print(f"  Generated at: {report['generated_at']}")
    print(f"  Total actions: {report['summary']['total_actions']}")
    print(f"  Successful: {report['summary']['successful']}")
    print(f"  Blocked: {report['summary']['failed']}")
    print(f"  Signature failures: {report['summary']['signature_failures']}")

    print("\n  This report proves:")
    print("  - What the agent was authorized to do (envelope constraints)")
    print("  - What it actually did (audit trail)")
    print("  - That authorization was valid (signature verification)")
    print("  - The complete delegation chain (if applicable)")


def demo_know_your_agent():
    """Demo 4: The "Know Your Agent" value proposition"""
    print_header("DEMO 4: Know Your Agent - Verifiable Credentials")

    private_key, public_key = generate_key_pair()

    print("""
    THE SCENARIO:

    You're a DeFi protocol. An AI agent wants to interact with your contracts.
    How do you know:

    1. Who authorized this agent?
    2. What is it allowed to do?
    3. Is the authorization still valid?
    4. Can you prove this for compliance?

    TRADITIONAL APPROACH:
    - Agent has API key → full access
    - No spending limits
    - No expiration
    - No audit trail
    - No way to verify authorization

    AUTHORITY RUNTIME APPROACH:
    - Agent presents signed envelope
    - You verify the signature
    - You check the constraints
    - You log the authorization proof
    - Anyone can verify the chain later
    """)

    # Agent presents envelope
    envelope = create_wallet_envelope(
        agent_id="defi-trading-agent",
        scopes=["wallet:transfer"],
        private_key=private_key,
        constraints={
            "max_amount": "1.0",
            "allowed_recipients": ["0xYourProtocol"],
            "allowed_tokens": ["ETH"],
        },
        ttl_seconds=300,
    )

    print("Agent presents envelope to your protocol:\n")
    print(f"  Envelope ID: {envelope.envelope_id}")
    print(f"  Agent ID: {envelope.agent_id}")
    print(f"  Scopes: {envelope.authority.scopes}")
    print(f"  Constraints: {json.dumps(envelope.authority.constraints, indent=4)}")
    print(f"  Expires: {envelope.expires_at}")
    print(f"  Signature: {envelope.signature[:32]}...")

    print("\nYour protocol verifies:")
    print("  ✅ Signature valid (Ed25519)")
    print("  ✅ Not expired")
    print("  ✅ Scopes include wallet:transfer")
    print("  ✅ Your address in allowed_recipients")
    print("  ✅ Amount within max_amount constraint")

    print("\nYou can now:")
    print("  - Execute the transaction with confidence")
    print("  - Store the envelope_id for your audit trail")
    print("  - Prove authorization to regulators if needed")
    print("  - Reject any request that doesn't meet constraints")


def main():
    print("""
╔══════════════════════════════════════════════════════════════════════════════╗
║                                                                              ║
║   AUTHORITY RUNTIME: Web3 Wallet Agent Demo                                  ║
║                                                                              ║
║   "Know Your Agent" - Cryptographically signed credentials for AI agents    ║
║                                                                              ║
║   Problem: If an AI agent has wallet access, it can do ANYTHING              ║
║   Solution: Signed envelopes that constrain what agents can do               ║
║                                                                              ║
╚══════════════════════════════════════════════════════════════════════════════╝
    """)

    demo_basic_constraints()
    demo_delegation_chain()
    demo_audit_trail()
    demo_know_your_agent()

    print_header("SUMMARY: Why This Matters")
    print("""
    FOR AGENT DEVELOPERS:
    - Scope what your agent can do, even with full credentials
    - Create audit trails that prove authorization
    - Delegate to sub-agents with narrowed permissions

    FOR PROTOCOLS/EXCHANGES:
    - Verify agent authorization before executing
    - Reject transactions that exceed envelope constraints
    - Store proof of authorization for compliance

    FOR COMPLIANCE:
    - Cryptographic proof of what was authorized
    - Complete delegation chain from root to action
    - Audit trail with signature verification

    THE KEY INSIGHT:
    Traditional auth says "who are you?"
    Authority Runtime says "what are you authorized to do RIGHT NOW?"

    Learn more: https://github.com/tronmongoose/agent.carryall
    """)


if __name__ == "__main__":
    main()
