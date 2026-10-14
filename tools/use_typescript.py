"""Create a TypeScript starter in a NEW directory without deleting Python files."""
import argparse
from pathlib import Path
import shutil

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("destination", type=Path)
args = parser.parse_args()
shutil.copytree(root / "templates/typescript", args.destination)
shutil.copytree(root / "tools", args.destination / "tools", ignore=shutil.ignore_patterns("__pycache__"))
print("Created TypeScript starter:", args.destination.resolve())
