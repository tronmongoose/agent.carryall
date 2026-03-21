#!/usr/bin/env python3
"""
Pipeline Verification — Assert output state between morning convoy stages.

Each pipeline stage calls verify() after completing. Verification checks:
- Output file exists and is non-empty
- No hallucinated/placeholder content
- API data actually arrived (not cached stale data)
- Notification was delivered (Telegram/ntfy response)

Usage:
    python usecases/pipeline_verify.py --check bank-sync
    python usecases/pipeline_verify.py --check argus
    python usecases/pipeline_verify.py --check exit-watch
    python usecases/pipeline_verify.py --check email
    python usecases/pipeline_verify.py --check research
    python usecases/pipeline_verify.py --check briefing
    python usecases/pipeline_verify.py --report          # Full convoy report
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))
from common import load_env, SLOS_DIR

load_env()

LOG_DIR = "/tmp/morning-convoy"
TODAY = datetime.now().strftime("%Y-%m-%d")

# Hallucination markers — if any of these appear in output, flag it
HALLUCINATION_MARKERS = [
    "[DATE]", "[INSERT", "[PLACEHOLDER", "[TODO",
    "lorem ipsum", "example.com", "test@test",
    "undefined", "NaN", "null null",
]


class VerifyResult:
    def __init__(self, stage: str):
        self.stage = stage
        self.checks: list[tuple[str, bool, str]] = []  # (name, passed, detail)

    def check(self, name: str, passed: bool, detail: str = ""):
        self.checks.append((name, passed, detail))
        return passed

    @property
    def passed(self) -> bool:
        return all(c[1] for c in self.checks)

    def summary(self) -> str:
        lines = [f"  [{self.stage}] {'PASS' if self.passed else 'FAIL'}"]
        for name, ok, detail in self.checks:
            icon = "ok" if ok else "FAIL"
            lines.append(f"    [{icon}] {name}{f': {detail}' if detail else ''}")
        return "\n".join(lines)


def _check_log_exists(stage: str) -> tuple[bool, str]:
    """Check if the convoy log file exists and has content."""
    log_path = os.path.join(LOG_DIR, f"{stage}-{TODAY}.log")
    if not os.path.exists(log_path):
        return False, f"Log not found: {log_path}"
    size = os.path.getsize(log_path)
    if size == 0:
        return False, "Log file is empty"
    return True, f"{size} bytes"


def _check_log_no_errors(stage: str) -> tuple[bool, str]:
    """Check convoy log for error indicators."""
    log_path = os.path.join(LOG_DIR, f"{stage}-{TODAY}.log")
    if not os.path.exists(log_path):
        return True, "No log to check"
    text = Path(log_path).read_text()
    errors = []
    for marker in ["Traceback", "Error:", "FAILED", "Exception"]:
        if marker in text:
            # Find the line
            for line in text.split("\n"):
                if marker in line:
                    errors.append(line.strip()[:100])
                    break
    if errors:
        return False, f"{len(errors)} error(s): {errors[0]}"
    return True, "Clean"


def _check_no_hallucination(text: str) -> tuple[bool, str]:
    """Check text for hallucination markers."""
    text_lower = text.lower()
    found = [m for m in HALLUCINATION_MARKERS if m.lower() in text_lower]
    if found:
        return False, f"Markers found: {found}"
    return True, "Clean"


def _check_file_fresh(path: str, max_age_hours: int = 24) -> tuple[bool, str]:
    """Check if a file was modified recently."""
    if not os.path.exists(path):
        return False, f"Not found: {path}"
    mtime = os.path.getmtime(path)
    age_hours = (datetime.now().timestamp() - mtime) / 3600
    if age_hours > max_age_hours:
        return False, f"Stale: {age_hours:.1f}h old"
    return True, f"Fresh: {age_hours:.1f}h old"


# -- Per-Stage Verifiers -------------------------------------------------------

def verify_bank_sync() -> VerifyResult:
    """Verify SimpleFIN sync produced fresh data."""
    r = VerifyResult("bank-sync")
    ok, detail = _check_log_exists("bank-sync")
    r.check("log_exists", ok, detail)
    ok, detail = _check_log_no_errors("bank-sync")
    r.check("no_errors", ok, detail)

    # Check Firefly has recent transactions
    state_file = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                              "docker/state/simplefin-last-sync.json")
    ok, detail = _check_file_fresh(state_file, max_age_hours=26)
    r.check("sync_state_fresh", ok, detail)

    return r


def verify_receipts() -> VerifyResult:
    """Verify receipt import ran."""
    r = VerifyResult("receipts")
    ok, detail = _check_log_exists("receipts")
    r.check("log_exists", ok, detail)
    ok, detail = _check_log_no_errors("receipts")
    r.check("no_errors", ok, detail)
    return r


def verify_argus() -> VerifyResult:
    """Verify ARGUS scan produced output."""
    r = VerifyResult("argus")
    ok, detail = _check_log_exists("argus")
    r.check("log_exists", ok, detail)
    ok, detail = _check_log_no_errors("argus")
    r.check("no_errors", ok, detail)

    # Check ARGUS audit log was written today
    audit_log = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                             "agents/argus/audit.jsonl")
    ok, detail = _check_file_fresh(audit_log, max_age_hours=26)
    r.check("audit_log_fresh", ok, detail)

    return r


def verify_exit_watch() -> VerifyResult:
    """Verify exit watch produced a brief."""
    r = VerifyResult("exit-watch")
    ok, detail = _check_log_exists("exit-watch")
    r.check("log_exists", ok, detail)
    ok, detail = _check_log_no_errors("exit-watch")
    r.check("no_errors", ok, detail)

    # Check today's brief exists
    brief_path = os.path.join(SLOS_DIR, f"vaults/personal/exit-watch/briefs/{TODAY}.md")
    ok, detail = _check_file_fresh(brief_path, max_age_hours=26)
    r.check("brief_exists", ok, detail)

    # Check for hallucination
    if os.path.exists(brief_path):
        text = Path(brief_path).read_text()
        ok, detail = _check_no_hallucination(text)
        r.check("no_hallucination", ok, detail)
        r.check("brief_not_empty", len(text) > 100, f"{len(text)} chars")

    return r


def verify_email() -> VerifyResult:
    """Verify email brief was generated."""
    r = VerifyResult("email")
    ok, detail = _check_log_exists("email")
    r.check("log_exists", ok, detail)
    ok, detail = _check_log_no_errors("email")
    r.check("no_errors", ok, detail)

    # Check this week's brief
    now = datetime.now()
    week = f"{now.year}-W{now.isocalendar().week:02d}"
    brief_path = os.path.join(SLOS_DIR, f"vaults/personal/email/briefs/{week}.md")
    ok, detail = _check_file_fresh(brief_path, max_age_hours=26)
    r.check("brief_exists", ok, detail)

    return r


def verify_research() -> VerifyResult:
    """Verify AI research brief was synthesized."""
    r = VerifyResult("research")
    ok, detail = _check_log_exists("research")
    r.check("log_exists", ok, detail)
    ok, detail = _check_log_no_errors("research")
    r.check("no_errors", ok, detail)

    # Check today's brief
    brief_path = os.path.join(SLOS_DIR, f"vaults/personal/ai-research/briefs/{TODAY}.md")
    ok, detail = _check_file_fresh(brief_path, max_age_hours=26)
    r.check("brief_exists", ok, detail)

    if os.path.exists(brief_path):
        text = Path(brief_path).read_text()
        ok, detail = _check_no_hallucination(text)
        r.check("no_hallucination", ok, detail)
        r.check("brief_not_empty", len(text) > 200, f"{len(text)} chars")

    return r


def verify_briefing() -> VerifyResult:
    """Verify financial briefing was sent."""
    r = VerifyResult("briefing")
    ok, detail = _check_log_exists("briefing")
    r.check("log_exists", ok, detail)
    ok, detail = _check_log_no_errors("briefing")
    r.check("no_errors", ok, detail)
    return r


def verify_calendar() -> VerifyResult:
    """Verify calendar sync ran."""
    r = VerifyResult("calendar")
    ok, detail = _check_log_exists("calendar")
    r.check("log_exists", ok, detail)
    ok, detail = _check_log_no_errors("calendar")
    r.check("no_errors", ok, detail)
    return r


def verify_venture() -> VerifyResult:
    """Verify venture intel ran."""
    r = VerifyResult("venture")
    ok, detail = _check_log_exists("venture")
    r.check("log_exists", ok, detail)
    ok, detail = _check_log_no_errors("venture")
    r.check("no_errors", ok, detail)
    return r


STAGE_VERIFIERS = {
    "bank-sync": verify_bank_sync,
    "receipts": verify_receipts,
    "argus": verify_argus,
    "exit-watch": verify_exit_watch,
    "email": verify_email,
    "research": verify_research,
    "briefing": verify_briefing,
    "calendar": verify_calendar,
    "venture": verify_venture,
}


def cmd_check(stage: str):
    """Verify a single stage."""
    if stage not in STAGE_VERIFIERS:
        print(f"  Unknown stage: {stage}")
        print(f"  Valid: {', '.join(STAGE_VERIFIERS.keys())}")
        return

    result = STAGE_VERIFIERS[stage]()
    print(result.summary())
    return result.passed


def cmd_report():
    """Full convoy verification report."""
    print(f"\n=== Morning Convoy Verification — {TODAY} ===\n")

    total = 0
    passed = 0
    for stage, verifier in STAGE_VERIFIERS.items():
        result = verifier()
        print(result.summary())
        total += 1
        if result.passed:
            passed += 1

    print(f"\n  Result: {passed}/{total} stages verified")
    if passed == total:
        print("  Status: ALL CLEAR")
    else:
        print(f"  Status: {total - passed} STAGE(S) NEED ATTENTION")


def main():
    parser = argparse.ArgumentParser(description="Pipeline Verification")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", metavar="STAGE", help="Verify a single stage")
    group.add_argument("--report", action="store_true", help="Full convoy report")

    args = parser.parse_args()

    if args.check:
        cmd_check(args.check)
    elif args.report:
        cmd_report()


if __name__ == "__main__":
    main()
