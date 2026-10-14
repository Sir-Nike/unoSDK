#!/usr/bin/env python3
"""unoSDK Python starter entrypoint (standard library only).

Usage:
    python src/agent.py          # speaks newline-JSON on stdin/stdout

Edit ``strategy.py`` to change how moves are picked. Do not print to stdout;
the adapter owns it. Diagnostics go to stderr.
"""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType


def _load_module(module_name: str, file_path: Path) -> ModuleType:
    """Load a module from a file path without import-position side effects."""
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {module_name} from {file_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


_SRC_DIR = Path(__file__).resolve().parent
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

_strategy = _load_module("strategy", _SRC_DIR / "strategy.py")
_adapter = _load_module("uno_agent.adapter", _SRC_DIR / "uno_agent" / "adapter.py")

select_action = _strategy.select_action
run_agent = _adapter.run_agent


if __name__ == "__main__":
    run_agent(select_action)
