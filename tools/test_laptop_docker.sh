#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
mkdir -p reports
sudo python3 tools/local_match.py --games 3 --timeout 30 --report reports/laptop-docker-cpu.json --cmd 'docker run --rm -i --network none --read-only --tmpfs /tmp:rw,noexec,nosuid,size=1g --memory 1g --cpus 1 --pids-limit 128 --cap-drop ALL --security-opt no-new-privileges triad-uno-laptop:cpu'
sudo python3 tools/local_match.py --games 1 --timeout 60 --report reports/laptop-docker-gpu.json --cmd 'docker run --rm -i --gpus all --network none --read-only --tmpfs /tmp:rw,noexec,nosuid,size=1g --memory 1g --cpus 0.5 -e UNO_GPU_MEMORY_BYTES=536870912 --pids-limit 128 --cap-drop ALL --security-opt no-new-privileges triad-uno-laptop:gpu'
sudo chown "$(id -u):$(id -g)" reports/laptop-docker-*.json
