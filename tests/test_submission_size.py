"""Submission-size checks use Git's file selection and real asset sizes."""
import importlib.util
import gzip
import io
import json
import tarfile
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "tools/check_submission_size.py"
spec = importlib.util.spec_from_file_location("size_check", SCRIPT)
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


class SubmissionSizeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        self.git("init", "-q")

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.repo), *args], stderr=subprocess.PIPE)

    def commit(self):
        self.git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "snapshot")

    def test_ignored_caches_excluded_but_tracked_ignored_files_count(self):
        (self.repo / ".gitignore").write_text("cache/\n*.bin\n")
        (self.repo / "cache").mkdir()
        (self.repo / "cache/large.bin").write_bytes(b"x" * 1000)
        (self.repo / "tracked.bin").write_bytes(b"x" * 30)
        self.git("add", "-f", "tracked.bin")
        (self.repo / "new source.py").write_bytes(b"x" * 20)
        report = checker.inventory(self.repo)
        expected = (self.repo / ".gitignore").stat().st_size + 30 + 20
        self.assertEqual(report["total_bytes"], expected)
        self.assertEqual(report["file_count"], 3)

    def test_frozen_revision_excludes_uncommitted_and_ignored_files(self):
        (self.repo / "agent.py").write_bytes(b"123")
        self.git("add", "agent.py")
        self.commit()
        (self.repo / "agent.py").write_bytes(b"12345678")
        (self.repo / "new.py").write_bytes(b"123456")
        self.assertEqual(checker.inventory(self.repo, "HEAD")["total_bytes"], 3)
        self.assertEqual(checker.inventory(self.repo)["total_bytes"], 14)

    def test_exact_limit_passes_and_one_byte_over_fails_without_allocating_ram(self):
        with (self.repo / "weights.bin").open("wb") as stream:
            stream.truncate(checker.MAX_BYTES)
        self.assertTrue(checker.inventory(self.repo)["ok"])
        with (self.repo / "weights.bin").open("r+b") as stream:
            stream.truncate(checker.MAX_BYTES + 1)
        self.assertFalse(checker.inventory(self.repo)["ok"])
        result = subprocess.run([sys.executable, str(SCRIPT), "--repo", str(self.repo), "--json"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout)["total_bytes"], checker.MAX_BYTES + 1)

    def test_lfs_counts_asset_size_in_worktree_and_frozen_commit(self):
        declared = checker.MAX_BYTES + 1
        pointer = f"version https://git-lfs.github.com/spec/v1\noid sha256:{'a' * 64}\nsize {declared}\n"
        (self.repo / "model.bin").write_text(pointer)
        self.git("add", "model.bin")
        self.commit()
        for revision in (None, "HEAD"):
            report = checker.inventory(self.repo, revision)
            self.assertEqual(report["total_bytes"], declared)
            self.assertFalse(report["ok"])
        self.assertEqual(checker.lfs_size(pointer.replace("\n", "\r\n").encode()), declared)

    def test_symlinks_cannot_hide_external_files(self):
        (self.repo / "outside-link").symlink_to(self.repo.parent)
        with self.assertRaisesRegex(ValueError, "symlink"):
            checker.inventory(self.repo)

    def test_submodule_contents_cannot_be_silently_omitted(self):
        (self.repo / "agent.py").write_text("pass")
        self.git("add", "agent.py")
        self.commit()
        sha = self.git("rev-parse", "HEAD").decode().strip()
        self.git("update-index", "--add", "--cacheinfo", f"160000,{sha},submodule")
        with self.assertRaisesRegex(ValueError, "submodule"):
            checker.inventory(self.repo)
        self.commit()
        with self.assertRaisesRegex(ValueError, "submodule"):
            checker.inventory(self.repo, "HEAD")

    def test_image_size_includes_installed_dependencies(self):
        with patch.object(checker.subprocess, "check_output", return_value=json.dumps([{"Size": checker.MAX_BYTES + 1}]).encode()):
            self.assertEqual(checker.image_size("agent:local"), checker.MAX_BYTES + 1)
        with patch.object(checker.subprocess, "check_output", return_value=b'[{}]'):
            with self.assertRaises(ValueError):
                checker.image_size("agent:local")

    def test_compressed_image_layers_cannot_bypass_size_limit(self):
        def exported_image(payload):
            blob = io.BytesIO()
            with tarfile.open(fileobj=blob, mode="w") as archive:
                for name, data in [("layer.bin", gzip.compress(payload)),
                                   ("manifest.json", b'[{"Layers":["layer.bin"]}]')]:
                    member = tarfile.TarInfo(name)
                    member.size = len(data)
                    archive.addfile(member, io.BytesIO(data))
            return blob.getvalue()

        class Export:
            def __init__(self, data):
                self.stdout = io.BytesIO(data)
            def poll(self):
                return 0
            def wait(self, timeout=None):
                return 0

        with patch.object(checker, "MAX_BYTES", 1024):
            for payload, expected in [(b"x" * 1024, 1024), (b"x" * 1025, 1025)]:
                with patch.object(checker.subprocess, "Popen", return_value=Export(exported_image(payload))):
                    self.assertEqual(checker.uncompressed_image_size("agent:local"), expected)


if __name__ == "__main__":
    unittest.main()
