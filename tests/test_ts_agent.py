"""Protocol lifecycle tests for the TypeScript starter.

Runs the same scripted lifecycle as the Python starter against
``templates/typescript/src/agent.ts``. Skips cleanly when Node is absent or
too old to strip types (the TS runtime itself is required to execute the
agent, not a dev tool): the starter needs Node >= 22.18 and erasable-syntax
sources, which the probe below verifies directly.
"""

import contextlib
import json
import shutil
import subprocess
import tempfile
import unittest
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from tools import check

ROOT = Path(__file__).resolve().parent.parent

NODE = shutil.which("node")
# After the documented switch the submission lives at src/agent.ts;
# otherwise use the template copy. Either way the same file is tested.
_TS_ROOT = ROOT / "src" / "agent.ts"
_TS_TEMPLATE = ROOT / "templates" / "typescript" / "src" / "agent.ts"
TS_AGENT = _TS_ROOT if _TS_ROOT.exists() else _TS_TEMPLATE

STRIP_PROBE = "const x: number = 40 + 2;\nconsole.log(x);\n"


def node_strips_types() -> bool:
    """Probe whether node can run type-stripped TypeScript."""
    if NODE is None:
        return False
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".ts", delete=False) as tmp:
            tmp.write(STRIP_PROBE)
            probe = tmp.name
        proc = subprocess.run([NODE, probe], capture_output=True, text=True,
                              timeout=30, check=False)
        return proc.returncode == 0 and proc.stdout.strip() == "42"
    except (OSError, subprocess.SubprocessError):
        return False


@contextlib.contextmanager
def agent_process(cmd: list[str]) -> Iterator[subprocess.Popen[str]]:
    """Start an agent process; terminate it when the block exits."""
    proc = subprocess.Popen(
        cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True)
    try:
        yield proc
    finally:
        if proc.poll() is None:
            proc.kill()


@unittest.skipIf(not node_strips_types(),
                 "node missing or too old for type stripping (>= 22.18 required)")
class TsAgentTest(unittest.TestCase):
    """Protocol lifecycle tests for the TypeScript starter."""

    def test_full_protocol_lifecycle(self) -> None:
        """Exercise the scripted lifecycle against the TS agent."""
        assert NODE is not None
        results = check.check_agent([NODE, str(TS_AGENT)], str(ROOT), "ts")
        failures = [f"{name}: {detail}" for name, ok, detail in results if not ok]
        self.assertEqual(failures, [], f"smoke failures: {failures}")
        self.assertGreaterEqual(len(results), 8)

    def test_initialization_requires_four_players(self) -> None:
        """All four seats work; other table sizes and invalid configs fail."""
        assert NODE is not None
        base = {"type": "initialize", "protocol_version": "1.0", "request_id": "r",
                "game_id": "g", "seat": 0, "config": {"players": 4}}
        cases = [(dict(base, seat=seat), None) for seat in range(4)]
        cases += [(dict(base, config={"players": players}), "invalid players")
                  for players in (2, 3, 5, 8, 10, True, "4", None)]
        cases += [(dict(base, seat=seat), "invalid seat") for seat in (-1, 4, True, 1.5)]
        cases += [(dict(base, config=config), "invalid config") for config in (None, [], 4)]
        cases.append((dict(base, config={}), None))
        script = (
            "const {validateOrganizerMessage} = await import(process.argv[1]);\n"
            "const {readFileSync} = await import('node:fs');\n"
            "const cases = JSON.parse(readFileSync(0, 'utf8'));\n"
            "console.log(JSON.stringify(cases.map(validateOrganizerMessage)));\n"
        )
        proc = subprocess.run(
            [NODE, "--input-type=module", "--eval", script,
             str(TS_AGENT.with_name("protocol.ts"))],
            input=json.dumps([msg for msg, _ in cases]), capture_output=True,
            text=True, timeout=30, check=False)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout), [error for _, error in cases])

    def test_starts_through_symlinked_path(self) -> None:
        """Agent must start when argv[1] contains symlinks."""
        assert NODE is not None
        # argv[1] may contain symlinks (macOS /tmp -> /private/tmp) while
        # import.meta.url is resolved; startup must not silently skip.
        with tempfile.TemporaryDirectory() as tmp:
            link = Path(tmp) / "agent-link.ts"
            try:
                link.symlink_to(TS_AGENT)
            except OSError as exc:
                self.skipTest(f"symlinks unavailable: {exc}")
            (Path(tmp) / "package.json").write_text('{"type": "module"}')
            with agent_process([NODE, str(link)]) as proc:
                out, _ = proc.communicate(
                    input='{"type":"initialize","protocol_version":"1.0",'
                          '"request_id":"r0","game_id":"g","seat":0,'
                          '"ruleset":"triad-uno-v1","config":{"max_turns":1000}}\n',
                    timeout=30)
            reply = json.loads(out.strip().splitlines()[0])
            self.assertEqual(reply["type"], "ready")
            self.assertEqual(reply["request_id"], "r0")

    def test_default_select_action_prefers_play(self) -> None:
        """Default selectAction prefers first play, else first legal action."""
        assert NODE is not None
        # Call the default selectAction directly: first play wins, else the
        # first legal action, checkpoint passed through untouched.
        script = (
            "const m = await import(process.argv[1]);\n"
            "const out = [];\n"
            "out.push(m.selectAction({legal_actions: ["
            "{action_id: 'a0', type: 'draw'},"
            "{action_id: 'a1', type: 'play'},"
            "{action_id: 'a2', type: 'play'}]}, {n: 1}));\n"
            "out.push(m.selectAction({legal_actions: "
            "[{action_id: 'a0', type: 'draw'}]}, null));\n"
            "console.log(JSON.stringify(out));\n"
        )
        proc = subprocess.run(
            [NODE, "--input-type=module", "--eval", script, TS_AGENT.as_uri()],
            capture_output=True, text=True, timeout=60, check=False)
        self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
        parsed: list[dict[str, Any]] = json.loads(proc.stdout.strip())
        first, second = parsed
        self.assertEqual(first["action_id"], "a1")
        self.assertEqual(first["agentState"], {"n": 1})
        self.assertEqual(second["action_id"], "a0")
        self.assertIsNone(second["agentState"])


if __name__ == "__main__":
    unittest.main()
