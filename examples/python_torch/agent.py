#!/usr/bin/env python3
"""GPU-optional scoring example.

Uses CUDA when torch and a GPU are present, otherwise a pure-Python path that
computes the same fixed random projection over a tiny action encoding. The
torch import is lazy, so the example runs on a plain CPU image with no extra
dependencies.
"""

from __future__ import annotations

import importlib
import importlib.util
import random
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

_SRC = Path(__file__).resolve().parents[2] / "src"
sys.path.insert(0, str(_SRC))
_spec = importlib.util.spec_from_file_location(
    "uno_agent.adapter", _SRC / "uno_agent" / "adapter.py"
)
if _spec is None or _spec.loader is None:
    raise ImportError("cannot load uno_agent.adapter")
_adapter: Any = importlib.util.module_from_spec(_spec)
sys.modules["uno_agent.adapter"] = _adapter
_spec.loader.exec_module(_adapter)
run_agent = _adapter.run_agent
log = _adapter.log

KIND_RANK = {
    "number": 0,
    "skip": 1,
    "reverse": 2,
    "draw_two": 3,
    "wild": 4,
    "wild_draw_four": 5,
    "draw": 6,
    "keep": 7,
    "choose_color": 8,
}
COLOR_RANK = {"red": 0, "yellow": 1, "green": 2, "blue": 3}
QUERY_LENGTH = 3
NUM_PROJECTIONS = 4


def encode(action: dict[str, Any]) -> list[float]:
    """Encode an action as ``[kind_rank, colour_rank, value]`` floats."""
    card = action.get("card") or {}
    kind = card.get("kind", action["type"])
    color = action.get("color") or card.get("color")
    value = card.get("value")
    return [
        float(KIND_RANK.get(kind, 0)),
        float(COLOR_RANK.get(str(color), 0)),
        float(value) if value is not None else 0.0,
    ]


def projection() -> list[list[float]]:
    """Return the fixed random projection matrix."""
    rng = random.Random(1234)
    return [[rng.uniform(-1, 1) for _ in range(QUERY_LENGTH)] for _ in range(NUM_PROJECTIONS)]


def scores_python(legal: list[dict[str, Any]], proj: list[list[float]]) -> list[float]:
    """Score actions with the pure-Python projection."""
    scores: list[float] = []
    for action in legal:
        x = encode(action)
        scores.append(
            sum(
                max(0.0, sum(x[j] * proj[k][j] for j in range(QUERY_LENGTH)))
                for k in range(NUM_PROJECTIONS)
            )
        )
    return scores


class TorchScoringStrategy:
    """Score legal actions with a fixed projection, GPU when available."""

    def __init__(self) -> None:
        """Create the strategy with CPU defaults and a fixed projection."""
        self.torch: ModuleType | None = None
        self.device = "cpu"
        self.proj = projection()

    def on_initialize(
        self, _game_id: str, _seat: int, _config: dict[str, Any]
    ) -> dict[str, str]:
        """Load torch when available and report the active device."""
        try:
            torch = importlib.import_module("torch")
        except ImportError:
            log("torch-agent: torch not installed, CPU fallback")
        else:
            self.torch = torch
            self.device = "cuda" if torch.cuda.is_available() else "cpu"
            log(f"torch-agent: device={self.device}")
        return {"device": self.device}

    def _scores_torch(self, torch: ModuleType, legal: list[dict[str, Any]]) -> list[float]:
        """Score actions with torch on the active device."""
        functional = importlib.import_module("torch.nn.functional")
        x = torch.tensor(
            [encode(action) for action in legal], dtype=torch.float32, device=self.device
        )
        w = torch.tensor(self.proj, dtype=torch.float32, device=self.device)
        return list(functional.relu(x @ w.T).sum(dim=1).tolist())

    def select_action(
        self, observation: dict[str, Any], agent_state: Any
    ) -> tuple[str, dict[str, Any]]:
        """Return the highest-scoring legal action and updated state."""
        legal: list[dict[str, Any]] = observation["legal_actions"]
        try:
            if self.torch is None:
                scores = scores_python(legal, self.proj)
            else:
                scores = self._scores_torch(self.torch, legal)
        except (RuntimeError, ValueError, TypeError, AttributeError, OSError) as exc:
            log(f"torch-agent: scoring failed ({exc}); argmax fallback to a0 path")
            scores = scores_python(legal, self.proj)
        best = max(range(len(legal)), key=lambda i: scores[i])
        state = dict(agent_state) if isinstance(agent_state, dict) else {}
        state["device"] = self.device
        state["n"] = int(state.get("n", 0)) + 1
        return str(legal[best]["action_id"]), state


if __name__ == "__main__":
    strategy = TorchScoringStrategy()
    run_agent(strategy.select_action, on_initialize=strategy.on_initialize)
