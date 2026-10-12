"""Importing authority_runtime must not load a frontier SDK; deployments gate finance code on sys.modules."""

import json
import subprocess
import sys

_PROBE = (
    "import sys, json, authority_runtime\n"
    "from authority_runtime.backends.slos import SlosBackend\n"
    "import authority_runtime.compiler\n"
    "print(json.dumps(sorted(m for m in ('openai', 'anthropic') if m in sys.modules)))\n"
)


def test_import_loads_no_frontier_sdk():
    out = subprocess.run([sys.executable, "-c", _PROBE], capture_output=True, text=True, check=True)
    assert json.loads(out.stdout.strip()) == []


def test_sdk_classes_still_resolve_lazily():
    from authority_runtime import compiler

    assert compiler.Anthropic.__name__ == "Anthropic"
    assert compiler.OpenAI.__name__ == "OpenAI"
