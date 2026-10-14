# Participant resource and submission limits

These limits apply to algorithms and non-language ML agents in UNO.

| Resource | Maximum per agent |
| --- | ---: |
| RAM | 1 GiB (1,073,741,824 bytes), including model loading, subprocesses and caches |
| GPU VRAM, if used | 1 GiB (1,073,741,824 bytes), including inference workspace and CUDA overhead |
| Participant code/assets | 1.5 GiB (1,610,612,736 bytes); approved dependency bases are exempt |

The size ceiling applies independently to frozen submitted code/assets and
uncompressed source-archive contents. Model weights and other participant assets
count. Git metadata is excluded. Do not vendor dependencies you expect to exempt:
put approved third-party packages in organizer-controlled dependency bases.

For Docker images, the organizer may set `approved_bases` to a list of immutable
linux/amd64 repository digests. Every image must then extend one of these bases.
The evaluator verifies its exact RootFS layer prefix, exempts that prefix, and
counts all participant-added uncompressed layer tar bytes against 1.5 GiB. This
includes layer metadata, installed extra packages, downloaded/generated assets,
and deleted or overwritten files. A participant label cannot authorize an
exemption. The full image can exceed 1.5 GiB because its approved base is exempt.

To exempt an extra dependency, the organizer publishes a new dependency-only
base and approves its new digest. Never include participant code, weights or
assets in an exempt base. This is conservative layer accounting, not automatic
classification of arbitrary packages or decoded-size inspection of compressed
model files. Source archives still have bounded uncompressed extraction.

Without an approved-base list, legacy whole-image admission remains strict:
the full uncompressed Docker image, including dependencies, must fit 1.5 GiB.

Existing timing and scoring remain unchanged: the default is 1,000 completed turns
per game (placement turns in UNO), subject to the announced configuration. Confirmed
finishers retain their placement points; unfinished players share the remaining
placement points equally. There is no new fixed one-point penalty. The CPU quota
remains 1 CPU per agent, startup is bounded at 60 seconds, and each action at 5 seconds.
A turn count is a game-length limit, not a measurement of CPU execution time.

## Check source and storage before submission

Run from your participant repository (Python 3.11+ and Git; no Python packages needed):

```sh
uv run --locked python tools/check_submission_size.py
uv run --locked python tools/check_submission_size.py --revision HEAD
```

The first command measures the current working files selected by Git: tracked files
plus untracked files not excluded by `.gitignore`, `.git/info/exclude`, or your Git
ignore configuration. Ignored untracked virtual environments, caches and build outputs
do not count. Already tracked files count even if an ignore rule matches them.
Required ignored assets must be explicitly added to Git before the freeze.

The second command measures only the chosen commit, including files at their committed
sizes. Uncommitted edits and new files cannot change that result. Use the exact frozen
commit SHA for the final check. Push that commit and all Git LFS objects before the
submission deadline. LFS pointer files are counted using the declared asset size;
this check does not prove that the LFS objects have been uploaded or can be retrieved.
Submodules and symlinks must be replaced by actual submitted files.

After building against the organizer base, verify its dependency exemption:

```sh
docker build --platform linux/amd64 -t participant-agent:check .
uv run --locked python tools/check_submission_size.py --revision HEAD --image participant-agent:check \
  --approved-base ORGANIZER_REPOSITORY@sha256:ACTUAL_DIGEST
```

Add `--json` for a machine-readable report. Exit codes are 0 for a passing size check,
1 for an oversized submission/image, and 2 when the check cannot be completed.
The report shows the largest files so you can identify what consumes space.
`.dockerignore` controls the image build context; it does not remove tracked files
from the frozen source submission. Run both source and image checks.

## Runtime enforcement

Official admission rejects participant layers above 1.5 GiB, unapproved ancestry
when bases are configured, and RAM/VRAM declarations above the ceilings. Without
approved bases, the whole-image limit applies. Smaller
per-agent requirements are allowed. Docker enforces the aggregate RAM limit and
CPU quota; swap is disabled. Model initialization must fit the RAM limit too.

GPU reservations and framework allocator controls are not hard hardware VRAM
partitions on a shared GPU. GPU agents must respect the 1 GiB budget and be profiled
under the event configuration. CPU-only agents receive no GPU. Organizers must
rehearse shared-GPU capacity and monitor actual allocations.
