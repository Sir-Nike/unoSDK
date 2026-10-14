"""Measure participant layers while exempting verified organizer dependency bases.

This standard-library-only module is also vendored into the participant SDK.
Approval comes from the organizer's digest list, never participant labels.
"""

from __future__ import annotations

import gzip
import json
import re
import subprocess
import tarfile

DEFAULT_LIMIT_BYTES = 3 * 1024**3 // 2
_REFERENCE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]*@sha256:[0-9a-f]{64}")
_DIFF_ID = re.compile(r"sha256:[0-9a-f]{64}")


def _inspect(image):
    raw = subprocess.check_output(
        ["docker", "image", "inspect", image], stderr=subprocess.PIPE, timeout=15
    )
    return json.loads(raw)[0]


def _layers(data):
    layers = data.get("RootFS", {}).get("Layers")
    if (
        not isinstance(layers, list)
        or not layers
        or any(
            not isinstance(layer, str) or not _DIFF_ID.fullmatch(layer)
            for layer in layers
        )
    ):
        raise ValueError("image inspect must report valid RootFS layer digests")
    return layers


def approved_base_prefix(data, approved_bases):
    """Return the longest approved, immutable, exact layer prefix of this image."""
    if not isinstance(approved_bases, (list, tuple)) or not approved_bases:
        raise ValueError("approved_bases must be a nonempty list of immutable digests")
    layers = _layers(data)
    match = None
    for reference in approved_bases:
        if not isinstance(reference, str) or not _REFERENCE.fullmatch(reference):
            raise ValueError("approved base must be repository@sha256:<64 hex digits>")
    for reference in dict.fromkeys(approved_bases):
        base = _inspect(reference)
        if reference not in base.get("RepoDigests", []):
            raise ValueError("base inspect did not confirm its immutable digest")
        if base.get("Os") != "linux" or base.get("Architecture") != "amd64":
            raise ValueError("approved base must be linux/amd64")
        base_layers = _layers(base)
        if layers[: len(base_layers)] == base_layers and (
            match is None or len(base_layers) > match[1]
        ):
            match = (reference, len(base_layers))
    if match is None:
        raise ValueError("submission does not extend an approved dependency base")
    return match


def exported_layer_sizes(image):
    """Read actual uncompressed layer sizes in manifest order, without extraction.

    Layer totals include deleted/overwritten files and build-generated assets.
    Docker metadata Size is not subtracted: compressed image stores may report
    different sizes from the actual uncompressed layer tar bytes.
    """
    proc = subprocess.Popen(
        ["docker", "image", "save", image],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    sizes = {}
    manifest = None
    try:
        with tarfile.open(fileobj=proc.stdout, mode="r|") as archive:
            for member in archive:
                if not member.isfile():
                    continue
                source = archive.extractfile(member)
                if member.name == "manifest.json":
                    if manifest is not None or member.size > 1024**2:
                        raise ValueError("invalid Docker export manifest")
                    manifest = json.loads(source.read())
                    continue
                prefix = source.peek(512)
                if prefix.startswith(b"\x1f\x8b"):
                    size = 0
                    with gzip.GzipFile(fileobj=source) as layer:
                        while chunk := layer.read(1024**2):
                            size += len(chunk)
                    sizes[member.name] = size
                elif (
                    member.name.endswith("/layer.tar")
                    or prefix[257:262] == b"ustar"
                    # OCI exports name blobs by hash; an empty layer is only
                    # two zero tar end blocks, without a ustar file header.
                    or (member.size >= 1024 and prefix[:512] == bytes(512))
                ):
                    sizes[member.name] = member.size
        if proc.wait(timeout=120) != 0:
            raise ValueError("Docker image export failed")
        if not isinstance(manifest, list) or len(manifest) != 1:
            raise ValueError(
                "build a single-platform linux/amd64 image before checking"
            )
        layers = manifest[0].get("Layers")
        if (
            not isinstance(layers, list)
            or not layers
            or any(not isinstance(path, str) or path not in sizes for path in layers)
        ):
            raise ValueError("image layers are missing or use unsupported compression")
        return [sizes[path] for path in layers]
    except (tarfile.TarError, EOFError) as exc:
        raise ValueError("unable to read Docker image export") from exc
    finally:
        if proc.stdout:
            proc.stdout.close()
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=10)


def measure_submission_image(
    image, approved_bases, *, data=None, limit_bytes=DEFAULT_LIMIT_BYTES
):
    """Count every participant-added layer; exempt only verified base layers."""
    data = _inspect(image) if data is None else data
    reference, count = approved_base_prefix(data, approved_bases)
    sizes = exported_layer_sizes(image)
    if len(sizes) != len(_layers(data)):
        raise ValueError("exported layers do not match image inspect")
    participant_bytes = sum(sizes[count:])
    return {
        "reference": image,
        "approved_base": reference,
        "base_layers": count,
        "total_bytes": sum(sizes),
        "exempt_base_bytes": sum(sizes[:count]),
        "participant_bytes": participant_bytes,
        "limit_bytes": limit_bytes,
        "ok": participant_bytes <= limit_bytes,
    }
