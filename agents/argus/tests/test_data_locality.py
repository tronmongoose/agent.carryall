"""ARGUS data locality scanner tests.

7 tests using stdlib unittest and tempfile fixtures.
Run with: PYTHONPATH=. python -m unittest agents.argus.tests.test_data_locality -v
"""

import json
import os
import shutil
import tempfile
import unittest

import yaml

from agents.argus.models.finding import Finding, FindingType, Severity
from agents.argus.scanner.data_locality import DataLocalityScanner, ScanConfig


def _create_test_config(base_dir: str) -> str:
    """Create a minimal domains.yaml for testing."""
    financial_dir = os.path.join(base_dir, "vaults", "financial")
    health_dir = os.path.join(base_dir, "vaults", "health")
    orchestration_dir = os.path.join(base_dir, "vaults", "orchestration")

    os.makedirs(financial_dir, exist_ok=True)
    os.makedirs(health_dir, exist_ok=True)
    os.makedirs(orchestration_dir, exist_ok=True)

    config = {
        "version": "1.0",
        "domains": {
            "financial": {
                "description": "Financial data",
                "authorized_agents": ["finance-agent"],
                "vault_paths": [financial_dir],
                "sensitive_patterns": [
                    {
                        "type": "credit_card",
                        "regex": r"\b(?:4[0-9]{12}(?:[0-9]{3})?|5[1-5][0-9]{14}|3[47][0-9]{13})\b",
                        "severity": "CRITICAL",
                    },
                ],
            },
            "health": {
                "description": "Health data",
                "authorized_agents": ["health-agent"],
                "vault_paths": [health_dir],
                "sensitive_patterns": [
                    {
                        "type": "ssn",
                        "regex": r"\b(?!000|666|9\d{2})\d{3}-(?!00)\d{2}-(?!0000)\d{4}\b",
                        "severity": "CRITICAL",
                    },
                ],
            },
            "orchestration": {
                "description": "Agent config",
                "authorized_agents": ["slos.carryall"],
                "vault_paths": [orchestration_dir],
                "forbidden_pattern_sources": ["financial", "health"],
            },
        },
        "cross_domain_allowances": [],
    }

    config_path = os.path.join(base_dir, "domains.yaml")
    with open(config_path, "w") as f:
        yaml.dump(config, f)

    return config_path


class TestDataLocalityScanner(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="argus_test_")
        self.config_path = _create_test_config(self.test_dir)
        self.audit_log = os.path.join(self.test_dir, "audit.jsonl")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_clean_scan_no_findings(self):
        """Clean vault directories produce zero findings."""
        # Write benign content to each vault
        for subdir in ["financial", "health", "orchestration"]:
            path = os.path.join(self.test_dir, "vaults", subdir, "readme.md")
            with open(path, "w") as f:
                f.write("This is a clean document with no sensitive data.\n")

        config = ScanConfig(self.config_path)
        scanner = DataLocalityScanner(config)
        findings = scanner.run()

        self.assertEqual(len(findings), 0)

    def test_credit_card_in_orchestration_is_critical(self):
        """Credit card number in orchestration vault → CRITICAL finding."""
        orch_file = os.path.join(
            self.test_dir, "vaults", "orchestration", "agent-config.yaml"
        )
        with open(orch_file, "w") as f:
            f.write("payment_card: 4111111111111111\n")

        config = ScanConfig(self.config_path)
        scanner = DataLocalityScanner(config)
        findings = scanner.run()

        self.assertGreater(len(findings), 0)
        cc_findings = [f for f in findings if f.pattern_type == "credit_card"]
        self.assertGreater(len(cc_findings), 0)
        self.assertEqual(cc_findings[0].severity, Severity.CRITICAL)
        self.assertEqual(cc_findings[0].detected_in_domain, "orchestration")
        self.assertEqual(cc_findings[0].source_domain, "financial")

    def test_credit_card_in_home_domain_no_finding(self):
        """Credit card in its home financial vault → no finding."""
        fin_file = os.path.join(
            self.test_dir, "vaults", "financial", "transactions.md"
        )
        with open(fin_file, "w") as f:
            f.write("Card ending in 4111111111111111 was charged $50.\n")

        config = ScanConfig(self.config_path)
        scanner = DataLocalityScanner(config)
        findings = scanner.run()

        # Should not flag credit card in its own domain
        cc_findings = [f for f in findings if f.pattern_type == "credit_card"]
        self.assertEqual(len(cc_findings), 0)

    def test_finding_written_to_audit_log_as_json(self):
        """Each finding is written to audit log as valid JSON."""
        orch_file = os.path.join(
            self.test_dir, "vaults", "orchestration", "leak.txt"
        )
        with open(orch_file, "w") as f:
            f.write("cc: 5100000000000008\n")

        config = ScanConfig(self.config_path)
        scanner = DataLocalityScanner(config)
        scanner.run(audit_log_path=self.audit_log)

        self.assertTrue(os.path.exists(self.audit_log))
        with open(self.audit_log) as f:
            lines = f.readlines()

        self.assertGreater(len(lines), 0)
        for line in lines:
            parsed = json.loads(line.strip())
            self.assertIn("finding_id", parsed)
            self.assertIn("severity", parsed)
            self.assertIn("finding_type", parsed)

    def test_matched_excerpt_is_redacted(self):
        """matched_excerpt is redacted and does not equal raw value."""
        raw_cc = "4111111111111111"
        orch_file = os.path.join(
            self.test_dir, "vaults", "orchestration", "leak.txt"
        )
        with open(orch_file, "w") as f:
            f.write(f"number: {raw_cc}\n")

        config = ScanConfig(self.config_path)
        scanner = DataLocalityScanner(config)
        findings = scanner.run()

        self.assertGreater(len(findings), 0)
        excerpt = findings[0].matched_excerpt
        self.assertNotEqual(excerpt, raw_cc)
        self.assertIn("...", excerpt)

    def test_binary_files_skipped(self):
        """Binary files (.png etc.) are skipped without error."""
        png_file = os.path.join(
            self.test_dir, "vaults", "orchestration", "image.png"
        )
        # Write bytes that look like a credit card if decoded
        with open(png_file, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n")
            f.write(b"4111111111111111")

        config = ScanConfig(self.config_path)
        scanner = DataLocalityScanner(config)
        findings = scanner.run()

        # No findings from binary file
        png_findings = [f for f in findings if "image.png" in f.source_path]
        self.assertEqual(len(png_findings), 0)

    def test_symlinks_skipped(self):
        """Symlinks in vault directories are skipped without error."""
        target_file = os.path.join(
            self.test_dir, "vaults", "financial", "real.txt"
        )
        with open(target_file, "w") as f:
            f.write("4111111111111111\n")

        symlink_path = os.path.join(
            self.test_dir, "vaults", "orchestration", "link.txt"
        )
        os.symlink(target_file, symlink_path)

        config = ScanConfig(self.config_path)
        scanner = DataLocalityScanner(config)
        findings = scanner.run()

        # Symlink itself should not generate findings
        link_findings = [f for f in findings if "link.txt" in f.source_path]
        self.assertEqual(len(link_findings), 0)


if __name__ == "__main__":
    unittest.main()
