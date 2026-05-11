"""Smoke test: `examples/quickstart_memory.py` must keep running end-to-end."""

import asyncio
import importlib.util
from pathlib import Path


def _load_example_module():
    path = Path(__file__).resolve().parent.parent / "examples" / "quickstart_memory.py"
    spec = importlib.util.spec_from_file_location("quickstart_memory_example", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_quickstart_runs_without_errors():
    module = _load_example_module()
    asyncio.run(module.run())
