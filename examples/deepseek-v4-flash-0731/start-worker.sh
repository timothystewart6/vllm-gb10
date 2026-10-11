#!/usr/bin/env bash
set -euo pipefail

ray stop --force >/dev/null 2>&1 || true
exec ray start --address="${HEAD_IP}:6379" \
  --node-ip-address="${WORKER_IP}" \
  --object-store-memory=1073741824 \
  --num-cpus=2 --disable-usage-stats --block
