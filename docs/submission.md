# Submission guide

The event submission is a **Linux/amd64 Docker image**, identified by its immutable
`registry/repository@sha256:<64 lowercase hexadecimal digits>` reference. Follow
[the participant image contract](docker-submission.md) for mandatory labels,
`USER 10000:10000`, exec-form ENTRYPOINT and resource limits. Tags alone are not
accepted. A source repository URL and commit SHA identify provenance; they do
not replace the runtime image digest.

## Package your agent

The image must include your agent, all runtime dependencies, optional model
weights and the vendored protocol components. Declare Python dependencies with
`uv add`, commit `pyproject.toml` and `uv.lock`, and install packages while building.
The standard-library starter needs no dependency installation. For offline builds,
preload pinned parent images and use a verified target-platform wheelhouse as
explained in [the quickstart](quickstart.md). Evaluation cannot fetch packages,
weights or external services.

Keep `submission.json` consistent with your agent and execution mode. Runtime
allocation is checked using image labels. GPU agents must use the published GPU
policy, keep peak allocation within 1 GiB, and configure the supplied allocator
helper before loading weights. GPU admission reservations are not a hard CUDA
memory quota.

## Test and publish

Run from the repository root:

```sh
uv sync --locked
uv run --locked python tools/check.py --agent python
uv run --locked pytest
uv run --locked python tools/local_match.py --games 3 --restart-test
docker build --platform linux/amd64 -t uno-agent:submission .
uv run --locked python tools/check_submission_size.py --revision HEAD --image uno-agent:submission
```

Review the source and commit all required files before the final build. Publish
the tested image to the registry announced by organizers:

```sh
docker tag uno-agent:submission YOUR_REGISTRY/YOUR_TEAM:submission
docker push YOUR_REGISTRY/YOUR_TEAM:submission
docker image inspect YOUR_REGISTRY/YOUR_TEAM:submission --format '{{json .RepoDigests}}'
```

Send the resulting immutable digest through the announced registration channel.
Organizers pull images before evaluation; runtime launches use the frozen image
and never pull. Registration, cutoff and replacement procedures are announced
separately by organizers.

## Provenance and size

Keep `provenance.md` with your team, repository URL, full commit SHA,
language/framework, third-party libraries, pretrained models, data sources and
code attribution. Keep the template's vendored-contract attribution and
`VENDOR.json`. The SDK does not supply a registration service.

RAM and GPU VRAM are each limited to 1 GiB. Source and image size are each limited
to 1.5 GiB. Use the Git-aware size checker before submitting; see
[resource limits](resource-limits.md). Passing local checks establishes protocol
compatibility, not playing strength or final organizer admission.
