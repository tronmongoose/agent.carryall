"""Test helper: import the vendored mayor/clawrouter.py with its missing siblings stubbed."""

import importlib.util
import sys
import types
from pathlib import Path
from typing import Any, List, Optional

REPO = Path(__file__).resolve().parents[2]


def load_clawrouter(monkeypatch, tmp_path, calls: Optional[List[str]] = None) -> types.ModuleType:
    """Load mayor/clawrouter.py with stubbed sibling modules; env must be set first."""
    stubs = {
        "get_account_balances": {"accounts": [{"name": "Checking", "balance": 1234.56}]},
        "get_net_worth": {"assets": {"total": 1.0}, "liabilities": {"total": 0.0}},
    }

    class _Firefly(types.ModuleType):
        def __getattr__(self, name: str) -> Any:
            def stub(*a: Any, **k: Any) -> Any:
                if calls is not None:
                    calls.append(name)
                return stubs.get(name, {})

            return stub

    firefly = _Firefly("firefly_tools")
    common = types.ModuleType("common")
    common.SLOS_DIR = str(tmp_path)  # type: ignore[attr-defined]
    ctx = types.ModuleType("context_manager")
    ctx.assemble_context_block = lambda *a, **k: ""  # type: ignore[attr-defined]
    for mod in (firefly, common, ctx):
        monkeypatch.setitem(sys.modules, mod.__name__, mod)
    spec = importlib.util.spec_from_file_location(
        "clawrouter_under_test", REPO / "mayor" / "clawrouter.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
