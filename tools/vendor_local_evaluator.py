#!/usr/bin/env python3
"""Refresh the public local evaluator from an authoritative game checkout."""
import argparse
import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GAME = json.loads((ROOT / "submission.json").read_text())["game"].split("-")[1]
PACKAGE = "triad_" + GAME
FILES = ["__init__.py", "cards.py", "engine.py", "protocol.py", "baselines.py",
         "simulator.py", "local_runner.py"]
FILES += ["agent_session.py", "scoring.py"] if GAME == "uno" else ["scoring.py"]


def adapted(name, data):
    # The event has a fixed UNO table size. Keep it as a source constant rather
    # than accepting a constructor override on the participant-side snapshot.
    if GAME == "uno" and name == "engine.py":
        field_line = b"    players: int = 4\n"
        config_hook = b"    position_points: tuple[float, ...] | None = None\n\n    def __post_init__(self) -> None:\n"
        validation = (
            b"        if type(self.players) is not int or self.players != SEATS:\n"
            b"            raise ValueError(\"UNO requires exactly four players\")\n"
        )
        if field_line not in data or config_hook not in data or validation not in data:
            raise SystemExit("UNO engine no longer matches the fixed-seat vendoring adaptation")
        data = data.replace(b"    players: int = 4\n", b"", 1)
        data = data.replace(
            b"    position_points: tuple[float, ...] | None = None\n\n    def __post_init__(self) -> None:\n",
            b"    position_points: tuple[float, ...] | None = None\n\n"
            b"    @property\n"
            b"    def players(self) -> int:\n"
            b'        """Fixed event table size; change ``SEATS`` to edit it in source."""\n'
            b"        return SEATS\n\n"
            b"    def __post_init__(self) -> None:\n",
            1,
        )
        data = data.replace(validation, b"", 1)
    # The SDK carries its event scorer because the organizer's legacy scorer
    # remains available only for historical evaluation artifacts.
    if GAME == "uno" and name == "scoring.py":
        data = (ROOT / "local_evaluator" / "src" / PACKAGE / "scoring.py").read_bytes()
    # Host admission is a production-only dependency; the local runner passes None.
    if GAME == "bluff" and name == "local_runner.py":
        data = data.replace(b"from typing import Any\n", b"from typing import Any, TYPE_CHECKING\n")
        data = data.replace(b"from .resources import HostAdmission\n",
                            b"if TYPE_CHECKING:\n    from .resources import HostAdmission\n")
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("checkout", type=Path)
    parser.add_argument("--check", action="store_true", help="verify without writing")
    parser.add_argument("--working-tree", action="store_true",
                        help="explicitly snapshot uncommitted source bytes and record their hashes")
    args = parser.parse_args()
    source = args.checkout.resolve()
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source, text=True).strip()
    dirty = subprocess.check_output(["git", "status", "--porcelain"], cwd=source, text=True).strip()
    if dirty and not args.working_tree:
        raise SystemExit("source checkout must be clean before publishing a snapshot")
    paths = [Path(name) for name in FILES]
    paths += sorted((source / "src" / PACKAGE / "schemas").glob("*.json"))
    records = {}
    target = ROOT / "local_evaluator" / "src" / PACKAGE
    for path in paths:
        relative = path.relative_to(source / "src" / PACKAGE) if path.is_absolute() else path
        # Canonical text hashes and paths work across Windows/Linux checkouts.
        data = (source / "src" / PACKAGE / relative).read_bytes().replace(b"\r\n", b"\n")
        bundled = adapted(relative.as_posix(), data)
        destination = target / relative
        records[relative.as_posix()] = {"source_sha256": hashlib.sha256(data).hexdigest(),
                                  "bundled_sha256": hashlib.sha256(bundled).hexdigest()}
        if args.check:
            if not destination.is_file() or destination.read_bytes() != bundled:
                raise SystemExit(f"snapshot differs: {relative}")
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(bundled)
    adaptations = []
    if GAME == "bluff":
        adaptations.append("local_runner.py: HostAdmission import is type-checking only")
    if GAME == "uno":
        adaptations.append("engine.py: fixed player count is a read-only property backed by SEATS")
        adaptations.append("scoring.py: SDK event scorer replaces legacy historical scorer")
    metadata = {"game": GAME, "source_revision": revision, "files": records,
                "adaptations": adaptations}
    if args.working_tree:
        metadata["source_kind"] = "working-tree"
    manifest = ROOT / "local_evaluator" / "VENDOR.json"
    if args.check:
        if json.loads(manifest.read_text()) != metadata:
            raise SystemExit("snapshot provenance differs")
    else:
        manifest.write_text(json.dumps(metadata, indent=2) + "\n")
    print(f"{GAME}: {'verified' if args.check else 'vendored'} {len(records)} files at {revision}")


if __name__ == "__main__":
    main()
