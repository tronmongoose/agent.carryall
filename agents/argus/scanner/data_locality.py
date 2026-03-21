"""Cross-domain data locality scanner for SLOS vaults.

Walks vault directories and detects sensitive patterns appearing outside
their home domain. Never follows symlinks. Uses mmap for large files.
Writes findings to an append-only, fsync'd audit log.
"""

import mmap
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

from agents.argus.models.finding import Finding, FindingType, Severity

BINARY_EXTENSIONS = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".mp4", ".mp3", ".zip", ".gz",
    ".tar", ".bin", ".so", ".dylib", ".pyc", ".db", ".sqlite", ".sqlite3",
    ".ico", ".woff", ".woff2", ".ttf", ".eot", ".pdf", ".bz2", ".xz",
    ".7z", ".rar", ".dmg", ".iso", ".wav", ".ogg", ".webp", ".avif",
})

LARGE_FILE_THRESHOLD = 10 * 1024 * 1024  # 10 MB

# Pattern types that require Luhn checksum validation
LUHN_VALIDATED_PATTERNS = frozenset({"credit_card"})


def _passes_luhn(number_str: str) -> bool:
    """Validate a numeric string passes the Luhn checksum.

    Real credit card numbers always pass Luhn. Random numeric IDs
    (TDH scores, timestamps, UUIDs) almost never do.
    """
    digits = [int(d) for d in number_str if d.isdigit()]
    if len(digits) < 13:
        return False
    checksum = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        checksum += d
    return checksum % 10 == 0


@dataclass
class PatternDef:
    """A single sensitive pattern with its compiled regex."""
    pattern_type: str
    regex: re.Pattern
    severity: Severity


@dataclass
class DomainConfig:
    """Configuration for one security domain."""
    name: str
    description: str
    authorized_agents: list[str]
    vault_paths: list[str]
    patterns: list[PatternDef] = field(default_factory=list)
    forbidden_pattern_sources: list[str] = field(default_factory=list)

    @classmethod
    def from_yaml(cls, name: str, data: dict) -> "DomainConfig":
        patterns = []
        for p in data.get("sensitive_patterns", []):
            regex_str = p.get("regex")
            if not regex_str:
                # Convert keywords list to regex
                keywords = p.get("keywords", [])
                if keywords:
                    regex_str = r"\b(?:" + "|".join(re.escape(k) for k in keywords) + r")\b"
                else:
                    continue
            patterns.append(PatternDef(
                pattern_type=p["type"],
                regex=re.compile(regex_str),
                severity=Severity(p["severity"]),
            ))
        return cls(
            name=name,
            description=data.get("description", ""),
            authorized_agents=data.get("authorized_agents", []),
            vault_paths=data.get("vault_paths", []),
            patterns=patterns,
            forbidden_pattern_sources=data.get("forbidden_pattern_sources", []),
        )


@dataclass
class CrossDomainAllowance:
    """An explicit permission for a pattern type to cross domain boundaries."""
    from_domain: str
    to_domain: str
    allowed_types: list[str]


class ScanConfig:
    """Parsed domains.yaml configuration."""

    def __init__(self, config_path: str):
        with open(config_path) as f:
            raw = yaml.safe_load(f)

        self.domains: dict[str, DomainConfig] = {}
        for name, data in raw.get("domains", {}).items():
            self.domains[name] = DomainConfig.from_yaml(name, data)

        self.allowances: list[CrossDomainAllowance] = []
        for a in raw.get("cross_domain_allowances", []):
            self.allowances.append(CrossDomainAllowance(
                from_domain=a["from"],
                to_domain=a["to"],
                allowed_types=a.get("allowed_types", []),
            ))

        # Paths to exclude from scanning (e.g., ARGUS's own files)
        self._exclude_paths: list[str] = []
        for ep in raw.get("exclude_paths", []):
            self._exclude_paths.append(
                str(Path(ep).expanduser().resolve())
            )

        # Build path-to-domain lookup (longest prefix match)
        self._path_map: list[tuple[str, str]] = []
        for domain in self.domains.values():
            for vp in domain.vault_paths:
                resolved = str(Path(vp).expanduser().resolve())
                self._path_map.append((resolved, domain.name))
        # Sort by path length descending for longest-prefix-first matching
        self._path_map.sort(key=lambda x: len(x[0]), reverse=True)

    def domain_for_path(self, filepath: str) -> Optional[str]:
        """Return the domain name for a given file path, or None if outside all domains."""
        resolved = str(Path(filepath).resolve())
        for prefix, domain_name in self._path_map:
            if resolved.startswith(prefix):
                return domain_name
        return None

    def is_excluded(self, filepath: str) -> bool:
        """Check if a file path falls within an excluded directory."""
        resolved = str(Path(filepath).resolve())
        return any(resolved.startswith(ep) for ep in self._exclude_paths)

    def is_allowed_cross_domain(self, from_domain: str, to_domain: str, pattern_type: str) -> bool:
        """Check if a cross-domain pattern match is explicitly allowed."""
        for a in self.allowances:
            if a.from_domain == from_domain and a.to_domain == to_domain:
                if pattern_type in a.allowed_types:
                    return True
        return False


class DataLocalityScanner:
    """Scans vault directories for sensitive data outside its home domain."""

    def __init__(self, config: ScanConfig):
        self.config = config

    def run(self, audit_log_path: Optional[str] = None) -> list[Finding]:
        """Execute a full scan across all configured domains.

        If audit_log_path is provided, each finding is appended with fsync.
        Returns all findings.
        """
        findings: list[Finding] = []
        audit_fd = None

        if audit_log_path:
            audit_fd = os.open(
                audit_log_path,
                os.O_WRONLY | os.O_CREAT | os.O_APPEND,
                0o600,
            )

        try:
            for domain in self.config.domains.values():
                for vault_path in domain.vault_paths:
                    expanded = str(Path(vault_path).expanduser())
                    if not os.path.isdir(expanded):
                        continue
                    for dirpath, _dirnames, filenames in os.walk(expanded, followlinks=False):
                        for filename in filenames:
                            filepath = os.path.join(dirpath, filename)
                            file_findings = self._scan_file(filepath, domain.name)
                            for f in file_findings:
                                findings.append(f)
                                if audit_fd is not None:
                                    line = f.to_log_line() + "\n"
                                    os.write(audit_fd, line.encode())
                                    os.fsync(audit_fd)
        finally:
            if audit_fd is not None:
                os.close(audit_fd)

        return findings

    def _scan_file(self, filepath: str, scanned_domain: str) -> list[Finding]:
        """Scan a single file for cross-domain pattern violations."""
        # Skip symlinks
        if os.path.islink(filepath):
            return []

        # Skip binary extensions
        _, ext = os.path.splitext(filepath)
        if ext.lower() in BINARY_EXTENSIONS:
            return []

        # Skip excluded paths (e.g., ARGUS's own config/test files)
        if self.config.is_excluded(filepath):
            return []

        # Read file content
        content = self._read_file(filepath)
        if content is None:
            return []

        findings = []
        file_domain = self.config.domain_for_path(filepath)

        for domain in self.config.domains.values():
            # Skip scanning a domain's own patterns against its own files
            if domain.name == file_domain:
                # But check forbidden_pattern_sources — patterns from these
                # source domains must never appear, even in the domain's own files
                if scanned_domain not in domain.forbidden_pattern_sources:
                    continue

            # Check if this domain's patterns are forbidden in the scanned domain
            scanned_domain_config = self.config.domains.get(scanned_domain)
            is_forbidden_source = (
                scanned_domain_config is not None
                and domain.name in scanned_domain_config.forbidden_pattern_sources
            )

            for pattern in domain.patterns:
                for match in pattern.regex.finditer(content):
                    matched_value = match.group(0)

                    # Luhn checksum validation for credit card patterns
                    if pattern.pattern_type in LUHN_VALIDATED_PATTERNS:
                        if not _passes_luhn(matched_value):
                            continue

                    # Check cross-domain allowances
                    if self.config.is_allowed_cross_domain(
                        domain.name, scanned_domain, pattern.pattern_type
                    ):
                        continue

                    finding_type = (
                        FindingType.FORBIDDEN_PATTERN if is_forbidden_source
                        else FindingType.DATA_IN_WRONG_DOMAIN
                    )

                    finding = Finding.create(
                        finding_type=finding_type,
                        severity=pattern.severity,
                        source_path=filepath,
                        source_domain=domain.name,
                        detected_in_domain=scanned_domain,
                        pattern_type=pattern.pattern_type,
                        matched_value=matched_value,
                        description=(
                            f"{pattern.pattern_type} from {domain.name} "
                            f"found in {scanned_domain} domain: {filepath}"
                        ),
                    )
                    findings.append(finding)

        return findings

    def _read_file(self, filepath: str) -> Optional[str]:
        """Read file content. Uses mmap for files > 10MB."""
        try:
            size = os.path.getsize(filepath)
            if size == 0:
                return ""
            if size > LARGE_FILE_THRESHOLD:
                return self._mmap_read(filepath, size)
            with open(filepath, "r", errors="replace") as f:
                return f.read()
        except (OSError, ValueError):
            return None

    def _mmap_read(self, filepath: str, size: int) -> Optional[str]:
        """Memory-map a large file for scanning."""
        try:
            fd = os.open(filepath, os.O_RDONLY)
            try:
                mm = mmap.mmap(fd, size, access=mmap.ACCESS_READ)
                try:
                    return mm[:].decode("utf-8", errors="replace")
                finally:
                    mm.close()
            finally:
                os.close(fd)
        except (OSError, ValueError):
            return None
