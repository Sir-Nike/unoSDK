"""Small untrained policy for packaging and inference verification only."""
import torch


def make_model():
    return torch.nn.Sequential(torch.nn.Linear(8, 32), torch.nn.ReLU(), torch.nn.Linear(32, 1))


if __name__ == "__main__":
    from pathlib import Path
    torch.manual_seed(173)
    path = Path("models/policy.pt")
    path.parent.mkdir(exist_ok=True)
    torch.save(make_model().state_dict(), path)
