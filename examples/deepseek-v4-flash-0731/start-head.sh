#!/usr/bin/env bash
# The published A16/DSpark profile. Network and model inputs come from .env.
set -euo pipefail

ray stop --force >/dev/null 2>&1 || true
ray start --head --node-ip-address="${HEAD_IP}" --port=6379 \
  --object-store-memory=1073741824 --num-cpus=2 \
  --include-dashboard=false --disable-usage-stats

# Do not start vLLM until both ranks, each with one GPU, have joined Ray.
python3 - <<'PY'
import os
import time
import ray

deadline = time.monotonic() + 900
ray.init(address="auto", log_to_driver=False, logging_level="ERROR")
expected = {os.environ["HEAD_IP"], os.environ["WORKER_IP"]}
while time.monotonic() < deadline:
    nodes = [node for node in ray.nodes() if node.get("Alive")]
    addresses = {node.get("NodeManagerAddress") for node in nodes}
    gpus = sum(node.get("Resources", {}).get("GPU", 0) for node in nodes)
    if expected.issubset(addresses) and gpus >= 2:
        print(f"Ray ready: {len(nodes)} nodes, {gpus:g} GPUs", flush=True)
        break
    print(f"Waiting for both Ray ranks: {addresses}, {gpus:g} GPUs", flush=True)
    time.sleep(5)
else:
    raise SystemExit("Timed out waiting for Ray head, worker and two GPUs")
ray.shutdown()
PY

exec vllm serve "${MODEL:?Set MODEL in .env}" \
  --revision "${MODEL_REVISION:?Set MODEL_REVISION in .env}" \
  --served-model-name "${MODEL}" \
  --host 0.0.0.0 --port 8000 --trust-remote-code \
  --tensor-parallel-size 2 --pipeline-parallel-size 1 \
  --distributed-executor-backend ray \
  --gpu-memory-utilization 0.90 \
  --linear-backend auto --moe-backend b12x \
  --sparse-indexer-topk-backend auto \
  --kv-cache-dtype fp8 --kv-cache-memory-bytes 21474836480 \
  --block-size 256 --max-model-len 524288 \
  --max-num-seqs 8 --max-num-batched-tokens 8192 \
  --tokenizer-mode deepseek_v4 \
  --enable-auto-tool-choice --tool-call-parser deepseek_v4 \
  --reasoning-parser deepseek_v4 \
  --default-chat-template-kwargs '{"thinking":false}' \
  --compilation-config '{"cudagraph_mode":"FULL_AND_PIECEWISE"}' \
  --max-cudagraph-capture-size 32 \
  --speculative-config '{"method":"dspark","num_speculative_tokens":3,"draft_sample_method":"greedy"}' \
  --per-request-spec-decode-metrics summary \
  --enable-prefix-caching --async-scheduling
