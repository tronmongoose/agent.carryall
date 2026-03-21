#!/usr/bin/env python3
"""
Carryall Dogfooding Script - Real Policy Compilation Metrics

This script exercises the LLM policy compiler with realistic agent scenarios
and collects metrics we can use to improve the system.

Scenarios tested:
1. Research agent - needs to read finance docs
2. Multi-vault access - HR + Finance for compliance report
3. Overly broad request - tests scope narrowing
4. Ambiguous intent - tests LLM interpretation
5. Repeated similar requests - tests consistency

Metrics collected:
- Scope reduction ratio (key value prop)
- LLM latency (user experience)
- Token cost (operational cost)
- Confidence scores (reliability)
- Consistency across similar requests
"""

import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

# Load .env file from parent directory
env_file = Path(__file__).parent.parent / ".env"
if env_file.exists():
    with open(env_file) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip())

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from authority_runtime.compiler import OpenAICompiler, AnthropicCompiler, RoleAwareCompiler
from authority_runtime.types import Authority, Skill, TokenMetrics
from authority_runtime.keys import AgentKeyStore
from authority_runtime.roles import list_roles, RoleDefinition


@dataclass
class TestScenario:
    """A test scenario for policy compilation"""
    name: str
    intent: str
    available_scopes: list[str]
    available_resources: list[str]
    expected_scopes: list[str]  # What we expect the LLM to select
    description: str


@dataclass
class TestResult:
    """Result of running a test scenario"""
    scenario_name: str
    selected_scopes: list[str]
    expected_scopes: list[str]
    scope_match: bool
    scope_reduction_ratio: float
    confidence: float
    reasoning: str
    latency_ms: int
    input_tokens: int
    output_tokens: int
    cost_usd: float
    error: Optional[str] = None


@dataclass
class MetricsSummary:
    """Aggregated metrics across all tests"""
    total_tests: int = 0
    passed_tests: int = 0
    avg_scope_reduction: float = 0.0
    avg_latency_ms: float = 0.0
    avg_confidence: float = 0.0
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    consistency_score: float = 0.0  # How consistent are similar requests
    results: list[TestResult] = field(default_factory=list)


# Define realistic test scenarios
SCENARIOS = [
    TestScenario(
        name="research_finance_read",
        intent="I need to read the Q4 2025 finance report to extract revenue figures",
        available_scopes=[
            "vault:finance:read",
            "vault:finance:write",
            "vault:hr:read",
            "vault:shared:read",
            "vault:legal:read",
        ],
        available_resources=["slos://vaults/*"],
        expected_scopes=["vault:finance:read"],
        description="Simple read request - should narrow to single scope"
    ),

    TestScenario(
        name="compliance_multi_vault",
        intent="Generate a compliance report that cross-references employee data with financial transactions",
        available_scopes=[
            "vault:finance:read",
            "vault:hr:read",
            "vault:legal:read",
            "vault:shared:read",
            "audit:read",
        ],
        available_resources=["slos://vaults/*"],
        expected_scopes=["vault:finance:read", "vault:hr:read"],
        description="Multi-vault access - should select exactly 2 relevant scopes"
    ),

    TestScenario(
        name="overly_broad_request",
        intent="Look up some information about the company",
        available_scopes=[
            "vault:finance:read",
            "vault:finance:write",
            "vault:hr:read",
            "vault:hr:write",
            "vault:legal:read",
            "vault:shared:read",
            "vault:shared:write",
            "audit:read",
        ],
        available_resources=["slos://vaults/*"],
        expected_scopes=["vault:shared:read"],  # Should default to minimal
        description="Vague request - should select minimal safe scope"
    ),

    TestScenario(
        name="write_operation",
        intent="Update the employee benefits documentation with the new 2026 policy changes",
        available_scopes=[
            "vault:hr:read",
            "vault:hr:write",
            "vault:shared:read",
            "vault:shared:write",
        ],
        available_resources=["slos://vaults/hr/*", "slos://vaults/shared/*"],
        expected_scopes=["vault:hr:write"],  # Needs write, should get only HR write
        description="Write operation - should select write scope for correct vault"
    ),

    TestScenario(
        name="audit_query",
        intent="Show me all access attempts to the finance vault in the last 24 hours",
        available_scopes=[
            "vault:finance:read",
            "vault:hr:read",
            "audit:read",
        ],
        available_resources=["slos://vaults/*"],
        expected_scopes=["audit:read"],
        description="Audit query - should select only audit scope"
    ),

    # Consistency tests - similar requests that should get same results
    TestScenario(
        name="consistency_1",
        intent="Read the quarterly financial report",
        available_scopes=["vault:finance:read", "vault:hr:read", "vault:shared:read"],
        available_resources=["slos://vaults/*"],
        expected_scopes=["vault:finance:read"],
        description="Consistency test 1"
    ),

    TestScenario(
        name="consistency_2",
        intent="Access the Q4 finance document",
        available_scopes=["vault:finance:read", "vault:hr:read", "vault:shared:read"],
        available_resources=["slos://vaults/*"],
        expected_scopes=["vault:finance:read"],
        description="Consistency test 2"
    ),

    TestScenario(
        name="consistency_3",
        intent="Get financial data from the quarterly report",
        available_scopes=["vault:finance:read", "vault:hr:read", "vault:shared:read"],
        available_resources=["slos://vaults/*"],
        expected_scopes=["vault:finance:read"],
        description="Consistency test 3"
    ),
]


def create_skills_from_scopes(scopes: list[str]) -> list[Skill]:
    """Generate skill definitions from scopes"""
    skills = []
    seen = set()

    for scope in scopes:
        parts = scope.split(":")
        if len(parts) >= 2:
            namespace = parts[0]
            action = parts[-1]
            skill_id = f"skill-{namespace}-{action}"

            if skill_id not in seen:
                seen.add(skill_id)
                skills.append(Skill(
                    id=skill_id,
                    name=f"{action.capitalize()} {namespace}",
                    tool=f"{namespace}_{action}",
                    description=f"Perform {action} on {namespace}",
                    parameters={"allowed": [scope], "constraints": {}}
                ))

    return skills or [Skill(
        id="skill-default",
        name="Default",
        tool="default",
        description="Default skill",
        parameters={"allowed": scopes, "constraints": {}}
    )]


async def run_scenario(
    scenario: TestScenario,
    compiler: OpenAICompiler | AnthropicCompiler,
) -> TestResult:
    """Run a single test scenario"""

    parent_authority = Authority(
        scopes=scenario.available_scopes,
        resources=scenario.available_resources,
        constraints={},
    )

    available_skills = create_skills_from_scopes(scenario.available_scopes)
    available_context = ["intent", "agent_id", "timestamp"]

    try:
        selection = await compiler.select_skill(
            user_request=scenario.intent,
            current_step=1,
            parent_authority=parent_authority,
            available_context_fields=available_context,
            available_skills=available_skills,
            available_scopes=scenario.available_scopes,
            temperature=0.0,
        )

        metrics = compiler.get_last_metrics()

        # Calculate scope reduction
        original_count = len(scenario.available_scopes)
        selected_count = len(selection.required_scopes)
        reduction = 1.0 - (selected_count / original_count) if original_count > 0 else 0.0

        # Check if selection matches expected
        scope_match = set(selection.required_scopes) == set(scenario.expected_scopes)

        return TestResult(
            scenario_name=scenario.name,
            selected_scopes=selection.required_scopes,
            expected_scopes=scenario.expected_scopes,
            scope_match=scope_match,
            scope_reduction_ratio=reduction,
            confidence=selection.confidence,
            reasoning=selection.reasoning,
            latency_ms=metrics.latency_ms if metrics else 0,
            input_tokens=metrics.input_tokens if metrics else 0,
            output_tokens=metrics.output_tokens if metrics else 0,
            cost_usd=metrics.total_cost_usd if metrics else 0.0,
        )

    except Exception as e:
        return TestResult(
            scenario_name=scenario.name,
            selected_scopes=[],
            expected_scopes=scenario.expected_scopes,
            scope_match=False,
            scope_reduction_ratio=0.0,
            confidence=0.0,
            reasoning="",
            latency_ms=0,
            input_tokens=0,
            output_tokens=0,
            cost_usd=0.0,
            error=str(e),
        )


def calculate_consistency(results: list[TestResult]) -> float:
    """Calculate consistency score for similar requests"""
    consistency_results = [r for r in results if r.scenario_name.startswith("consistency_")]

    if len(consistency_results) < 2:
        return 1.0

    # Check if all consistency tests got the same scopes
    first_scopes = set(consistency_results[0].selected_scopes)
    matches = sum(1 for r in consistency_results if set(r.selected_scopes) == first_scopes)

    return matches / len(consistency_results)


def print_results(summary: MetricsSummary):
    """Print formatted results"""
    print("\n" + "=" * 70)
    print("CARRYALL POLICY COMPILER - DOGFOODING METRICS")
    print("=" * 70)
    print(f"Timestamp: {datetime.now().isoformat()}")
    print(f"Total scenarios: {summary.total_tests}")
    print(f"Passed (exact match): {summary.passed_tests}/{summary.total_tests}")
    print()

    print("KEY METRICS:")
    print("-" * 40)
    print(f"  Avg Scope Reduction:  {summary.avg_scope_reduction*100:.1f}%")
    print(f"  Avg Latency:          {summary.avg_latency_ms:.0f}ms")
    print(f"  Avg Confidence:       {summary.avg_confidence:.2f}")
    print(f"  Consistency Score:    {summary.consistency_score*100:.0f}%")
    print(f"  Total Tokens:         {summary.total_tokens:,}")
    print(f"  Total Cost:           ${summary.total_cost_usd:.4f}")
    print()

    print("DETAILED RESULTS:")
    print("-" * 70)

    for result in summary.results:
        status = "PASS" if result.scope_match else "FAIL"
        status_color = "✓" if result.scope_match else "✗"

        print(f"\n{status_color} {result.scenario_name} [{status}]")
        print(f"  Intent: {SCENARIOS[[s.name for s in SCENARIOS].index(result.scenario_name)].intent[:60]}...")
        print(f"  Expected: {result.expected_scopes}")
        print(f"  Selected: {result.selected_scopes}")
        print(f"  Reduction: {result.scope_reduction_ratio*100:.0f}% | Confidence: {result.confidence:.2f} | Latency: {result.latency_ms}ms")

        if result.error:
            print(f"  ERROR: {result.error}")
        elif not result.scope_match:
            print(f"  Reasoning: {result.reasoning[:100]}...")

    print("\n" + "=" * 70)
    print("IMPROVEMENT OPPORTUNITIES:")
    print("-" * 40)

    # Identify improvement areas
    failed = [r for r in summary.results if not r.scope_match and not r.error]
    errors = [r for r in summary.results if r.error]
    slow = [r for r in summary.results if r.latency_ms > 2000]
    low_confidence = [r for r in summary.results if r.confidence < 0.8]

    if failed:
        print(f"  - {len(failed)} scenarios didn't match expected scopes")
        for r in failed:
            print(f"    • {r.scenario_name}: got {r.selected_scopes}, expected {r.expected_scopes}")

    if errors:
        print(f"  - {len(errors)} scenarios had errors")

    if slow:
        print(f"  - {len(slow)} scenarios took >2s (consider caching)")

    if low_confidence:
        print(f"  - {len(low_confidence)} scenarios had <0.8 confidence")

    if summary.consistency_score < 1.0:
        print(f"  - Consistency score {summary.consistency_score*100:.0f}% (similar requests got different results)")

    if not (failed or errors or slow or low_confidence or summary.consistency_score < 1.0):
        print("  None identified - all metrics look good!")

    print("=" * 70)


async def main():
    """Run all dogfooding tests"""

    # Check for API key
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("ERROR: OPENAI_API_KEY environment variable not set")
        print("Set it with: export OPENAI_API_KEY=your-key")
        sys.exit(1)

    # Choose compiler mode based on args
    use_roles = "--roles" in sys.argv or "-r" in sys.argv
    llm_only = "--llm-only" in sys.argv

    if use_roles and not llm_only:
        print("Mode: ROLE-AWARE (role matching + LLM fallback)")
        print()
        print("Available roles:")
        for role in list_roles():
            print(f"  - {role.name}: {role.scopes} (priority={role.priority})")
        print()

        llm_compiler = OpenAICompiler(model="gpt-4o-mini", api_key=api_key)
        compiler = RoleAwareCompiler(
            llm_compiler=llm_compiler,
            role_confidence_threshold=0.7,
            llm_fallback=True,
        )
    else:
        print("Mode: LLM-ONLY (improved prompt)")
        compiler = OpenAICompiler(model="gpt-4o-mini", api_key=api_key)

    print()
    print(f"Running {len(SCENARIOS)} test scenarios...")
    print()

    results = []
    for i, scenario in enumerate(SCENARIOS, 1):
        print(f"  [{i}/{len(SCENARIOS)}] {scenario.name}...", end=" ", flush=True)
        result = await run_scenario(scenario, compiler)
        results.append(result)

        if result.error:
            print(f"ERROR: {result.error[:40]}")
        else:
            status = "✓" if result.scope_match else "✗"
            print(f"{status} ({result.latency_ms}ms, {result.scope_reduction_ratio*100:.0f}% reduction)")

    # Calculate summary
    valid_results = [r for r in results if not r.error]

    summary = MetricsSummary(
        total_tests=len(SCENARIOS),
        passed_tests=sum(1 for r in results if r.scope_match),
        avg_scope_reduction=sum(r.scope_reduction_ratio for r in valid_results) / len(valid_results) if valid_results else 0,
        avg_latency_ms=sum(r.latency_ms for r in valid_results) / len(valid_results) if valid_results else 0,
        avg_confidence=sum(r.confidence for r in valid_results) / len(valid_results) if valid_results else 0,
        total_tokens=sum(r.input_tokens + r.output_tokens for r in valid_results),
        total_cost_usd=sum(r.cost_usd for r in valid_results),
        consistency_score=calculate_consistency(results),
        results=results,
    )

    print_results(summary)

    # Print role-aware stats if applicable
    if hasattr(compiler, 'get_stats'):
        stats = compiler.get_stats()
        print("\nROLE-AWARE COMPILER STATS:")
        print("-" * 40)
        print(f"  Role hits (cached):   {stats['role_hits']}")
        print(f"  LLM calls:            {stats['llm_calls']}")
        print(f"  Cache hit rate:       {stats['cache_hit_rate']*100:.0f}%")
        print(f"  Estimated savings:    ${stats['role_hits'] * 0.00014:.4f}")
        print()

    # Save results to JSON for tracking over time
    output_dir = Path(__file__).parent.parent / "metrics"
    output_dir.mkdir(exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_file = output_dir / f"dogfood_{timestamp}.json"

    with open(output_file, "w") as f:
        json.dump({
            "timestamp": datetime.now().isoformat(),
            "summary": {
                "total_tests": summary.total_tests,
                "passed_tests": summary.passed_tests,
                "avg_scope_reduction": summary.avg_scope_reduction,
                "avg_latency_ms": summary.avg_latency_ms,
                "avg_confidence": summary.avg_confidence,
                "total_tokens": summary.total_tokens,
                "total_cost_usd": summary.total_cost_usd,
                "consistency_score": summary.consistency_score,
            },
            "results": [
                {
                    "scenario": r.scenario_name,
                    "selected_scopes": r.selected_scopes,
                    "expected_scopes": r.expected_scopes,
                    "scope_match": r.scope_match,
                    "scope_reduction_ratio": r.scope_reduction_ratio,
                    "confidence": r.confidence,
                    "reasoning": r.reasoning,
                    "latency_ms": r.latency_ms,
                    "tokens": r.input_tokens + r.output_tokens,
                    "cost_usd": r.cost_usd,
                    "error": r.error,
                }
                for r in results
            ],
        }, f, indent=2)

    print(f"\nResults saved to: {output_file}")

    return summary


if __name__ == "__main__":
    asyncio.run(main())
