#!/usr/bin/env python3
"""Check Git submission contents and optionally the built image against 1.5 GiB."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

MAX_BYTES = 3 * 1024**3 // 2
LFS_HEADER = b"version https://git-lfs.github.com/spec/v1\n"


def git(repo: Path, *args: str) -> bytes:
    return subprocess.check_output(["git", "-C", str(repo), *args], stderr=subprocess.PIPE)


def lfs_size(data: bytes) -> int | None:
    data = data.replace(b"\r\n", b"\n")
    if not data.startswith(LFS_HEADER):
        return None
    match = re.search(rb"^size ([0-9]+)$", data, re.MULTILINE)
    oid = re.search(rb"^oid sha256:[0-9a-f]{64}$", data, re.MULTILINE)
    if not match or not oid:
        raise ValueError("invalid Git LFS pointer; fetch the actual asset")
    return int(match.group(1))


def inventory(repo: Path, revision: str | None = None) -> dict:
    repo = Path(git(repo, "rev-parse", "--show-toplevel").decode().strip())
    files = []
    lfs = []
    if revision:
        revision = git(repo, "rev-parse", "--verify", revision + "^{commit}").decode().strip()
        for record in git(repo, "ls-tree", "-rlz", "--full-tree", revision).split(b"\0"):
            if not record:
                continue
            header, name = record.split(b"\t", 1)
            mode, kind, oid, size = header.split()
            path = name.decode("utf-8", "surrogateescape")
            if kind != b"blob" or mode == b"120000":
                raise ValueError(f"vendor submodules and replace symlinks with real files: {path}")
            size = int(size)
            if size <= 1024:
                declared = lfs_size(git(repo, "cat-file", "blob", oid.decode()))
                if declared is not None:
                    size = declared
                    lfs.append(path)
            files.append({"path": path, "bytes": size})
    else:
        for record in git(repo, "ls-files", "--stage", "-z").split(b"\0"):
            if record and record.split(b" ", 1)[0] == b"160000":
                raise ValueError("vendor submodule contents before submitting")
        names = set(git(repo, "ls-files", "-z", "--cached", "--others", "--exclude-standard").split(b"\0"))
        for name in sorted(names - {b""}):
            path = name.decode("utf-8", "surrogateescape")
            source = repo / path
            if source.is_symlink():
                raise ValueError(f"replace symlink with a real submitted file: {path}")
            if not source.exists():
                continue  # staged/working deletion; --revision checks the frozen tree
            if not source.is_file():
                raise ValueError(f"submission path is not a regular file: {path}")
            size = source.stat().st_size
            if size <= 1024:
                declared = lfs_size(source.read_bytes())
                if declared is not None:
                    size = declared
                    lfs.append(path)
            files.append({"path": path, "bytes": size})
    total = sum(item["bytes"] for item in files)
    return {"repo": str(repo), "revision": revision, "total_bytes": total,
            "limit_bytes": MAX_BYTES, "ok": total <= MAX_BYTES,
            "file_count": len(files), "lfs_pointers": lfs,
            "largest_files": sorted(files, key=lambda item: (-item["bytes"], item["path"]))[:10]}


def image_size(image: str) -> int:
    raw = subprocess.check_output(["docker", "image", "inspect", image], stderr=subprocess.PIPE)
    size = json.loads(raw)[0].get("Size")
    if type(size) is not int or size < 0:
        raise ValueError("Docker inspect did not report a valid Size")
    return size if size > MAX_BYTES else uncompressed_image_size(image)


def uncompressed_image_size(image: str) -> int:
    """Count exported uncompressed layer tar bytes; stop once the cap is exceeded."""
    import gzip
    import json
    import subprocess
    import tarfile

    proc = subprocess.Popen(["docker", "image", "save", image], stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL)
    sizes = {}
    manifest = None
    total = 0
    try:
        with tarfile.open(fileobj=proc.stdout, mode="r|") as archive:
            for member in archive:
                if not member.isfile():
                    continue
                source = archive.extractfile(member)
                if member.name == "manifest.json":
                    if member.size > 1024**2:
                        raise ValueError("image manifest is too large")
                    manifest = json.loads(source.read())
                    continue
                prefix = source.peek(512)
                if prefix.startswith(b"\x1f\x8b"):
                    size = 0
                    with gzip.GzipFile(fileobj=source) as layer:
                        while chunk := layer.read(min(1024**2, MAX_BYTES - total + 1)):
                            size += len(chunk)
                            total += len(chunk)
                            if total > MAX_BYTES:
                                return total
                    sizes[member.name] = size
                elif member.name.endswith("/layer.tar") or prefix[257:262] == b"ustar":
                    sizes[member.name] = member.size
                    total += member.size
                    if total > MAX_BYTES:
                        return total
        if proc.wait(timeout=120) != 0:
            raise ValueError("Docker image export failed")
        if not isinstance(manifest, list) or len(manifest) != 1:
            raise ValueError("build a single-platform linux/amd64 image before checking")
        layers = manifest[0].get("Layers", [])
        if not isinstance(layers, list) or not layers or any(path not in sizes for path in layers):
            raise ValueError("image layers are missing or use unsupported compression")
        return sum(sizes[path] for path in set(layers))
    except (tarfile.TarError, EOFError) as exc:
        raise ValueError("unable to read Docker image export; check the image and daemon") from exc
    finally:
        if proc.stdout:
            proc.stdout.close()
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=10)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--revision", help="check an exact commit instead of tracked/unignored working files")
    parser.add_argument("--image", help="also check a locally built Docker image, including dependencies")
    parser.add_argument("--json", action="store_true", help="emit a machine-readable report")
    args = parser.parse_args()
    try:
        report = inventory(args.repo, args.revision)
        if args.image:
            size = image_size(args.image)
            report["image"] = {"reference": args.image, "bytes": size, "ok": size <= MAX_BYTES}
            report["ok"] = report["ok"] and report["image"]["ok"]
    except (OSError, ValueError, subprocess.SubprocessError, KeyError, IndexError) as exc:
        print(f"Unable to check submission: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        status = "PASS" if report["ok"] else "FAIL"
        print(f"{status}: source {report['total_bytes']:,} / {MAX_BYTES:,} bytes "
              f"({report['total_bytes'] / 1024**3:.3f} / 1.500 GiB; {report['file_count']} files)")
        print("Scope: frozen commit" if args.revision else "Scope: tracked + unignored working files; push/commit before freeze")
        for item in report["largest_files"]:
            print(f"  {item['bytes']:>12,}  {item['path']}")
        if report["lfs_pointers"]:
            print("LFS assets counted at declared size; this does not verify uploaded LFS objects.")
        if args.image:
            print(f"Image: {report['image']['bytes']:,} / {MAX_BYTES:,} bytes")
        else:
            print("Built dependencies are checked separately: add --image YOUR_IMAGE after building.")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
