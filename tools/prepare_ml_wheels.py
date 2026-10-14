#!/usr/bin/env python3
"""Download target wheels, verifying every artifact against the project's uv.lock."""

import argparse
import hashlib
import json
import subprocess
import tempfile
import tomllib
from pathlib import Path
from urllib.parse import unquote, urlparse


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def prepare(project, output, resolve=()):
    project, output = project.resolve(), output.resolve()
    lock = tomllib.loads((project / "uv.lock").read_text())
    frozen = {(p["name"], p["version"]): p for p in lock["package"]}
    output.mkdir(parents=True, exist_ok=True)
    manifest = {}
    with tempfile.TemporaryDirectory(prefix="ml-wheels-") as directory:
        requirements = Path(directory) / "requirements.txt"
        subprocess.run(["uv", "export", "--project", str(project), "--locked",
                        "--no-dev", "--no-emit-project", "--no-hashes", "--emit-index-url",
                        "--output-file", str(requirements), "--quiet"], check=True)
        target = Path(directory) / "pylock.target.toml"
        subprocess.run(["uv", "pip", "compile", str(requirements), "--python-version", "3.11",
                        "--index-strategy", "unsafe-best-match",
                        "--python-platform", "x86_64-unknown-linux-gnu", "--format", "pylock.toml",
                        "--output-file", str(target), "--quiet"], check=True)
        for package in tomllib.loads(target.read_text())["packages"]:
            wheels = package.get("wheels", [])
            if len(wheels) != 1:
                raise ValueError(f"Expected one target wheel for {package['name']}")
            wheel = wheels[0]
            artifacts = frozen[(package["name"], package["version"])].get("wheels", [])
            filename = Path(unquote(urlparse(wheel["url"]).path)).name.lower()
            matches = [w for w in artifacts
                       if Path(unquote(urlparse(w["url"]).path)).name.lower() == filename]
            if len(matches) != 1:
                raise ValueError(f"Wheel differs from uv.lock: {package['name']}")
            artifact = matches[0]
            expected = artifact["hash"].removeprefix("sha256:")
            url = urlparse(artifact["url"])
            if url.scheme != "https":
                raise ValueError("Wheel downloads require HTTPS")
            path = output / Path(unquote(url.path)).name
            if not path.exists() or sha256(path) != expected:
                temporary = path.with_suffix(".partial")
                command = ["curl", "--http1.1", "--fail", "--location", "--retry", "3",
                                "--connect-timeout", "15", "--max-time", "300",
                                "--speed-limit", "1024", "--speed-time", "20",
                                "--silent", "--show-error", artifact["url"], "--output", str(temporary)]
                for address in resolve:
                    command += ["--resolve", address]
                subprocess.run(command, check=True)
                if sha256(temporary) != expected:
                    temporary.unlink()
                    raise ValueError(f"Wheel hash mismatch: {path.name}")
                temporary.replace(path)
            manifest[path.name] = expected
            print(f"Verified {path.name}", flush=True)
    (output / "SHA256SUMS.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Prepared {len(manifest)} Linux/amd64 Python 3.11 wheels in {output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--resolve", action="append", default=[],
                        help="Optional curl HOST:PORT:ADDRESS override; TLS and hashes remain verified")
    args = parser.parse_args()
    prepare(args.project, args.output, args.resolve)
