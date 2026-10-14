# Laptop Docker and GPU testing

The RTX 5060 Laptop GPU requires a PyTorch build supporting Blackwell (sm_120).
The event CUDA 12.4 / PyTorch 2.6 fixtures fail a real GPU calculation on this laptop.
The separate laptop build pins PyTorch 2.7.1+cu128 and locks every dependency.
The existing event Dockerfiles and CPU dependencies remain unchanged.

Official compatibility reference: https://pytorch.org/blog/pytorch-2-7/

Run from `/home/sirnike/unoSDK`:

```sh
uv run --locked pytest -q
uv run --locked python tools/check.py
uv run --locked python tools/local_match.py --games 3 --report reports/laptop-local.json
sudo docker build -f Dockerfile.cpu -t triad-uno-laptop:cpu .
sudo docker build -f Dockerfile.gpu.laptop -t triad-uno-laptop:gpu .
```

After building the images, run `bash tools/test_laptop_docker.sh` to repeat the CPU and GPU full-game tests with network access disabled, read-only containers and CPU/RAM/process limits. GPU allocator budgets are supplied by environment variables; these are not hardware VRAM isolation.

Both GPU fixtures execute their models on CUDA and require CUDA initialization to succeed.
These are integration fixtures, not competitive ML models. Local games establish correctness,
not playing strength or official organizer admission. The Docker game reports are written to `reports/laptop-docker-cpu.json` and `reports/laptop-docker-gpu.json`.
