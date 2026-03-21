#!/usr/bin/env python3
"""
ClawRouter — Intelligent Model Routing for Finance Queries (M2.3)

Routes natural language finance questions to either:
  LOCAL  — Mistral Small 3.2 via Ollama (free, fast, private)
  FRONTIER — Claude Sonnet via Anthropic Messages API (paid, smart, tool-use)

Privacy gate: queries involving personal financial data (balances, accounts,
transactions, merchants, etc.) are ALWAYS routed locally. The frontier path
sends real Firefly III data to Anthropic's API — the sensitivity gate prevents
this for any query that would expose personal financial information.

Usage:
    python usecases/clawrouter.py "what's my checking balance?"
    python usecases/clawrouter.py "why is spending higher this month?"
    python usecases/clawrouter.py --route-only "should I cancel any subscriptions?"
    python usecases/clawrouter.py --force-local "analyze my spending"
    python usecases/clawrouter.py --force-frontier "what's my balance?"
    python usecases/clawrouter.py --test

Environment:
    FIREFLY_TOKEN       - Firefly III personal access token
    OLLAMA_URL          - Ollama API base URL (default: http://localhost:11434)
    ANTHROPIC_API_KEY   - Anthropic API key (required for frontier path)
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# Add usecases/ to path so we can import firefly_tools (also loads secrets)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import firefly_tools
from common import SLOS_DIR
from context_manager import assemble_context_block

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
FRONTIER_MODEL = os.environ.get("CLAWROUTER_FRONTIER_MODEL", "claude-sonnet-4-20250514")
LOCAL_MODEL = os.environ.get("CLAWROUTER_LOCAL_MODEL", "mistral-small-3.2")
ROUTER_MODEL = os.environ.get("CLAWROUTER_ROUTER_MODEL", "hermes3:8b")

USAGE_LOG = Path(SLOS_DIR) / "vaults" / "finance" / "router-usage.jsonl"


# ── Usage Logging ─────────────────────────────────────────────


def log_usage(result: dict, query: str):
    """Append a usage entry to the JSONL log file."""
    entry = {
        "timestamp": datetime.now().isoformat(),
        "query_len": len(query),
        "route": result.get("route", "unknown"),
        "model": result.get("model", "unknown"),
        "tokens": result.get("tokens", 0),
        "cost_usd": result.get("cost_usd", 0.0),
        "latency_ms": result.get("latency_ms", 0),
        "score": result.get("classification", {}).get("score", 0),
        "sensitive": result.get("sensitivity", {}).get("sensitive", False),
    }
    USAGE_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(USAGE_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def load_usage_log(days: int = None) -> list:
    """Load usage entries from the JSONL log. Optionally filter to last N days."""
    if not USAGE_LOG.exists():
        return []
    entries = []
    cutoff = None
    if days:
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    for line in USAGE_LOG.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
            if cutoff and entry.get("timestamp", "") < cutoff:
                continue
            entries.append(entry)
        except json.JSONDecodeError:
            continue
    return entries


def compute_usage_stats(entries: list) -> dict:
    """Compute aggregate stats from usage log entries."""
    if not entries:
        return {
            "total_queries": 0, "local_queries": 0, "frontier_queries": 0,
            "local_pct": 0, "frontier_pct": 0,
            "total_tokens": 0, "local_tokens": 0, "frontier_tokens": 0,
            "total_cost": 0.0,
            "avg_latency_local": 0, "avg_latency_frontier": 0,
        }

    local = [e for e in entries if e.get("route") == "local"]
    frontier = [e for e in entries if e.get("route") == "frontier"]
    total = len(entries)

    local_tokens = sum(e.get("tokens", 0) for e in local)
    frontier_tokens = sum(e.get("tokens", 0) for e in frontier)
    total_cost = sum(e.get("cost_usd", 0) for e in entries)

    avg_lat_local = (sum(e.get("latency_ms", 0) for e in local) / len(local)) if local else 0
    avg_lat_frontier = (sum(e.get("latency_ms", 0) for e in frontier) / len(frontier)) if frontier else 0

    return {
        "total_queries": total,
        "local_queries": len(local),
        "frontier_queries": len(frontier),
        "local_pct": round(len(local) / total * 100, 1) if total else 0,
        "frontier_pct": round(len(frontier) / total * 100, 1) if total else 0,
        "total_tokens": local_tokens + frontier_tokens,
        "local_tokens": local_tokens,
        "frontier_tokens": frontier_tokens,
        "total_cost": round(total_cost, 4),
        "avg_latency_local": round(avg_lat_local),
        "avg_latency_frontier": round(avg_lat_frontier),
    }


# ── Complexity Classifier ──────────────────────────────────────


# Keywords that signal multi-step reasoning / frontier-worthy queries
FRONTIER_SIGNALS = [
    (r"\bwhy\b", 0.3, "asks 'why' (causal reasoning)"),
    (r"\bshould\b", 0.3, "asks 'should' (recommendation)"),
    (r"\bcompare\b", 0.25, "comparison request"),
    (r"\banalyze\b", 0.25, "analysis request"),
    (r"\brecommend\b", 0.3, "recommendation request"),
    (r"\bunusual\b", 0.25, "anomaly detection"),
    (r"\banomal", 0.25, "anomaly detection"),
    (r"\btrend\b", 0.2, "trend analysis"),
    (r"\boptimize\b", 0.3, "optimization request"),
    (r"\breduce\b", 0.2, "reduction strategy"),
    (r"\bsave\b", 0.15, "savings strategy"),
    (r"\bplan\b", 0.2, "planning request"),
    (r"\bforecast\b", 0.3, "forecasting"),
    (r"\bpredict\b", 0.3, "prediction"),
    (r"\bbudget.*advice\b", 0.3, "budget advice"),
    (r"\bcancel\b", 0.2, "cancellation recommendation"),
    (r"\bprioritize\b", 0.3, "prioritization"),
    (r"\bvs\.?\b|versus", 0.2, "comparison"),
]

# Keywords that signal simple lookups / local-worthy queries
LOCAL_SIGNALS = [
    (r"\bbalance\b", -0.15, "balance lookup"),
    (r"\bhow much\b", -0.1, "simple amount query"),
    (r"\bwhat('s| is| are)\b", -0.1, "simple what-query"),
    (r"\bcategorize\b", -0.2, "categorization task"),
    (r"\blist\b", -0.1, "list request"),
    (r"\bshow\b", -0.1, "show request"),
    (r"\bnet worth\b", -0.1, "net worth lookup"),
]


def classify_complexity(query: str) -> dict:
    """Score query complexity on 0-1 scale. Determines local vs frontier routing.

    Returns: {"score": float, "route": "local"|"frontier", "reasons": [str]}
    """
    q = query.lower().strip()
    score = 0.3  # baseline — slightly below frontier threshold
    reasons = []

    for pattern, weight, reason in FRONTIER_SIGNALS + LOCAL_SIGNALS:
        if re.search(pattern, q):
            score += weight
            reasons.append(f"{'+' if weight > 0 else ''}{weight:.2f} {reason}")

    # Multi-entity detection (multiple accounts, time periods, categories)
    period_refs = len(re.findall(r"\b(month|week|year|january|february|march|april|may|june|july|august|september|october|november|december|last|this|next)\b", q))
    if period_refs >= 2:
        score += 0.15
        reasons.append("+0.15 multiple time references")

    # Question length as a weak signal (longer = more complex)
    words = len(q.split())
    if words > 15:
        score += 0.1
        reasons.append("+0.10 long query (>15 words)")

    score = max(0.0, min(1.0, score))
    route = "frontier" if score >= 0.4 else "local"

    return {"score": round(score, 2), "route": route, "reasons": reasons}


# ── Sensitivity Classifier ────────────────────────────────────


# Patterns that indicate personal financial data will be fetched.
# A single match is enough to gate — false positives cost nothing,
# false negatives leak real account data to Anthropic.
SENSITIVE_SIGNALS = [
    (r"\bbalance[s]?\b", "account balance"),
    (r"\baccount[s]?\b", "account reference"),
    (r"\btransaction[s]?\b", "transaction data"),
    (r"\bmerchant[s]?\b", "merchant data"),
    (r"\bspend(ing|t)?\b", "spending data"),
    (r"\bdebt\b", "debt info"),
    (r"\bowe|owing\b", "debt info"),
    (r"\bnet\s*worth\b", "net worth"),
    (r"\bassets?\b", "asset data"),
    (r"\bliabilit", "liability data"),
    (r"\bincome\b", "income data"),
    (r"\bsalar(y|ies)\b", "salary data"),
    (r"\bcredit\b", "credit account"),
    (r"\bchecking\b", "checking account"),
    (r"\bsaving[s]?\b", "savings account"),
    (r"\bamex\b", "specific account"),
    (r"\bbill[s]?\b", "bill data"),
    (r"\bpayment[s]?\b", "payment data"),
    (r"\bbudget\b", "budget data"),
    (r"\bsubscription[s]?\b", "subscription data"),
    (r"\brecurring\b", "recurring charges"),
    (r"\bgroceries\b", "spending category"),
    (r"\brent\b", "housing cost"),
    (r"\bmortgage\b", "housing cost"),
    (r"\bloan[s]?\b", "loan data"),
    (r"\bhow much\b", "amount query"),
    (r"\$\d", "dollar amount"),
]


def classify_sensitivity(query: str) -> dict:
    """Check if query involves personal financial data that must stay local.

    Returns: {"sensitive": bool, "reasons": [str]}
    """
    q = query.lower().strip()
    reasons = []

    for pattern, label in SENSITIVE_SIGNALS:
        if re.search(pattern, q):
            reasons.append(label)

    return {"sensitive": len(reasons) > 0, "reasons": reasons}


# ── Tool Definitions for Claude ────────────────────────────────


TOOLS = [
    {
        "name": "get_account_balances",
        "description": "Get current balances for all accounts or filter by name. Returns account names, types, balances, currencies, and last activity dates.",
        "input_schema": {
            "type": "object",
            "properties": {
                "account_name": {
                    "type": "string",
                    "description": "Optional partial name filter (e.g. 'checking', 'AMEX'). Omit for all accounts.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_spending_by_category",
        "description": "Get spending totals grouped by category for a date range. Returns categories with totals and transaction counts.",
        "input_schema": {
            "type": "object",
            "properties": {
                "period": {
                    "type": "string",
                    "description": "Year-month like '2026-02'. Default: current month.",
                },
                "days": {
                    "type": "integer",
                    "description": "Alternative: last N days. Overrides period if set.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_net_worth",
        "description": "Get total assets minus liabilities with full breakdown by account.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "get_transactions",
        "description": "Search transactions with filters. Can filter by merchant name, amount range, category, and lookback period.",
        "input_schema": {
            "type": "object",
            "properties": {
                "merchant": {"type": "string", "description": "Merchant name substring search."},
                "amount_min": {"type": "number", "description": "Minimum absolute amount."},
                "amount_max": {"type": "number", "description": "Maximum absolute amount."},
                "days": {"type": "integer", "description": "Look back N days (default 30)."},
                "category": {"type": "string", "description": "Category name filter."},
                "limit": {"type": "integer", "description": "Max results (default 50)."},
            },
            "required": [],
        },
    },
    {
        "name": "get_budget_status",
        "description": "Get budget vs. actual spending for all budget categories. Shows budgeted amount, spent, remaining, and percentage used.",
        "input_schema": {
            "type": "object",
            "properties": {
                "period": {"type": "string", "description": "Year-month like '2026-02'. Default: current month."},
            },
            "required": [],
        },
    },
    {
        "name": "get_recurring_charges",
        "description": "Detect subscriptions and recurring payments from transaction history. Identifies merchants with 2+ charges at similar amounts.",
        "input_schema": {
            "type": "object",
            "properties": {
                "days": {"type": "integer", "description": "Lookback window in days (default 90)."},
            },
            "required": [],
        },
    },
    {
        "name": "get_spending_trend",
        "description": "Get category spending over time, broken down by weekly or monthly periods.",
        "input_schema": {
            "type": "object",
            "properties": {
                "granularity": {
                    "type": "string",
                    "enum": ["weekly", "monthly"],
                    "description": "Time granularity (default: monthly).",
                },
                "months": {"type": "integer", "description": "How many months to look back (default 3)."},
            },
            "required": [],
        },
    },
    {
        "name": "get_goal_progress",
        "description": "Get savings goal tracking with projections. Uses Firefly III piggy banks as goals.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "compare_periods",
        "description": "Compare spending between two months. Shows per-category changes and overall difference.",
        "input_schema": {
            "type": "object",
            "properties": {
                "period_a": {"type": "string", "description": "First period, e.g. '2026-01'."},
                "period_b": {"type": "string", "description": "Second period, e.g. '2026-02'."},
            },
            "required": ["period_a", "period_b"],
        },
    },
]

# Map tool names to actual functions
TOOL_DISPATCH = {
    "get_account_balances": firefly_tools.get_account_balances,
    "get_spending_by_category": firefly_tools.get_spending_by_category,
    "get_net_worth": firefly_tools.get_net_worth,
    "get_transactions": firefly_tools.get_transactions,
    "get_budget_status": firefly_tools.get_budget_status,
    "get_recurring_charges": firefly_tools.get_recurring_charges,
    "get_spending_trend": firefly_tools.get_spending_trend,
    "get_goal_progress": firefly_tools.get_goal_progress,
    "compare_periods": firefly_tools.compare_periods,
}

SYSTEM_PROMPT = """You are Finance Phill, a household finance assistant for Erik and Janelle.
You have access to their real financial data via Firefly III. Answer questions concisely and
helpfully. Use the available tools to look up real data before answering — never guess at numbers.
When giving advice, be specific and actionable. Format currency as $X,XXX.XX."""


# ── Local Model (Ollama) ──────────────────────────────────────


def ollama_available() -> bool:
    """Check if Ollama is reachable."""
    try:
        req = Request(f"{OLLAMA_URL}/api/tags")
        with urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except (URLError, HTTPError, OSError):
        return False


def _summarize_balances(data: dict) -> str:
    """Compact text summary of account balances."""
    lines = []
    for a in data.get("accounts", []):
        lines.append(f"  {a['name']}: ${a['balance']:,.2f}")
    return "Balances:\n" + "\n".join(lines)


def _summarize_spending(data: dict) -> str:
    """Compact text summary of spending by category."""
    lines = [f"Spending ({data.get('period', 'current month')}): ${data.get('grand_total', 0):,.2f} total"]
    for c in data.get("categories", [])[:8]:
        lines.append(f"  {c['name']}: ${c['total']:,.2f} ({c['count']} txns)")
    return "\n".join(lines)


def _summarize_net_worth(data: dict) -> str:
    return f"Net Worth: ${data.get('net_worth', 0):,.2f} (Assets: ${data['assets']['total']:,.2f}, Liabilities: ${data['liabilities']['total']:,.2f})"


def _summarize_recurring(data: dict) -> str:
    lines = [f"Recurring charges ({data.get('count', 0)}), est. ${data.get('estimated_monthly_total', 0):,.2f}/mo:"]
    for r in data.get("recurring", [])[:5]:
        lines.append(f"  {r['merchant'][:30]}: ${r['avg_amount']:,.2f} ({r['frequency']})")
    return "\n".join(lines)


def call_local(query: str) -> dict:
    """Route to local Mistral model. Pre-fetches relevant data as compact text."""
    start = time.time()

    # Pre-fetch relevant data in compact form (minimize prompt tokens)
    context_parts = []
    q = query.lower()

    if any(w in q for w in ["balance", "account", "checking", "saving", "amex", "credit"]):
        context_parts.append(_summarize_balances(firefly_tools.get_account_balances()))

    if any(w in q for w in ["spend", "category", "groceries", "dining", "gas", "how much"]):
        context_parts.append(_summarize_spending(firefly_tools.get_spending_by_category()))

    if any(w in q for w in ["net worth", "assets", "liabilities", "worth"]):
        context_parts.append(_summarize_net_worth(firefly_tools.get_net_worth()))

    if any(w in q for w in ["recurring", "subscription", "monthly charge"]):
        context_parts.append(_summarize_recurring(firefly_tools.get_recurring_charges()))

    if any(w in q for w in ["budget"]):
        data = firefly_tools.get_budget_status()
        context_parts.append(f"Budget: {json.dumps(data.get('spending_summary', data.get('budgets', [])))[:300]}")

    if any(w in q for w in ["goal", "saving", "piggy"]):
        data = firefly_tools.get_goal_progress()
        context_parts.append(f"Goals: {json.dumps(data.get('goals', []))[:300]}")

    if not context_parts:
        context_parts.append(_summarize_balances(firefly_tools.get_account_balances()))

    prior = assemble_context_block("finance-agent", "finance", token_budget=512)
    if prior:
        context_parts.append(prior)

    context = "\n".join(context_parts)

    prompt = f"""Answer this finance question using the data below. Be concise (1-2 sentences).

{context}

Q: {query}
A:"""

    url = f"{OLLAMA_URL}/api/generate"
    payload = {
        "model": LOCAL_MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.2, "num_predict": 150},
    }

    body = json.dumps(payload).encode("utf-8")
    req = Request(url, method="POST", data=body)
    req.add_header("Content-Type", "application/json")

    with urlopen(req, timeout=300) as resp:
        result = json.loads(resp.read().decode("utf-8"))

    elapsed = time.time() - start
    response_text = result.get("response", "").strip()
    tokens = result.get("eval_count", 0) + result.get("prompt_eval_count", 0)

    return {
        "answer": response_text,
        "route": "local",
        "model": LOCAL_MODEL,
        "tokens": tokens,
        "cost_usd": 0.0,
        "latency_ms": int(elapsed * 1000),
    }


# ── Frontier Model (Claude via Anthropic API) ─────────────────


def call_frontier(query: str) -> dict:
    """Route to Claude with native tool use. Handles multi-turn tool call loop."""
    if not ANTHROPIC_API_KEY:
        return {
            "answer": "No ANTHROPIC_API_KEY configured. Add it to ~/.config/sovereign-finance/simplefin.env",
            "route": "frontier",
            "model": FRONTIER_MODEL,
            "tokens": 0,
            "cost_usd": 0.0,
            "latency_ms": 0,
        }

    start = time.time()
    total_input_tokens = 0
    total_output_tokens = 0

    prior = assemble_context_block("finance-agent", "finance", token_budget=768)
    system = SYSTEM_PROMPT + (f"\n\n{prior}" if prior else "")

    messages = [{"role": "user", "content": query}]

    # Tool-use loop: keep calling until Claude gives a final text response
    max_rounds = 5
    for _ in range(max_rounds):
        payload = {
            "model": FRONTIER_MODEL,
            "max_tokens": 1024,
            "system": system,
            "tools": TOOLS,
            "messages": messages,
        }

        body = json.dumps(payload).encode("utf-8")
        req = Request("https://api.anthropic.com/v1/messages", method="POST", data=body)
        req.add_header("Content-Type", "application/json")
        req.add_header("x-api-key", ANTHROPIC_API_KEY)
        req.add_header("anthropic-version", "2023-06-01")

        with urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read().decode("utf-8"))

        total_input_tokens += result.get("usage", {}).get("input_tokens", 0)
        total_output_tokens += result.get("usage", {}).get("output_tokens", 0)

        # Check if Claude wants to use tools
        stop_reason = result.get("stop_reason", "")
        content_blocks = result.get("content", [])

        if stop_reason == "tool_use":
            # Execute each tool call and build results
            assistant_msg = {"role": "assistant", "content": content_blocks}
            messages.append(assistant_msg)

            tool_results = []
            for block in content_blocks:
                if block.get("type") == "tool_use":
                    tool_name = block["name"]
                    tool_input = block.get("input", {})
                    tool_id = block["id"]

                    fn = TOOL_DISPATCH.get(tool_name)
                    if fn:
                        try:
                            tool_output = fn(**tool_input)
                            tool_results.append({
                                "type": "tool_result",
                                "tool_use_id": tool_id,
                                "content": json.dumps(tool_output, indent=2),
                            })
                        except Exception as e:
                            tool_results.append({
                                "type": "tool_result",
                                "tool_use_id": tool_id,
                                "content": f"Error: {e}",
                                "is_error": True,
                            })
                    else:
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": tool_id,
                            "content": f"Unknown tool: {tool_name}",
                            "is_error": True,
                        })

            messages.append({"role": "user", "content": tool_results})
        else:
            # Final text response
            text_parts = [b.get("text", "") for b in content_blocks if b.get("type") == "text"]
            answer = "\n".join(text_parts).strip()

            elapsed = time.time() - start

            # Cost estimate (Sonnet 4 pricing: $3/M input, $15/M output)
            cost = (total_input_tokens * 3.0 / 1_000_000) + (total_output_tokens * 15.0 / 1_000_000)

            return {
                "answer": answer,
                "route": "frontier",
                "model": FRONTIER_MODEL,
                "tokens": total_input_tokens + total_output_tokens,
                "input_tokens": total_input_tokens,
                "output_tokens": total_output_tokens,
                "cost_usd": round(cost, 6),
                "latency_ms": int(elapsed * 1000),
            }

    # Shouldn't get here, but safety net
    elapsed = time.time() - start
    return {
        "answer": "(max tool rounds reached)",
        "route": "frontier",
        "model": FRONTIER_MODEL,
        "tokens": total_input_tokens + total_output_tokens,
        "cost_usd": 0.0,
        "latency_ms": int(elapsed * 1000),
    }


# ── Router ─────────────────────────────────────────────────────


def route_query(query: str, force: str = None) -> dict:
    """Main entry point. Classifies query and routes to appropriate model.

    Applies a sensitivity gate: queries involving personal financial data
    are always routed locally, even with --force-frontier. The frontier
    path sends real Firefly III data to Anthropic's API.

    Args:
        query: Natural language finance question.
        force: "local" or "frontier" to override routing.

    Returns: {"answer", "route", "model", "tokens", "cost_usd", "latency_ms",
              "classification", "sensitivity"}
    """
    classification = classify_complexity(query)
    sensitivity = classify_sensitivity(query)

    if force:
        route = force
    else:
        route = classification["route"]

    # Sensitivity gate: personal financial data never leaves the machine
    if sensitivity["sensitive"] and route == "frontier":
        route = "local"
        classification["reasons"].append(
            "SENSITIVE: forced local (financial data must not leave machine)"
        )

    # Fallback: if local requested but Ollama is down
    if route == "local" and not ollama_available():
        if sensitivity["sensitive"]:
            # REFUSE: sensitive data cannot fall back to frontier
            return {
                "answer": (
                    "Ollama is not running. This query involves sensitive "
                    "financial data and cannot be sent to an external API. "
                    "Start Ollama with: ollama serve"
                ),
                "route": "refused",
                "model": "none",
                "tokens": 0,
                "cost_usd": 0.0,
                "latency_ms": 0,
                "classification": classification,
                "sensitivity": sensitivity,
            }
        elif ANTHROPIC_API_KEY:
            route = "frontier"
            classification["reasons"].append(
                "WARNING: Ollama unavailable, falling back to frontier (non-sensitive query)"
            )
        else:
            return {
                "answer": "Ollama is not running and no ANTHROPIC_API_KEY configured. Start Ollama with: ollama serve",
                "route": "local",
                "model": LOCAL_MODEL,
                "tokens": 0,
                "cost_usd": 0.0,
                "latency_ms": 0,
                "classification": classification,
                "sensitivity": sensitivity,
            }

    # Fallback: if frontier requested but no API key, use local
    if route == "frontier" and not ANTHROPIC_API_KEY:
        if ollama_available():
            route = "local"
            classification["reasons"].append("No ANTHROPIC_API_KEY, falling back to local")
        else:
            return {
                "answer": "No ANTHROPIC_API_KEY configured and Ollama is not running.",
                "route": "frontier",
                "model": FRONTIER_MODEL,
                "tokens": 0,
                "cost_usd": 0.0,
                "latency_ms": 0,
                "classification": classification,
                "sensitivity": sensitivity,
            }

    if route == "local":
        result = call_local(query)
    else:
        result = call_frontier(query)

    result["classification"] = classification
    result["sensitivity"] = sensitivity
    log_usage(result, query)
    return result


# ── Test Battery ───────────────────────────────────────────────


TEST_QUERIES = [
    # (query, expected_effective_route, expected_sensitive)
    # Financial queries — all sensitive, all forced local regardless of complexity
    ("what's my checking balance?", "local", True),
    ("how much did I spend on groceries this month?", "local", True),
    ("why is my spending higher than last month?", "local", True),
    ("should I cancel any subscriptions?", "local", True),
    ("what's my net worth?", "local", True),
    ("compare my spending trends and recommend where to cut back", "local", True),
    # Non-sensitive queries — can route to frontier based on complexity
    ("why should I invest in bonds vs equities and recommend a strategy", "frontier", False),
    ("should I invest in index funds or individual stocks?", "frontier", False),
    ("what is the capital of France?", "local", False),
]


def run_test(execute: bool = False):
    """Run test battery with sensitivity gate. If execute=True, actually calls models."""
    print("=" * 65)
    print("  ClawRouter Test Battery (with sensitivity gate)")
    print("=" * 65)
    print(f"  Local:    {LOCAL_MODEL} @ {OLLAMA_URL}")
    print(f"  Frontier: {FRONTIER_MODEL}")
    print(f"  Ollama:   {'UP' if ollama_available() else 'DOWN'}")
    print(f"  API Key:  {'configured' if ANTHROPIC_API_KEY else 'NOT SET'}")
    print()

    passed = 0
    failed = 0

    for query, expected_route, expected_sensitive in TEST_QUERIES:
        c = classify_complexity(query)
        s = classify_sensitivity(query)

        # Apply sensitivity gate to get effective route
        effective_route = c["route"]
        if s["sensitive"] and effective_route == "frontier":
            effective_route = "local"

        route_match = effective_route == expected_route
        sens_match = s["sensitive"] == expected_sensitive
        all_pass = route_match and sens_match
        icon = "PASS" if all_pass else "FAIL"
        if all_pass:
            passed += 1
        else:
            failed += 1

        gated = " [GATED]" if s["sensitive"] and c["route"] == "frontier" else ""
        sens_label = "SENSITIVE" if s["sensitive"] else "open"

        print(f"  [{icon}] \"{query}\"")
        print(f"         Complexity: {c['score']:.2f} -> {c['route'].upper()}")
        print(f"         Sensitivity: {sens_label}")
        print(f"         Effective:  {effective_route.upper()}{gated}" +
              (f" (expected {expected_route.upper()})" if not route_match else ""))

        if not sens_match:
            print(f"         SENS MISMATCH: got {s['sensitive']}, expected {expected_sensitive}")

        if execute:
            try:
                result = route_query(query)
                answer_preview = result["answer"][:120].replace("\n", " ")
                print(f"         Model: {result['model']}")
                print(f"         Tokens: {result['tokens']} | Cost: ${result['cost_usd']:.4f} | Latency: {result['latency_ms']}ms")
                print(f"         Answer: {answer_preview}...")
            except Exception as e:
                print(f"         ERROR: {e}")
        print()

    print(f"  Results: {passed}/{passed + failed} correct")
    if not execute:
        print("  (route-only mode — add --execute to call models)")


# ── Usage Stats Display ───────────────────────────────────────


def show_stats(entries: list, days: int = None):
    """Print a human-readable usage stats summary."""
    s = compute_usage_stats(entries)
    period = f"last {days} days" if days else "all time"

    print("=" * 55)
    print("  ClawRouter LLM Usage Stats")
    print("=" * 55)
    print(f"  Period: {period}")
    print(f"  Log:    {USAGE_LOG}")
    print()
    print(f"  Total Queries:    {s['total_queries']}")
    print(f"  Local (Mistral):  {s['local_queries']} ({s['local_pct']}%)")
    print(f"  Frontier (Claude): {s['frontier_queries']} ({s['frontier_pct']}%)")
    print()
    print(f"  Total Tokens:     {s['total_tokens']:,}")
    print(f"    Local:          {s['local_tokens']:,}")
    print(f"    Frontier:       {s['frontier_tokens']:,}")
    print()
    print(f"  Total Cost:       ${s['total_cost']:.4f}")
    print(f"  Avg Latency:")
    print(f"    Local:          {s['avg_latency_local']:,}ms")
    print(f"    Frontier:       {s['avg_latency_frontier']:,}ms")

    # Show daily breakdown if entries exist
    if entries:
        print()
        print("  Daily Breakdown:")
        print("  Date       | Local | Frontier | Cost")
        print("  -----------|-------|----------|--------")
        by_day = {}
        for e in entries:
            day = e.get("timestamp", "")[:10]
            if day not in by_day:
                by_day[day] = {"local": 0, "frontier": 0, "cost": 0.0}
            if e.get("route") == "local":
                by_day[day]["local"] += 1
            else:
                by_day[day]["frontier"] += 1
            by_day[day]["cost"] += e.get("cost_usd", 0)
        for day in sorted(by_day.keys()):
            d = by_day[day]
            print(f"  {day} | {d['local']:>5} | {d['frontier']:>8} | ${d['cost']:.4f}")
    print()


# ── CLI ────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="ClawRouter — route finance queries to local or frontier models"
    )
    parser.add_argument("query", nargs="?", help="Finance question to answer")
    parser.add_argument("--route-only", action="store_true",
                        help="Show routing decision without calling a model")
    parser.add_argument("--force-local", action="store_true",
                        help="Force local model (Ollama/Mistral)")
    parser.add_argument("--force-frontier", action="store_true",
                        help="Force frontier model (Claude)")
    parser.add_argument("--test", action="store_true",
                        help="Run routing test battery")
    parser.add_argument("--test-execute", action="store_true",
                        help="Run test battery with actual model calls")
    parser.add_argument("--stats", action="store_true",
                        help="Show LLM usage statistics")
    parser.add_argument("--stats-days", type=int, default=None,
                        help="Filter stats to last N days")
    parser.add_argument("--json", action="store_true",
                        help="Output raw JSON response")
    args = parser.parse_args()

    if args.test or args.test_execute:
        run_test(execute=args.test_execute)
        return

    if args.stats:
        entries = load_usage_log(days=args.stats_days)
        if args.json:
            print(json.dumps(compute_usage_stats(entries), indent=2))
        else:
            show_stats(entries, days=args.stats_days)
        return

    if not args.query:
        parser.print_help()
        return

    if args.route_only:
        c = classify_complexity(args.query)
        print(f"  Query:    \"{args.query}\"")
        print(f"  Score:    {c['score']:.2f}")
        print(f"  Route:    {c['route'].upper()}")
        print(f"  Reasons:")
        for r in c["reasons"]:
            print(f"            {r}")
        return

    force = None
    if args.force_local:
        force = "local"
    elif args.force_frontier:
        force = "frontier"

    try:
        result = route_query(args.query, force=force)
    except (URLError, HTTPError, OSError) as e:
        print(f"  Error: {e}", file=sys.stderr)
        sys.exit(1)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        c = result.get("classification", {})
        print()
        print(f"  Route:   {result['route'].upper()} ({result['model']})")
        print(f"  Score:   {c.get('score', '?')}")
        print(f"  Tokens:  {result['tokens']} | Cost: ${result['cost_usd']:.4f} | Latency: {result['latency_ms']}ms")
        print()
        print(result["answer"])
        print()


if __name__ == "__main__":
    main()
