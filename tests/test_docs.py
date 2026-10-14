"""Documentation structure: every required guide exists with its sections."""

import unittest
from pathlib import Path

DOCS = Path(__file__).resolve().parent.parent / "docs"

REQUIRED = {
    "quickstart.md": ["fork", "run the default agent", "edit", "test", "build",
                      "commit"],
    "typescript.md": ["replace", "root files", "selectAction", "check", "erasable"],
    "protocol.md": ["observation", "history", "legal action", "lifecycle",
                    "checkpoint", "resource"],
    "rules-evaluation.md": ["rules", "placement", "draw", "scoring",
                            "fallback"],
    "local-testing.md": ["checkout", "validate", "restart", "games"],
    "training.md": ["simulator", "seed", "baseline", "self-play"],
    "submission.md": ["commit sha", "required files", "dependenc", "weights",
                      "provenance", "packag"],
    "troubleshooting.md": ["malformed", "timeout", "restart", "container"],
}


class DocsTest(unittest.TestCase):
    """Required guides exist with their sections."""

    def test_all_guides_present_with_sections(self) -> None:
        """Every required guide contains its expected sections."""
        for doc, keywords in REQUIRED.items():
            text = (DOCS / doc).read_text().lower()
            for keyword in keywords:
                self.assertIn(keyword.lower(), text,
                              f"{doc} is missing '{keyword}'")


if __name__ == "__main__":
    unittest.main()
