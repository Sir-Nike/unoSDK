# Participant resource and submission limits

These limits apply equally to algorithms and non-language ML agents in UNO and Bluff.

| Resource | Maximum per agent |
| --- | ---: |
| RAM | 1 GiB (1,073,741,824 bytes), including model loading, subprocesses and caches |
| GPU VRAM, if used | 1 GiB (1,073,741,824 bytes), including inference workspace and CUDA overhead |
| Complete submission | 1.5 GiB (1,610,612,736 bytes) |

The size ceiling applies independently to the total uncompressed frozen source/assets,
any submitted archive, and the final uncompressed Docker image. The image measurement
includes its base image and installed dependencies, measured as the sum of
uncompressed layer tar sizes (including layer metadata). Docker's compressed
image-store size alone is not used for this measurement. Compressing files or installing
packages during the build does not bypass this ceiling. Git metadata is excluded.

Existing timing and scoring remain unchanged: the default is 1,000 completed turns
per game (placement turns in Bluff), subject to the announced configuration. Confirmed
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

After building, include the installed runtime dependencies in the check:

```sh
docker build --platform linux/amd64 -t participant-agent:check .
uv run --locked python tools/check_submission_size.py --revision HEAD --image participant-agent:check
```

Add `--json` for a machine-readable report. Exit codes are 0 for a passing size check,
1 for an oversized submission/image, and 2 when the check cannot be completed.
The report shows the largest files so you can identify what consumes space.
`.dockerignore` controls the image build context; it does not remove tracked files
from the frozen source submission. Run both source and image checks.

## Runtime enforcement

Official admission rejects images larger than 1.5 GiB and RAM/VRAM declarations above
the ceilings, even if an organizer manifest attempts to raise the ceilings. Smaller
per-agent requirements are allowed. Docker enforces the aggregate RAM limit and
CPU quota; swap is disabled. Model initialization must fit the RAM limit too.

GPU reservations and framework allocator controls are not hard hardware VRAM
partitions on a shared GPU. GPU agents must respect the 1 GiB budget and be profiled
under the event configuration. CPU-only agents receive no GPU. Organizers must
rehearse shared-GPU capacity and monitor actual allocations.
