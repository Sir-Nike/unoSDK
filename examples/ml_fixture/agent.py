"""Packaged CPU/CUDA fixture. Scores only actions supplied by the UNO engine."""
import hashlib
import os
from pathlib import Path
import sys
import time
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from uno_agent.adapter import run_agent, log
from uno_agent.gpu import configure_torch
sys.path.insert(0, str(Path(__file__).resolve().parent))
from model import make_model

MODEL = None
DEVICE = None
READY = None


def initialize(game_id, seat, config):
    global MODEL, DEVICE, READY
    DEVICE = configure_torch(torch, os.environ.get("UNO_REQUIRE_CUDA") == "1")
    weights = Path(__file__).resolve().parents[2] / "models" / "policy.pt"
    MODEL = make_model()
    MODEL.load_state_dict(torch.load(weights, weights_only=True, map_location="cpu"))
    MODEL = MODEL.eval().to(DEVICE)
    with torch.inference_mode():
        test = MODEL(torch.zeros(1, 8, device=DEVICE))
        if not torch.isfinite(test).all():
            raise RuntimeError("model self-test failed")
        if DEVICE.startswith("cuda"):
            torch.cuda.synchronize()
    READY = {"device": DEVICE, "cuda_self_test": DEVICE.startswith("cuda"), "model_self_test": True,
             "gpu_name": torch.cuda.get_device_name(0) if DEVICE.startswith("cuda") else None,
             "weights_sha256": hashlib.sha256(weights.read_bytes()).hexdigest(), "inferences": 0}
    log(READY)
    return dict(READY)


def select_action(obs, checkpoint):
    state = dict(checkpoint or READY)
    legal = obs["legal_actions"]
    features = []
    for action in legal:
        card = action.get("card", {})
        features.append([len(obs["hand"]) / 108, obs["draw_pile_size"] / 108,
                         len(obs["hand_sizes"]) / 10, action["type"] == "play",
                         action["type"] == "draw", action["type"] == "keep",
                         card.get("color") == obs["active_color"], (card.get("value") or 0) / 9])
    start = time.time_ns()
    with torch.inference_mode():
        predictions = MODEL(torch.tensor(features, dtype=torch.float32, device=DEVICE)).flatten()
        if not torch.isfinite(predictions).all():
            raise RuntimeError("non-finite inference")
        choice = predictions.argmax().item()
        if DEVICE.startswith("cuda"):
            torch.cuda.synchronize()
    state["inferences"] = state.get("inferences", 0) + 1
    state["last_inference"] = {"start_ns": start, "end_ns": time.time_ns()}
    return legal[choice]["action_id"], state


if __name__ == "__main__":
    run_agent(select_action, on_initialize=initialize)
