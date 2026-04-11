"""
Dynamic Role System for Carryall

Roles define reusable permission patterns that can be:
1. Matched against intents using semantic similarity or keywords
2. Cached for fast lookups on common requests
3. Dynamically created and updated without code changes

Architecture:
- RoleDefinition: A named set of scopes + intent patterns
- RoleStore: Persists roles to SQLite for dynamic updates
- IntentMatcher: Matches user intents to roles (keyword + optional LLM fallback)
- RoleCache: LRU cache for recent intent->role mappings

This solves:
- "compliance report" should map to [vault:finance:read, vault:hr:read], not audit:read
- Ambiguous requests should map to a "default" or "shared" role
- Common patterns are cached to avoid LLM calls
"""

import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


@dataclass
class RoleDefinition:
    """
    A named role with scopes and intent patterns.

    Example:
        RoleDefinition(
            id="finance-reader",
            name="Finance Reader",
            description="Read access to finance vault",
            scopes=["vault:finance:read"],
            intent_patterns=["finance", "revenue", "budget", "quarterly report", "q[1-4]"],
            priority=10,  # Higher priority = matched first
        )
    """
    id: str
    name: str
    description: str
    scopes: list[str]
    intent_patterns: list[str] = field(default_factory=list)  # Keywords/regex patterns
    priority: int = 0  # Higher = matched first when multiple roles match
    requires_approval: bool = False  # If true, actions need human approval
    metadata: dict = field(default_factory=dict)

    def matches_intent(self, intent: str) -> tuple[bool, float]:
        """
        Check if this role matches an intent.

        Returns (matches, confidence) where confidence is 0.0-1.0
        """
        intent_lower = intent.lower()

        matches = 0
        total_patterns = len(self.intent_patterns)

        if total_patterns == 0:
            return False, 0.0

        for pattern in self.intent_patterns:
            pattern_lower = pattern.lower()

            # Try regex first
            try:
                if re.search(pattern_lower, intent_lower):
                    matches += 1
                    continue
            except re.error:
                pass

            # Fall back to substring match
            if pattern_lower in intent_lower:
                matches += 1

        if matches == 0:
            return False, 0.0

        # Confidence based on how many patterns matched
        confidence = min(0.95, 0.5 + (matches / total_patterns) * 0.45)
        return True, confidence


# Built-in roles for common patterns
BUILTIN_ROLES = [
    RoleDefinition(
        id="finance-reader",
        name="Finance Reader",
        description="Read access to finance vault for reports and data",
        scopes=["vault:finance:read"],
        intent_patterns=[
            r"finance", r"financial", r"revenue", r"budget", r"expense",
            r"quarterly.*report", r"q[1-4].*report", r"fiscal",
            r"balance.*sheet", r"income.*statement", r"cash.*flow",
        ],
        priority=10,
    ),
    RoleDefinition(
        id="hr-reader",
        name="HR Reader",
        description="Read access to HR vault for employee data",
        scopes=["vault:hr:read"],
        intent_patterns=[
            r"employee", r"hr\b", r"human.*resource", r"staff",
            r"personnel", r"headcount", r"org.*chart", r"benefits",
            r"salary", r"compensation", r"payroll",
        ],
        priority=10,
    ),
    RoleDefinition(
        id="compliance-analyst",
        name="Compliance Analyst",
        description="Cross-vault read access for compliance reporting",
        scopes=["vault:finance:read", "vault:hr:read"],
        intent_patterns=[
            r"compliance.*report", r"cross.*reference",
            r"employee.*transaction", r"transaction.*employee",
            r"audit.*compliance", r"regulatory",
        ],
        priority=20,  # Higher priority than single-vault roles
    ),
    RoleDefinition(
        id="auditor",
        name="Auditor",
        description="Access to audit logs",
        scopes=["audit:read"],
        intent_patterns=[
            r"audit.*log", r"access.*log", r"access.*attempt",
            r"who.*accessed", r"activity.*log", r"security.*log",
        ],
        priority=15,
    ),
    RoleDefinition(
        id="hr-writer",
        name="HR Writer",
        description="Write access to HR vault",
        scopes=["vault:hr:write", "vault:hr:read"],
        intent_patterns=[
            r"update.*employee", r"update.*hr", r"update.*benefits",
            r"modify.*employee", r"change.*policy", r"edit.*documentation",
        ],
        priority=15,
    ),
    RoleDefinition(
        id="shared-reader",
        name="Shared Reader",
        description="Read access to shared/public vault - default for ambiguous requests",
        scopes=["vault:shared:read"],
        intent_patterns=[
            r"company.*info", r"general.*info", r"public",
            r"shared", r"common", r"look.*up.*info",
        ],
        priority=1,  # Low priority - fallback
    ),
]


# Edtech roles for FERPA-governed environments
EDTECH_ROLES = [
    RoleDefinition(
        id="student-records-reader",
        name="Student Records Reader",
        description="Read access to student enrollment, GPA, and course records",
        scopes=["vault:student-records:read"],
        intent_patterns=[
            r"student.*record", r"enrollment", r"gpa", r"transcript",
            r"course.*history", r"academic.*record", r"class.*roster",
            r"student.*info",
        ],
        priority=10,
    ),
    RoleDefinition(
        id="financial-aid-reader",
        name="Financial Aid Reader",
        description="Read access to student financial aid records (Pell grants, loans, scholarships)",
        scopes=["vault:financial-aid:read"],
        intent_patterns=[
            r"financial.*aid", r"pell.*grant", r"scholarship", r"student.*loan",
            r"fafsa", r"aid.*package", r"tuition.*assistance",
        ],
        priority=10,
    ),
    RoleDefinition(
        id="academic-advisor",
        name="Academic Advisor",
        description="Read access to student records and financial aid for advising",
        scopes=["vault:student-records:read", "vault:financial-aid:read"],
        intent_patterns=[
            r"advis", r"academic.*plan", r"degree.*audit", r"course.*recommend",
            r"student.*meeting", r"advising.*session",
        ],
        priority=15,
    ),
    RoleDefinition(
        id="student-health-reader",
        name="Student Health Reader",
        description="Read access to student health and disability accommodation records (highly restricted)",
        scopes=["vault:student-health:read"],
        intent_patterns=[
            r"health.*record", r"disability", r"accommodation",
            r"medical", r"504.*plan", r"iep",
        ],
        priority=10,
        requires_approval=True,
    ),
    RoleDefinition(
        id="ferpa-compliance-analyst",
        name="FERPA Compliance Analyst",
        description="Cross-vault read access for FERPA compliance reporting and attestation",
        scopes=[
            "vault:student-records:read",
            "vault:financial-aid:read",
            "vault:student-health:read",
            "audit:read",
        ],
        intent_patterns=[
            r"ferpa", r"compliance.*report", r"compliance.*audit",
            r"access.*attestation", r"negative.*attestation",
            r"data.*governance", r"privacy.*audit",
        ],
        priority=20,
        requires_approval=True,
    ),
]


# All role sets for easy loading
ROLE_SETS = {
    "builtin": BUILTIN_ROLES,
    "edtech": EDTECH_ROLES,
}


class RoleStore:
    """
    Persistent storage for role definitions.

    Stores roles in SQLite for dynamic updates without code changes.
    """

    def __init__(self, db_path: str = "~/.carryall/roles.db"):
        self.db_path = Path(db_path).expanduser()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
        self._load_builtins()

    def _init_db(self):
        """Initialize database schema."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS roles (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT,
                    scopes TEXT NOT NULL,  -- JSON array
                    intent_patterns TEXT,  -- JSON array
                    priority INTEGER DEFAULT 0,
                    requires_approval INTEGER DEFAULT 0,
                    metadata TEXT,  -- JSON object
                    is_builtin INTEGER DEFAULT 0,
                    created_at TEXT,
                    updated_at TEXT
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_roles_priority
                ON roles(priority DESC)
            """)

    def _load_builtins(self):
        """Load built-in roles if not already present."""
        with sqlite3.connect(self.db_path) as conn:
            for role in BUILTIN_ROLES:
                existing = conn.execute(
                    "SELECT id FROM roles WHERE id = ?", (role.id,)
                ).fetchone()

                if not existing:
                    self._save_role(conn, role, is_builtin=True)

    def load_role_set(self, name: str) -> list[RoleDefinition]:
        """Load a named role set (e.g., 'edtech', 'builtin').

        Returns the list of roles that were loaded.
        """
        if name not in ROLE_SETS:
            raise ValueError(f"Unknown role set: {name}. Available: {list(ROLE_SETS.keys())}")

        roles = ROLE_SETS[name]
        with sqlite3.connect(self.db_path) as conn:
            for role in roles:
                existing = conn.execute(
                    "SELECT id FROM roles WHERE id = ?", (role.id,)
                ).fetchone()
                if not existing:
                    self._save_role(conn, role, is_builtin=True)

        return roles

    def _save_role(self, conn: sqlite3.Connection, role: RoleDefinition, is_builtin: bool = False):
        """Save a role to the database."""
        now = datetime.now(timezone.utc).isoformat()
        conn.execute("""
            INSERT OR REPLACE INTO roles
            (id, name, description, scopes, intent_patterns, priority,
             requires_approval, metadata, is_builtin, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            role.id,
            role.name,
            role.description,
            json.dumps(role.scopes),
            json.dumps(role.intent_patterns),
            role.priority,
            1 if role.requires_approval else 0,
            json.dumps(role.metadata),
            1 if is_builtin else 0,
            now,
            now,
        ))

    def create_role(self, role: RoleDefinition) -> RoleDefinition:
        """Create a new custom role."""
        with sqlite3.connect(self.db_path) as conn:
            self._save_role(conn, role, is_builtin=False)
        return role

    def get_role(self, role_id: str) -> Optional[RoleDefinition]:
        """Get a role by ID."""
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM roles WHERE id = ?", (role_id,)
            ).fetchone()

            if not row:
                return None

            return self._row_to_role(row)

    def list_roles(self, include_builtin: bool = True) -> list[RoleDefinition]:
        """List all roles, ordered by priority."""
        with sqlite3.connect(self.db_path) as conn:
            if include_builtin:
                rows = conn.execute(
                    "SELECT * FROM roles ORDER BY priority DESC"
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM roles WHERE is_builtin = 0 ORDER BY priority DESC"
                ).fetchall()

            return [self._row_to_role(row) for row in rows]

    def delete_role(self, role_id: str) -> bool:
        """Delete a role. Returns True if deleted."""
        with sqlite3.connect(self.db_path) as conn:
            # Don't allow deleting builtins
            result = conn.execute(
                "DELETE FROM roles WHERE id = ? AND is_builtin = 0", (role_id,)
            )
            return result.rowcount > 0

    def _row_to_role(self, row: tuple) -> RoleDefinition:
        """Convert a database row to a RoleDefinition."""
        return RoleDefinition(
            id=row[0],
            name=row[1],
            description=row[2] or "",
            scopes=json.loads(row[3]),
            intent_patterns=json.loads(row[4]) if row[4] else [],
            priority=row[5],
            requires_approval=bool(row[6]),
            metadata=json.loads(row[7]) if row[7] else {},
        )


class IntentMatcher:
    """
    Matches user intents to roles.

    Strategy:
    1. Check cache for exact/similar intent
    2. Try keyword matching against all roles
    3. Fall back to LLM if no match and LLM enabled
    4. Return default role if still no match
    """

    def __init__(
        self,
        role_store: Optional[RoleStore] = None,
        cache_size: int = 1000,
        default_role_id: str = "shared-reader",
    ):
        self.role_store = role_store or RoleStore()
        self.default_role_id = default_role_id
        self._cache: dict[str, tuple[RoleDefinition, float]] = {}
        self._cache_size = cache_size

    def match(
        self,
        intent: str,
        available_scopes: list[str],
        use_llm_fallback: bool = False,
    ) -> tuple[RoleDefinition, float]:
        """
        Match an intent to a role.

        Args:
            intent: User's intent string
            available_scopes: Scopes the agent has available
            use_llm_fallback: Whether to use LLM if no keyword match

        Returns:
            (role, confidence) tuple
        """
        # Normalize intent for caching
        cache_key = self._normalize_intent(intent)

        # Check cache
        if cache_key in self._cache:
            role, confidence = self._cache[cache_key]
            # Verify scopes are still available
            if all(s in available_scopes for s in role.scopes):
                return role, confidence

        # Get all roles and try matching
        roles = self.role_store.list_roles()

        best_match: Optional[RoleDefinition] = None
        best_confidence = 0.0

        for role in roles:
            # Skip roles with unavailable scopes
            if not all(s in available_scopes for s in role.scopes):
                continue

            matches, confidence = role.matches_intent(intent)
            if matches and confidence > best_confidence:
                best_match = role
                best_confidence = confidence

        if best_match:
            self._add_to_cache(cache_key, best_match, best_confidence)
            return best_match, best_confidence

        # Fall back to default role
        default_role = self.role_store.get_role(self.default_role_id)
        if default_role and all(s in available_scopes for s in default_role.scopes):
            return default_role, 0.5

        # Last resort: create a minimal role with first available scope
        if available_scopes:
            return RoleDefinition(
                id="fallback",
                name="Fallback",
                description="Auto-generated fallback role",
                scopes=[available_scopes[0]],
            ), 0.3

        raise ValueError("No roles available for the given scopes")

    def _normalize_intent(self, intent: str) -> str:
        """Normalize intent for caching."""
        # Lowercase, remove extra whitespace
        normalized = " ".join(intent.lower().split())
        # Remove common filler words
        for word in ["please", "can you", "i need to", "i want to", "help me"]:
            normalized = normalized.replace(word, "")
        return normalized.strip()

    def _add_to_cache(self, key: str, role: RoleDefinition, confidence: float):
        """Add to cache with LRU eviction."""
        if len(self._cache) >= self._cache_size:
            # Remove oldest entry (simple FIFO for now)
            oldest_key = next(iter(self._cache))
            del self._cache[oldest_key]

        self._cache[key] = (role, confidence)

    def clear_cache(self):
        """Clear the intent cache."""
        self._cache.clear()

    def get_cache_stats(self) -> dict:
        """Get cache statistics."""
        return {
            "size": len(self._cache),
            "max_size": self._cache_size,
        }


# Convenience functions
_default_matcher: Optional[IntentMatcher] = None


def get_matcher() -> IntentMatcher:
    """Get the default IntentMatcher instance."""
    global _default_matcher
    if _default_matcher is None:
        _default_matcher = IntentMatcher()
    return _default_matcher


def match_intent(intent: str, available_scopes: list[str]) -> tuple[RoleDefinition, float]:
    """Match an intent to a role using the default matcher."""
    return get_matcher().match(intent, available_scopes)


def create_role(
    id: str,
    name: str,
    scopes: list[str],
    intent_patterns: list[str],
    description: str = "",
    priority: int = 10,
) -> RoleDefinition:
    """Create and persist a new role."""
    role = RoleDefinition(
        id=id,
        name=name,
        description=description,
        scopes=scopes,
        intent_patterns=intent_patterns,
        priority=priority,
    )
    return get_matcher().role_store.create_role(role)


def list_roles() -> list[RoleDefinition]:
    """List all available roles."""
    return get_matcher().role_store.list_roles()
