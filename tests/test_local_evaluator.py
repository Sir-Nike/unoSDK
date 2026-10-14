"""Snapshot integrity and real subprocess games without organizer imports."""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GAME = json.loads((ROOT / "submission.json").read_text())["game"].split("-")[1]


class LocalEvaluatorTests(unittest.TestCase):
    def run_match(self, *args, expected=0):
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        env.pop("PYTHONPATH", None)
        with tempfile.TemporaryDirectory() as cwd:
            result = subprocess.run([sys.executable, str(ROOT / "tools/local_match.py"),
                                     "--games", "1", "--max-turns", "60", *args],
                                    cwd=cwd, env=env, text=True, capture_output=True, timeout=60)
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return json.loads(result.stdout.strip().splitlines()[-1])

    def test_snapshot_hashes_and_no_service_modules(self):
        manifest = json.loads((ROOT / "local_evaluator/VENDOR.json").read_text())
        source = ROOT / "local_evaluator/src" / ("triad_" + GAME)
        for name, record in manifest["files"].items():
            self.assertEqual(hashlib.sha256((source / name).read_bytes()).hexdigest(), record["bundled_sha256"])
        self.assertEqual({p.relative_to(source).as_posix() for p in source.rglob("*")
                          if p.is_file() and "__pycache__" not in p.parts}, set(manifest["files"]))
        self.assertFalse((source / "server.py").exists())
        self.assertFalse((source / "submissions.py").exists())

    def test_full_protocol_game_from_unrelated_cwd(self):
        row = self.run_match()
        self.assertFalse(row["faults"])
        self.assertEqual(sum(row["fallbacks"].values()), 0)
        self.assertIsNotNone(row["outcome"])

    def test_baseline_simulation(self):
        self.assertIsNotNone(self.run_match("--baseline-only")["outcome"])

    def test_uno_game_config_has_fixed_player_count(self):
        code = """
import sys
sys.path.insert(0, sys.argv[1])
from triad_uno.engine import GameConfig, SEATS
assert SEATS == 4
assert GameConfig().players == SEATS
try:
    GameConfig(players=5)
except TypeError:
    pass
else:
    raise AssertionError('GameConfig must not accept a players argument')
"""
        result = subprocess.run(
            [sys.executable, "-c", code, str(ROOT / "local_evaluator/src")],
            capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_uno_cli_does_not_expose_player_count_option(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "tools/local_match.py"), "--players", "5"],
            capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 2)
        self.assertIn("unrecognized arguments: --players", result.stderr)

    def test_faulting_agent_fails_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            agent = Path(directory) / "exit.py"
            agent.write_text("raise SystemExit(1)\n")
            row = self.run_match("--cmd", f"{sys.executable} {agent}", expected=2)
        self.assertTrue(row["faults"])
        self.assertGreater(sum(row["fallbacks"].values()), 0)

    def test_restart_or_multideck(self):
        if GAME == "uno":
            row = self.run_match("--restart-test")
            self.assertGreaterEqual(sum(row["restarts"].values()), 4)
        else:
            row = self.run_match("--players", "8", "--decks", "8")
        self.assertFalse(row["faults"])
        self.assertEqual(sum(row["fallbacks"].values()), 0)


if __name__ == "__main__":
    unittest.main()
