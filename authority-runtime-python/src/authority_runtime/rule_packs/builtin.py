"""The hard rules carryall ships: destructive commands and credential-file access.

Exports: EXEC_POINT, READ_POINT, WRITE_POINT, BUILTIN_RULES, builtin_predicates,
builtin_pack.

These are deny rules with no configuration: a rule either fires or it does not.
Predicates follow the package convention (None = permitted, str = violation) and
fail closed — a context that omits `command` or `path` is a violation, because a
caller that forgets to pass the subject must not be treated as safe.

Matching is textual and deliberately conservative. It catches the literal forms
these rules name; it is not a sandbox and cannot see through shell indirection
(variables, base64, eval). Treat it as one layer, not the boundary.
"""

from __future__ import annotations

import re
from typing import Any, List, Mapping, Optional

from .pack import Rule, RulePack
from .registry import Predicate, PredicateRegistry

EXEC_POINT = "pre-exec"
READ_POINT = "pre-file-read"
WRITE_POINT = "pre-file-write"

_RECURSIVE_RM = re.compile(r"(?:^|[;&|]\s*|\s)rm\s+(?:-[a-z]*r[a-z]*|--recursive)\b", re.I)
_RAW_DISK = re.compile(
    r"\bmkfs(\.\w+)?\b|\bdd\b[^|;&]*\bof=/dev/|>\s*/dev/(disk|sd|nvme|hd)|"
    r"\bdiskutil\s+(erase|partition|reformat)|\bfdisk\b|\bnewfs\b|\bzpool\s+destroy\b",
    re.I,
)
_FORK_BOMB = re.compile(r":\s*\(\s*\)\s*\{.*\|.*&.*\}\s*;?\s*:")
_PRIV_ESC = re.compile(
    r"(?:^|[;&|]\s*)\s*(?:sudo|doas)\s|(?:^|[;&|]\s*)\s*su\s+-(?:\s|$)", re.I)
_PIPE_TO_SHELL = re.compile(r"\b(curl|wget|fetch)\b[^|]*\|\s*(sudo\s+)?(ba|z|k|da|fi)?sh\b", re.I)
_FORCE_PUSH = re.compile(r"\bgit\s+push\b[^;&|]*?(?<!-with-lease)(\s--force(?!-with-lease)|\s-f)\b", re.I)

_CREDENTIAL_DIRS = ("/.ssh/", "/.aws/", "/.gnupg/", "/.config/gh/", "/Library/Keychains/")
_ENV_FILE = re.compile(r"(?:^|/)\.env(\.[\w-]+)?$")


def _command(context: Mapping[str, Any]) -> Optional[str]:
    """Return the command string, or None when the caller supplied none."""
    value = context.get("command")
    return value if isinstance(value, str) and value.strip() else None


def _path(context: Mapping[str, Any]) -> Optional[str]:
    """Return the path, or None when the caller supplied none."""
    value = context.get("path")
    return value if isinstance(value, str) and value.strip() else None


def _match_command(context: Mapping[str, Any], pattern: "re.Pattern[str]",
                   label: str) -> Optional[str]:
    """Shared command-rule body: missing command is itself a violation."""
    command = _command(context)
    if command is None:
        return "No 'command' in context; refusing to evaluate a shell rule without its subject"
    hit = pattern.search(command)
    return f"{label}: {hit.group(0).strip()!r}" if hit else None


def recursive_delete(context: Mapping[str, Any]) -> Optional[str]:
    """Deny recursive rm in any flag spelling."""
    return _match_command(context, _RECURSIVE_RM, "recursive delete")


def raw_disk_write(context: Mapping[str, Any]) -> Optional[str]:
    """Deny mkfs, dd to a device, redirects into /dev, and partition tools."""
    return _match_command(context, _RAW_DISK, "raw disk write")


def fork_bomb(context: Mapping[str, Any]) -> Optional[str]:
    """Deny the classic shell fork bomb."""
    return _match_command(context, _FORK_BOMB, "fork bomb")


def privilege_escalation(context: Mapping[str, Any]) -> Optional[str]:
    """Deny sudo/doas/su."""
    return _match_command(context, _PRIV_ESC, "privilege escalation")


def pipe_to_shell(context: Mapping[str, Any]) -> Optional[str]:
    """Deny curl|sh and friends: remote code executed unreviewed."""
    return _match_command(context, _PIPE_TO_SHELL, "remote script piped to a shell")


def force_push(context: Mapping[str, Any]) -> Optional[str]:
    """Deny git push --force / -f. --force-with-lease is allowed."""
    return _match_command(context, _FORCE_PUSH, "force push")


def credential_path(context: Mapping[str, Any]) -> Optional[str]:
    """Deny any access to key, cloud-credential, GPG, gh or Keychain directories."""
    path = _path(context)
    if path is None:
        return "No 'path' in context; refusing to evaluate a path rule without its subject"
    normalized = path.replace("\\", "/")
    if normalized.startswith("~"):
        normalized = "/home/user" + normalized[1:]
    if not normalized.startswith("/"):
        normalized = "/cwd/" + normalized
    for directory in _CREDENTIAL_DIRS:
        if directory in normalized:
            return f"credential store {directory.strip('/')!r} in path {path!r}"
    return None


def env_file_path(context: Mapping[str, Any]) -> Optional[str]:
    """Deny .env and .env.<suffix> files, read or write."""
    path = _path(context)
    if path is None:
        return "No 'path' in context; refusing to evaluate a path rule without its subject"
    return f"env file {path!r}" if _ENV_FILE.search(path.replace("\\", "/")) else None


_PREDICATES = {
    "recursive_delete": recursive_delete,
    "raw_disk_write": raw_disk_write,
    "fork_bomb": fork_bomb,
    "privilege_escalation": privilege_escalation,
    "pipe_to_shell": pipe_to_shell,
    "force_push": force_push,
    "credential_path": credential_path,
    "env_file_path": env_file_path,
}

BUILTIN_RULES: List[Rule] = [
    Rule(id="destructive-recursive-delete", number=1, predicate="recursive_delete",
         description="Recursive delete is never run; remove files individually by name",
         enforcement=[EXEC_POINT]),
    Rule(id="destructive-raw-disk-write", number=2, predicate="raw_disk_write",
         description="No mkfs, dd to a device, or partition-table edits",
         enforcement=[EXEC_POINT]),
    Rule(id="destructive-fork-bomb", number=3, predicate="fork_bomb",
         description="No fork bombs", enforcement=[EXEC_POINT]),
    Rule(id="privilege-escalation", number=4, predicate="privilege_escalation",
         description="No sudo, doas, or su", enforcement=[EXEC_POINT]),
    Rule(id="remote-script-pipe-to-shell", number=5, predicate="pipe_to_shell",
         description="No curl|sh: remote code must be reviewed before it runs",
         enforcement=[EXEC_POINT]),
    Rule(id="git-force-push", number=6, predicate="force_push",
         description="No git push --force; --force-with-lease is allowed",
         enforcement=[EXEC_POINT]),
    Rule(id="credential-store-access", number=7, predicate="credential_path",
         description="No read or write of ~/.ssh, ~/.aws, ~/.gnupg, ~/.config/gh, Keychains",
         enforcement=[READ_POINT, WRITE_POINT]),
    Rule(id="env-file-access", number=8, predicate="env_file_path",
         description="No read or write of .env files",
         enforcement=[READ_POINT, WRITE_POINT]),
]


def builtin_predicates() -> PredicateRegistry:
    """A fresh registry holding the builtin predicates only."""
    registry = PredicateRegistry()
    for name, fn in _PREDICATES.items():
        registry.register(name, fn)
    return registry


def builtin_pack(registry: Optional[PredicateRegistry] = None) -> RulePack:
    """The shipped hard-rule pack. Uses its own registry unless one is supplied."""
    if registry is None:
        registry = builtin_predicates()
    else:
        for name, fn in _PREDICATES.items():
            if not registry.has(name):
                registry.register(name, fn)
    return RulePack(rules=list(BUILTIN_RULES), registry=registry)


__all__ = [
    "EXEC_POINT", "READ_POINT", "WRITE_POINT", "BUILTIN_RULES",
    "builtin_predicates", "builtin_pack",
    *sorted(_PREDICATES),
]

_: Predicate = recursive_delete  # type check: predicates match the registry contract
