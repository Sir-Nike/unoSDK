"""Protocol lifecycle tests for the Python starter (standard library only).

Runs the complete scripted lifecycle against ``src/agent.py`` with plain
``python3 -m unittest``: handshake, play preference, legal-action discipline,
checkpoint round-trip, malformed-input stdout discipline, restart from a
checkpoint, and terminal acknowledgement.
"""

import importlib.util
import sys
import unittest
from pathlib import Path
from types import ModuleType
from typing import Any, cast

from tools import check

ROOT = Path(__file__).resolve().parent.parent

PY_AGENT = ROOT / "src" / "agent.py"
HAS_PY_AGENT = PY_AGENT.exists()


def _load_module(module_name: str, file_path: Path) -> ModuleType:
    """Load a module from a file path for isolated testing."""
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {module_name} from {file_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def select_action(observation: dict[str, Any],
                  agent_state: Any) -> tuple[str, Any]:
    """Exercise the editable default strategy in ``src/strategy.py``."""
    strategy = _load_module("strategy", ROOT / "src" / "strategy.py")
    return cast(tuple[str, Any], strategy.select_action(observation, agent_state))


@unittest.skipUnless(HAS_PY_AGENT, "Python starter not present (swapped layout?)")
class PythonAgentTest(unittest.TestCase):
    """Full protocol lifecycle for the Python starter."""

    def test_full_protocol_lifecycle(self) -> None:
        """Exercise handshake through terminal acknowledgement."""
        cmd = [sys.executable, str(PY_AGENT)]
        results = check.check_agent(cmd, str(ROOT), "python")
        failures = [f"{name}: {detail}" for name, ok, detail in results if not ok]
        self.assertEqual(failures, [], f"smoke failures: {failures}")
        self.assertGreaterEqual(len(results), 8)


def _obs(legal: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a minimal observation with the given legal actions."""
    return {"legal_actions": legal}


@unittest.skipUnless(HAS_PY_AGENT, "Python starter not present (swapped layout?)")
class DefaultStrategyTest(unittest.TestCase):
    """The default select_action prefers a play, else the first legal action."""

    def test_prefers_first_play(self) -> None:
        """First play action wins over earlier draws."""
        legal = [{"action_id": "a0", "type": "draw"},
                 {"action_id": "a1", "type": "play"},
                 {"action_id": "a2", "type": "play"}]
        aid, state = select_action(_obs(legal), {"n": 1})
        self.assertEqual(aid, "a1")
        self.assertEqual(state, {"n": 1})

    def test_falls_back_to_first_legal_action(self) -> None:
        """Single legal action is returned unchanged."""
        legal = [{"action_id": "a0", "type": "draw"}]
        aid, state = select_action(_obs(legal), None)
        self.assertEqual(aid, "a0")
        self.assertIsNone(state)


if __name__ == "__main__":
    unittest.main()
