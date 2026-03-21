"""
Tests for the roles module (RBAC, intent matching, role store, caching).

Covers:
- RoleDefinition.matches_intent (keyword matching, regex, confidence)
- RoleStore (SQLite persistence, builtins, CRUD, role sets)
- IntentMatcher (matching, caching, normalization, fallback)
- Built-in role definitions (finance, HR, compliance, auditor, shared)
- Edtech role set (FERPA roles)
"""

import tempfile
import pytest

from authority_runtime.roles import (
    RoleDefinition,
    RoleStore,
    IntentMatcher,
    BUILTIN_ROLES,
    EDTECH_ROLES,
    ROLE_SETS,
)


# =============================================================================
# RoleDefinition.matches_intent
# =============================================================================


class TestRoleDefinitionMatching:
    def test_exact_keyword_match(self):
        role = RoleDefinition(
            id="test", name="Test", description="",
            scopes=["vault:test:read"],
            intent_patterns=["finance", "budget"],
        )
        matches, confidence = role.matches_intent("Show me the budget")
        assert matches is True
        assert confidence > 0.5

    def test_regex_pattern_match(self):
        role = RoleDefinition(
            id="test", name="Test", description="",
            scopes=["vault:test:read"],
            intent_patterns=[r"q[1-4].*report"],
        )
        matches, confidence = role.matches_intent("Get the Q3 quarterly report")
        assert matches is True

    def test_no_match(self):
        role = RoleDefinition(
            id="test", name="Test", description="",
            scopes=["vault:test:read"],
            intent_patterns=["finance", "budget"],
        )
        matches, confidence = role.matches_intent("Show me employee headcount")
        assert matches is False
        assert confidence == 0.0

    def test_case_insensitive(self):
        role = RoleDefinition(
            id="test", name="Test", description="",
            scopes=["vault:test:read"],
            intent_patterns=["FINANCE"],
        )
        matches, _ = role.matches_intent("show me finance data")
        assert matches is True

    def test_multiple_pattern_matches_higher_confidence(self):
        role = RoleDefinition(
            id="test", name="Test", description="",
            scopes=["vault:test:read"],
            intent_patterns=["finance", "budget", "revenue"],
        )
        _, conf_one = role.matches_intent("show me finance data")
        _, conf_two = role.matches_intent("show me finance budget revenue")
        assert conf_two > conf_one

    def test_empty_patterns_no_match(self):
        role = RoleDefinition(
            id="test", name="Test", description="",
            scopes=["vault:test:read"],
            intent_patterns=[],
        )
        matches, confidence = role.matches_intent("anything")
        assert matches is False
        assert confidence == 0.0

    def test_confidence_capped_at_095(self):
        role = RoleDefinition(
            id="test", name="Test", description="",
            scopes=["vault:test:read"],
            intent_patterns=["a"],
        )
        _, confidence = role.matches_intent("a")
        assert confidence <= 0.95

    def test_invalid_regex_falls_back_to_substring(self):
        """Invalid regex should fall back to substring match without crashing."""
        role = RoleDefinition(
            id="test", name="Test", description="",
            scopes=["vault:test:read"],
            intent_patterns=["[invalid regex"],  # Unbalanced bracket
        )
        # The string "[invalid regex" should still match as substring
        matches, _ = role.matches_intent("contains [invalid regex here")
        assert matches is True


# =============================================================================
# Built-in Roles
# =============================================================================


class TestBuiltinRoles:
    def test_finance_reader_matches_finance(self):
        role = next(r for r in BUILTIN_ROLES if r.id == "finance-reader")
        matches, _ = role.matches_intent("Read the Q4 finance report")
        assert matches is True

    def test_hr_reader_matches_employee(self):
        role = next(r for r in BUILTIN_ROLES if r.id == "hr-reader")
        matches, _ = role.matches_intent("Show me employee headcount")
        assert matches is True

    def test_compliance_analyst_matches_cross_reference(self):
        role = next(r for r in BUILTIN_ROLES if r.id == "compliance-analyst")
        matches, _ = role.matches_intent("Generate a compliance report cross-referencing employee and transactions")
        assert matches is True

    def test_auditor_matches_access_log(self):
        role = next(r for r in BUILTIN_ROLES if r.id == "auditor")
        matches, _ = role.matches_intent("Show me the audit log for last week")
        assert matches is True

    def test_shared_reader_matches_general(self):
        role = next(r for r in BUILTIN_ROLES if r.id == "shared-reader")
        matches, _ = role.matches_intent("Look up company info")
        assert matches is True

    def test_compliance_has_higher_priority_than_finance(self):
        compliance = next(r for r in BUILTIN_ROLES if r.id == "compliance-analyst")
        finance = next(r for r in BUILTIN_ROLES if r.id == "finance-reader")
        assert compliance.priority > finance.priority

    def test_all_builtins_have_scopes(self):
        for role in BUILTIN_ROLES:
            assert len(role.scopes) > 0, f"Role {role.id} has no scopes"

    def test_finance_not_matched_by_hr(self):
        role = next(r for r in BUILTIN_ROLES if r.id == "hr-reader")
        matches, _ = role.matches_intent("Read the Q4 finance report")
        assert matches is False


# =============================================================================
# Edtech Roles
# =============================================================================


class TestEdtechRoles:
    def test_edtech_role_set_exists(self):
        assert "edtech" in ROLE_SETS

    def test_student_records_reader(self):
        role = next(r for r in EDTECH_ROLES if r.id == "student-records-reader")
        matches, _ = role.matches_intent("Show me enrollment records")
        assert matches is True
        assert "vault:student-records:read" in role.scopes

    def test_ferpa_compliance_analyst(self):
        role = next(r for r in EDTECH_ROLES if r.id == "ferpa-compliance-analyst")
        matches, _ = role.matches_intent("Generate a FERPA compliance report")
        assert matches is True
        assert role.requires_approval is True

    def test_student_health_requires_approval(self):
        role = next(r for r in EDTECH_ROLES if r.id == "student-health-reader")
        assert role.requires_approval is True

    def test_academic_advisor_has_dual_scopes(self):
        role = next(r for r in EDTECH_ROLES if r.id == "academic-advisor")
        assert "vault:student-records:read" in role.scopes
        assert "vault:financial-aid:read" in role.scopes


# =============================================================================
# RoleStore (SQLite persistence)
# =============================================================================


class TestRoleStore:
    @pytest.fixture
    def store(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as f:
            yield RoleStore(db_path=f.name)

    def test_builtins_loaded_on_init(self, store):
        roles = store.list_roles()
        builtin_ids = {r.id for r in BUILTIN_ROLES}
        stored_ids = {r.id for r in roles}
        assert builtin_ids.issubset(stored_ids)

    def test_create_custom_role(self, store):
        custom = RoleDefinition(
            id="custom-test",
            name="Custom Test",
            description="A custom role",
            scopes=["vault:custom:read"],
            intent_patterns=["custom", "special"],
            priority=5,
        )
        store.create_role(custom)
        retrieved = store.get_role("custom-test")
        assert retrieved is not None
        assert retrieved.name == "Custom Test"
        assert retrieved.scopes == ["vault:custom:read"]
        assert retrieved.intent_patterns == ["custom", "special"]

    def test_get_nonexistent_role(self, store):
        assert store.get_role("nonexistent") is None

    def test_list_excludes_builtins(self, store):
        custom = RoleDefinition(
            id="custom-only",
            name="Custom Only",
            description="",
            scopes=["vault:x:read"],
        )
        store.create_role(custom)
        custom_only = store.list_roles(include_builtin=False)
        ids = {r.id for r in custom_only}
        assert "custom-only" in ids
        assert "finance-reader" not in ids

    def test_delete_custom_role(self, store):
        custom = RoleDefinition(
            id="to-delete", name="Delete Me", description="",
            scopes=["vault:x:read"],
        )
        store.create_role(custom)
        assert store.delete_role("to-delete") is True
        assert store.get_role("to-delete") is None

    def test_cannot_delete_builtin(self, store):
        assert store.delete_role("finance-reader") is False
        assert store.get_role("finance-reader") is not None

    def test_load_edtech_role_set(self, store):
        loaded = store.load_role_set("edtech")
        assert len(loaded) == len(EDTECH_ROLES)
        # Verify they're persisted
        all_roles = store.list_roles()
        all_ids = {r.id for r in all_roles}
        for edtech_role in EDTECH_ROLES:
            assert edtech_role.id in all_ids

    def test_load_invalid_role_set_raises(self, store):
        with pytest.raises(ValueError, match="Unknown role set"):
            store.load_role_set("nonexistent")

    def test_roles_ordered_by_priority(self, store):
        roles = store.list_roles()
        priorities = [r.priority for r in roles]
        assert priorities == sorted(priorities, reverse=True)


# =============================================================================
# IntentMatcher
# =============================================================================


class TestIntentMatcher:
    @pytest.fixture
    def matcher(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as f:
            store = RoleStore(db_path=f.name)
            yield IntentMatcher(role_store=store)

    def test_matches_finance_intent(self, matcher):
        scopes = ["vault:finance:read", "vault:hr:read", "audit:read"]
        role, confidence = matcher.match("Read the Q4 finance report", scopes)
        assert "vault:finance:read" in role.scopes
        assert confidence > 0.5

    def test_matches_hr_intent(self, matcher):
        scopes = ["vault:finance:read", "vault:hr:read"]
        role, confidence = matcher.match("Show employee headcount", scopes)
        assert "vault:hr:read" in role.scopes

    def test_skips_roles_with_unavailable_scopes(self, matcher):
        """If a role's scopes aren't in available_scopes, skip it."""
        scopes = ["vault:hr:read"]  # No finance scope
        role, _ = matcher.match("Read the finance report", scopes)
        # Should not match finance-reader since its scope isn't available
        assert "vault:finance:read" not in role.scopes

    def test_falls_back_to_default_role(self, matcher):
        scopes = ["vault:shared:read"]
        role, confidence = matcher.match("something completely random xyz", scopes)
        # Should fall back to shared-reader or similar
        assert confidence <= 0.5

    def test_caching_works(self, matcher):
        scopes = ["vault:finance:read", "vault:hr:read"]
        role1, conf1 = matcher.match("Read the finance report", scopes)
        role2, conf2 = matcher.match("Read the finance report", scopes)
        assert role1.id == role2.id
        assert conf1 == conf2
        stats = matcher.get_cache_stats()
        assert stats["size"] >= 1

    def test_clear_cache(self, matcher):
        scopes = ["vault:finance:read"]
        matcher.match("Read finance data", scopes)
        assert matcher.get_cache_stats()["size"] >= 1
        matcher.clear_cache()
        assert matcher.get_cache_stats()["size"] == 0

    def test_normalize_intent_strips_filler(self, matcher):
        normalized = matcher._normalize_intent("Please can you help me read finance data")
        assert "please" not in normalized
        assert "can you" not in normalized
        assert "help me" not in normalized
        assert "finance" in normalized

    def test_cache_eviction(self):
        """Cache should evict oldest entries when full."""
        with tempfile.NamedTemporaryFile(suffix=".db") as f:
            store = RoleStore(db_path=f.name)
            matcher = IntentMatcher(role_store=store, cache_size=3)

            scopes = ["vault:finance:read", "vault:hr:read", "vault:shared:read"]
            matcher.match("finance report", scopes)
            matcher.match("employee data", scopes)
            matcher.match("shared information", scopes)
            assert matcher.get_cache_stats()["size"] == 3

            # Adding one more should evict the oldest
            matcher.match("budget analysis for the quarter", scopes)
            assert matcher.get_cache_stats()["size"] == 3

    def test_no_scopes_raises(self, matcher):
        with pytest.raises(ValueError, match="No roles available"):
            matcher.match("anything", [])

    def test_fallback_role_with_single_scope(self, matcher):
        """When no role matches and default isn't available, use first scope."""
        scopes = ["vault:exotic:read"]
        role, confidence = matcher.match("something unmatched", scopes)
        assert confidence <= 0.5
        assert "vault:exotic:read" in role.scopes
