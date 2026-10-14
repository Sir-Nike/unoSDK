"""Compatibility checks for vendored protocol components.

Compares the SDK's vendored protocol helpers and JSON Schemas against a
separate UNO engine checkout (the authoritative wire contract). Skips when no
checkout is available -- agent execution never needs the sibling repository.

Checkout discovery: ``UNO_CHECKOUT`` env var, else a sibling ``../UNO``
directory next to this repository.
"""

import hashlib
import importlib.util
import hashlib
import json
import os
import re
import subprocess
import unittest
from pathlib import Path
from types import ModuleType
from typing import Any

ROOT = Path(__file__).resolve().parent.parent

# Layout detection: the documented TypeScript switch replaces src/agent.py
# and src/uno_agent/ with src/agent.ts + src/protocol.ts. Python-vendor
# checks run only when the Python starter is present; TypeScript checks run
# against the swapped root when it exists, else the template.
PY_DIR = ROOT / "src" / "uno_agent"
HAS_PY = (PY_DIR / "protocol.py").exists()
VENDOR_PROTOCOL = PY_DIR / "protocol.py"
VENDOR_SCHEMAS = PY_DIR / "schemas"
RECORDED: dict[str, Any] | None = None
if (PY_DIR / "VENDOR.json").exists():
    _recorded = json.loads((PY_DIR / "VENDOR.json").read_text())
    if isinstance(_recorded, dict):
        RECORDED = _recorded

TS_ROOT = ROOT / "src"
TS_TEMPLATE = ROOT / "templates" / "typescript" / "src"
if (TS_ROOT / "protocol.ts").exists():
    TS_PROTOCOL = TS_ROOT / "protocol.ts"
    TS_SCHEMAS = TS_ROOT / "schemas"
else:
    TS_PROTOCOL = TS_TEMPLATE / "protocol.ts"
    TS_SCHEMAS = TS_TEMPLATE / "schemas"
HAS_TS = TS_PROTOCOL.exists()


def find_uno_checkout() -> Path | None:
    """Locate the sibling UNO engine checkout, if available."""
    env = os.environ.get("UNO_CHECKOUT")
    if env:
        cand = Path(env)
        if (cand / "src" / "triad_uno" / "protocol.py").exists():
            return cand
    sibling = ROOT.parent / "UNO"
    if (sibling / "src" / "triad_uno" / "protocol.py").exists():
        return sibling
    return None


def load_module(path: Path, name: str) -> ModuleType:
    """Load a Python module from a file path under the given name."""
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {name} from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


UNO = find_uno_checkout()


@unittest.skipIf(UNO is None, "no UNO checkout found (set UNO_CHECKOUT)")
class CompatTest(unittest.TestCase):
    """Cross-check vendored protocol against the UNO engine checkout."""

    uno: Any
    vendor: Any
    uno_schemas: Path

    @classmethod
    def setUpClass(cls) -> None:
        """Load reference and vendored protocol modules."""
        assert UNO is not None
        uno_proto = UNO / "src" / "triad_uno" / "protocol.py"
        cls.uno = load_module(uno_proto, "uno_protocol_reference")
        cls.vendor = (load_module(VENDOR_PROTOCOL, "uno_protocol_vendored")
                      if HAS_PY else None)
        cls.uno_schemas = UNO / "src" / "triad_uno" / "schemas"

    @unittest.skipUnless(HAS_PY, "Python starter not present (swapped layout?)")
    def test_vendored_files_match_recorded_revision(self) -> None:
        """Vendored bytes must equal the recorded UNO revision's bytes."""
        # Require the recorded commit's bytes, or an explicitly recorded and
        # hashed working-tree override. Unrecorded local edits cannot count
        # as compatible.
        assert UNO is not None
        assert RECORDED is not None
        if "sha256" in RECORDED:
            for relative, expected in RECORDED["sha256"].items():
                content = (PY_DIR / relative).read_text(encoding="utf-8")
                self.assertEqual(hashlib.sha256(content.encode()).hexdigest(), expected)
            return
        sha = RECORDED["source_revision"]
        pairs = [("src/triad_uno/protocol.py", VENDOR_PROTOCOL)]
        pairs += [(f"src/triad_uno/schemas/{p.name}", p)
                  for p in sorted(VENDOR_SCHEMAS.glob("*.json"))]
        for repo_rel, local_path in pairs:
            proc = subprocess.run(["git", "show", f"{sha}:{repo_rel}"], cwd=UNO,
                                  capture_output=True, check=False)
            self.assertEqual(proc.returncode, 0,
                             f"recorded revision {sha} is missing {repo_rel}")
            expected = proc.stdout
            override = RECORDED.get("source_overrides", {}).get(repo_rel)
            if override is not None:
                self.assertEqual(RECORDED.get("source_kind"), "working-tree")
                expected = (UNO / repo_rel).read_bytes()
                self.assertEqual(hashlib.sha256(expected).hexdigest(), override,
                                 f"upstream {repo_rel} changed after the recorded override")
            self.assertEqual(expected, local_path.read_bytes(),
                             f"vendored {local_path.name} differs from {sha}")

    @unittest.skipUnless(HAS_PY, "Python starter not present (swapped layout?)")
    def test_protocol_constants_match(self) -> None:
        """Vendored protocol constants must match the reference."""
        for attr in ("PROTOCOL_VERSION", "MAX_PROTOCOL_LINE", "MAX_CHECKPOINT",
                     "ORGANIZER_MSGS", "AGENT_MSGS"):
            self.assertEqual(getattr(self.vendor, attr), getattr(self.uno, attr),
                             f"vendored {attr} diverged from UNO")

    @unittest.skipUnless(HAS_PY, "Python starter not present (swapped layout?)")
    def test_builders_match(self) -> None:
        """Vendored builders must match the reference builders."""
        u, v = self.uno, self.vendor
        self.assertEqual(
            v.build_initialize("g", 2, "g-d0", 1000),
            u.build_initialize("g", 2, "g-d0", 1000))
        obs = {"seat": 0}
        self.assertEqual(v.build_act("g", "g-d1", obs, {"n": 1}),
                         u.build_act("g", "g-d1", obs, {"n": 1}))
        outcome = {"placements": [0, 1, 2, 3]}
        self.assertEqual(v.build_game_over("g", "g-d9", outcome, obs),
                         u.build_game_over("g", "g-d9", outcome, obs))
        self.assertEqual(v.build_ready("g-d0", None), u.build_ready("g-d0", None))
        self.assertEqual(v.build_action("g-d1", "a0", None),
                         u.build_action("g-d1", "a0", None))
        self.assertEqual(v.build_done("g-d9"), u.build_done("g-d9"))

    @unittest.skipUnless(HAS_PY, "Python starter not present (swapped layout?)")
    def test_validators_match(self) -> None:
        """Vendored validators must agree with the reference on samples."""
        u, v = self.uno, self.vendor
        samples = [
            {"type": "initialize", "protocol_version": "1.0", "request_id": "r",
             "game_id": "g", "seat": 0, "ruleset": "x", "config": {}},
            {"type": "act", "protocol_version": "1.0", "request_id": "r",
             "game_id": "g", "observation": {}, "agent_state": None},
            {"type": "act", "protocol_version": "9.9", "request_id": "r",
             "game_id": "g"},
            {"type": "bogus"},
            "not-a-dict",
        ]
        samples += [
            {"type": "initialize", "protocol_version": "1.0", "request_id": "r",
             "game_id": "g", "seat": 0, "config": {"players": players}}
            for players in (2, 3, 4, 5, 8, 10, True, "4")
        ]
        for sample in samples:
            self.assertEqual(v.validate_organizer_message(sample),
                             u.validate_organizer_message(sample),
                             f"organizer validation diverged on {sample!r}")
        responses = [
            ({"type": "action", "request_id": "r", "action_id": "a0"}, "action",
             "r", {"a0"}),
            ({"type": "action", "request_id": "stale", "action_id": "a0"}, "action",
             "r", {"a0"}),
            ({"type": "action", "request_id": "r", "action_id": "a9"}, "action",
             "r", {"a0"}),
            ({"type": "ready", "request_id": "r"}, "ready", "r", None),
            ({"type": "done", "request_id": "r"}, "done", "r", None),
        ]
        for msg, mtype, rid, legal in responses:
            self.assertEqual(v.validate_agent_response(msg, mtype, rid, legal),
                             u.validate_agent_response(msg, mtype, rid, legal),
                             f"agent validation diverged on {msg!r}")

    def test_schemas_byte_identical(self) -> None:
        """Vendored JSON schemas must be byte-identical to the reference."""
        for schema in sorted(p.name for p in self.uno_schemas.glob("*.json")):
            reference = (self.uno_schemas / schema).read_text()
            if HAS_PY:
                vendored = (VENDOR_SCHEMAS / schema).read_text()
                self.assertEqual(vendored, reference, f"schema {schema} diverged")
            if HAS_TS:
                ts = (TS_SCHEMAS / schema).read_text()
                self.assertEqual(ts, reference, f"TS schema {schema} diverged")

    @unittest.skipUnless(HAS_TS, "TypeScript starter not present")
    def test_ts_protocol_constants_and_errors_match(self) -> None:
        """TypeScript protocol constants and errors must match the reference."""
        src = TS_PROTOCOL.read_text()
        self.assertIn(f'"{self.uno.PROTOCOL_VERSION}"', src.replace("'", '"'))
        for const, value in (("MAX_PROTOCOL_LINE", self.uno.MAX_PROTOCOL_LINE),
                             ("MAX_CHECKPOINT", self.uno.MAX_CHECKPOINT)):
            match = re.search(rf"{const}\s*=\s*([\d\s*]+);", src)
            self.assertIsNotNone(match, f"{const} missing from protocol.ts")
            assert match is not None
            computed = 1
            for factor in match.group(1).split("*"):
                computed *= int(factor.strip())
            self.assertEqual(computed, value, const)
        # The fixed organizer-validation error strings must stay identical so
        # agents and runners agree on faults.
        bad_messages = [
            "message must be an object",
            "unknown organizer message type",
            "unsupported protocol version",
            "invalid seat",
            "act requires observation",
            "game_over requires outcome",
        ]
        for text in bad_messages:
            self.assertIn(text, src, f"protocol.ts lost error string: {text}")


if __name__ == "__main__":
    unittest.main()
