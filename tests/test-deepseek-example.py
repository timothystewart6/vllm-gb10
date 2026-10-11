#!/usr/bin/env python3
"""Static contract checks for the public, pinned DeepSeek V4 dual-GB10 recipe.

These checks need no GPU, Docker daemon, or private deployment environment.
The hardware-only live serving checks remain documented in the example README.
"""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RECIPE = ROOT / "examples" / "deepseek-v4-flash-0731"
IMAGE = "ghcr.io/timothystewart6/vllm-gb10@sha256:39fc41b2e0a91d0c75fe03cbf00a715524bd8fab23114d37710a26e1de034333"
REVISION = "7872f01b1d1fe23eabc4c98b48bffcef5a386062"


def main():
    expected = [
        ".env.example",
        "docker-compose.head.yaml",
        "docker-compose.worker.yaml",
        "start-head.sh",
        "start-worker.sh",
        "README.md",
        "benchmarks.md",
        "benchmark-results.json",
    ]
    for filename in expected:
        assert (RECIPE / filename).is_file(), f"Missing recipe file: {filename}"

    env = (RECIPE / ".env.example").read_text()
    head = (RECIPE / "docker-compose.head.yaml").read_text()
    worker = (RECIPE / "docker-compose.worker.yaml").read_text()
    serve = (RECIPE / "start-head.sh").read_text()
    join = (RECIPE / "start-worker.sh").read_text()
    docs = (RECIPE / "README.md").read_text()
    bench = (RECIPE / "benchmarks.md").read_text()
    results = json.loads((RECIPE / "benchmark-results.json").read_text())

    assert f"IMAGE={IMAGE}" in env
    assert f"MODEL_REVISION={REVISION}" in env
    assert "MODEL=deepseek-ai/DeepSeek-V4-Flash-0731" in env
    assert "IMAGE" in head and "IMAGE" in worker
    assert "HF_CACHE_DIR" in head and "HF_CACHE_DIR" in worker
    assert "HF_HUB_OFFLINE=0" in env  # allow download on first boot
    assert "HF_HUB_OFFLINE" in head and "HF_HUB_OFFLINE" in worker
    assert "IB_HCA" in head and "IB_HCA" in worker
    assert "IB_GID_INDEX" in head and "IB_GID_INDEX" in worker
    assert "VLLM_B12X_MOE_FP4_FORCE_A16" in head and "VLLM_B12X_MOE_FP4_FORCE_A16" in worker
    assert "VLLM_USE_BREAKABLE_CUDAGRAPH" in head and "VLLM_USE_BREAKABLE_CUDAGRAPH" in worker

    serve_flags = [
        "--tensor-parallel-size 2", "--pipeline-parallel-size 1",
        "--linear-backend auto", "--moe-backend b12x",
        "--sparse-indexer-topk-backend auto",
        "--kv-cache-dtype fp8", "--kv-cache-memory-bytes 21474836480",
        "--block-size 256", "--max-model-len 524288",
        "--max-num-seqs 8", "--max-num-batched-tokens 8192",
        "--gpu-memory-utilization 0.90",
        "--tokenizer-mode deepseek_v4",
        "--enable-auto-tool-choice", "--tool-call-parser deepseek_v4",
        "--reasoning-parser deepseek_v4",
        "--max-cudagraph-capture-size 32",
        "--enable-prefix-caching", "--async-scheduling",
    ]
    for flag in serve_flags:
        assert flag in serve, f"Missing qualified vLLM flag: {flag}"

    assert "FULL_AND_PIECEWISE" in serve
    assert '"method":"dspark","num_speculative_tokens":3,"draft_sample_method":"greedy"' in serve
    assert "ray start --head" in serve and "ray status" in docs
    assert '--address="${HEAD_IP}:6379"' in join
    assert 'ray.nodes()' in serve  # waits for both nodes before starting vLLM
    assert 'deepseek-v4-flash-0731/' in (ROOT / "README.md").read_text()

    assert results["software"]["image"] == IMAGE
    assert results["software"]["model_revision"] == REVISION
    runtime = results["runtime"]
    assert runtime["max_model_len"] == 524288
    assert runtime["kv_cache_memory_bytes_per_gpu"] == 21474836480
    assert runtime["moe_backend"] == "b12x" and runtime["linear_backend"] == "auto"
    assert runtime["speculative"]["num_speculative_tokens"] == 3
    assert len(results["short_context"]) == 5
    assert len(results["startup_means"]) == 3
    assert len(results["long_context"]) == 4
    assert results["near_limit"]["success"] is True
    assert "113.098" in bench and "115.064" in bench
    assert "523520" in bench and "590.904" in bench

    # Public docs must not leak internal source repositories or hostnames.
    forbidden = ("local-ai-lab", "home-ops", "techtronic.us", "zIgnore", "tstewart")
    for filename in expected:
        content = (RECIPE / filename).read_text()
        for value in forbidden:
            assert value not in content, f"Private name in {filename}: {value}"

    print("DeepSeek V4 recipe static contract checks passed.")


if __name__ == "__main__":
    main()
