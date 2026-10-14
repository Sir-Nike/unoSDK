# Participant image standard

Submit a Linux/amd64 `registry/repository@sha256:<64 lowercase hex>` reference.
Images must use exec-form ENTRYPOINT and `USER 10000:10000`, contain all code,
dependencies and model weights, and require no runtime downloads or networking.
Do not declare Docker VOLUMEs. Only bounded `/tmp` scratch is writable.

Required labels (example CPU algorithm):

```dockerfile
LABEL org.triad-uno.protocol="1.0" org.triad-uno.runtime="2" org.triad-uno.sdk="uno-sdk-2" \
      org.triad-uno.cpu="1" org.triad-uno.ram-bytes="1073741824" \
      org.triad-uno.scratch-bytes="1073741824" org.triad-uno.pids="128" \
      org.triad-uno.gpu="false" org.triad-uno.gpu-bytes="0"
```

Declare positive CPU/RAM/scratch/PIDs; GPU bytes must be positive exactly when
GPU=true. Requests exceeding event policy are rejected. Labels are enforced by
Docker where supported and by shared host admission; GPU bytes are accounting,
not a hard CUDA memory cap. The prescribed UID is enforced again at launch.

`Dockerfile.cpu`: standard-library algorithm. `Dockerfile.ml-cpu`: packaged
untrained Torch policy running on CPU. `Dockerfile.gpu`: the same UNO-compatible
fixture requiring CUDA. The GPU example requests 0.5 CPU, 1 GiB RAM, 1 GiB scratch,
128 PIDs and 512 MiB GPU reservation. The CPU and CUDA fixtures pin Torch
2.7.1+cpu and 2.7.1+cu128 in separate `pyproject.toml`/`uv.lock` pairs under
`examples/ml_fixture/cpu/` and `examples/ml_fixture/cu128/`. Each Dockerfile
copies uv 0.11.28 from its published image, syncs the matching lock with
`--locked`, and carries only that environment into the final image. Pin your
Python and uv base images before a release. For air-gapped builds, stage the
complete artifact cache and base images first, then test the exact build with
uv's offline mode; do not claim an offline build works until the complete
transitive set is available.

The CUDA template uses `UV_HTTP_TIMEOUT=300` and a BuildKit cache for completed
dependency downloads. Override the timeout with `--build-arg UV_HTTP_TIMEOUT=600`
on a slow connection. This is a network timeout, not a total build-time limit.
Keep Docker's build cache between retries; the CUDA package downloads are large.
The standard CPU template does not install Torch or CUDA.

Use strict newline-delimited UNO SDK JSON on stdin/stdout, never Bluff actions.
Reply to initialize with ready, act with a supplied legal `action_id`, and
game_over with done. Echo the exact request ID. stdout is protocol-only;
diagnostics go to stderr. Bounds: 4 MiB line, 1 MiB checkpoint, 10 MiB stderr
per attempt, default 64 MiB stdout per attempt. Floods are participant faults.
The published UNO configuration always has four players and one deck. It also
contains initial_hand_size, max_turns and position_points. Use hand_sizes to read
the observation shape; do not accept a configurable table size. Own cards use local
h-refs; seeds and other hands are never exposed. Full public history remains available.

Persistent state belongs in JSON `agent_state`, not files. A null returned state
keeps the prior accepted checkpoint. On restart, initialize loads weights; the
next act supplies the last checkpoint. Process memory and `/tmp` are expendable.
Correct initialization, all UNO decision phases, restart and terminal shutdown
are required for admission. A failed participant gets documented legal fallback
actions; faults and fallbacks appear in event aggregates.

For CUDA, call `uno_agent.gpu.configure_torch(torch, require_cuda=True)` before
loading your model onto the device. Only the assigned device is visible as
cuda:0; `UNO_GPU_MEMORY_BYTES` is supplied by the organizer. PyTorch's allocator
limit does not cap all context, CUDA or library allocations. Shared arbitrary
CUDA has no hard memory isolation and concurrency does not guarantee simultaneous
kernels. The fixture is a verification tool, not a competitive trained model.

CPU preflight: `python -m triad_uno.image_check IMAGE --manifest EVENT.json`.
GPU preflight: organizer-approved `python -m triad_uno.hardware EVENT.json
--output verification-output/preflight --mode preflight --execute`.
See the organizer's RUNNING.md for build, registry, event and resume commands.

## Approved shared GPU base

`Dockerfile.gpu.shared` uses the official PyTorch 2.7.1 / CUDA 12.8 runtime:

```dockerfile
FROM pytorch/pytorch@sha256:c16f4c749e2d9e96878875cdf6cc45cddda1d1a36fddd371dd6f2360f1b6e2a2
```

This runtime includes Blackwell sm_100/sm_120 kernels. The local fixture creates
its own policy weights in a build stage; those assets count as participant layers.
The organizer approves the digest in `examples/approved-bases.json` and sets
`approved_bases` on the event manifest. All participant-added layers count toward
1.5 GiB; exact approved base layers are exempt. Additional installed dependencies
count unless the organizer publishes and approves a new dependency-only base.

```sh
docker build --platform linux/amd64 -f Dockerfile.gpu.shared -t uno-ml:shared .
python tools/check_submission_size.py --image uno-ml:shared \
  --approved-base pytorch/pytorch@sha256:c16f4c749e2d9e96878875cdf6cc45cddda1d1a36fddd371dd6f2360f1b6e2a2
```

The checker is included in unoSDK. Participant images must be published and
submitted by immutable digest. Docker reuses identical base layers on the same
host; the standalone `Dockerfile.gpu` installs its own environment and does not
receive this dependency exemption. CPU-only bases need separate organizer approval.
