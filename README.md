# vllm-gb10

[![Build](https://github.com/timothystewart6/vllm-gb10/actions/workflows/build-image.yaml/badge.svg)](https://github.com/timothystewart6/vllm-gb10/actions/workflows/build-image.yaml)
[![Latest release](https://img.shields.io/github/v/release/timothystewart6/vllm-gb10)](https://github.com/timothystewart6/vllm-gb10/releases/latest)
[![GHCR](https://img.shields.io/badge/ghcr.io-vllm--gb10-blue)](https://github.com/timothystewart6/vllm-gb10/pkgs/container/vllm-gb10)

Reproducible [vLLM](https://github.com/vllm-project/vllm) Docker image for the
**NVIDIA DGX Spark (GB10 / sm_121a)**. Every input - CUDA base image, PyTorch
stack, NCCL, FlashInfer, vLLM - is pinned by commit SHA or digest. The same
`versions.env` always produces the same image.

> **Hardware:** DGX Spark (GB10 SoC) only. The image targets `linux/arm64`
> with `TORCH_CUDA_ARCH_LIST=12.1a`. It will not run on x86 or other GPU
> architectures.

## Quick start

Pull the latest release and serve a model:

```bash
docker pull ghcr.io/timothystewart6/vllm-gb10:latest

docker run --rm -it \
  --gpus all \
  --ipc=host \
  --network host \
  -v ~/.cache/huggingface:/root/.cache/huggingface \
  ghcr.io/timothystewart6/vllm-gb10:latest \
  vllm serve <model> --host 0.0.0.0 --port 8000 --gpu-memory-utilization 0.7
```

For a pinned version see the [releases page](https://github.com/timothystewart6/vllm-gb10/releases)
for the full component table and immutable tag for each build.

## What's in the image

Each release page lists the exact versions of every component. Key stack:

| Component | Pinned by |
|---|---|
| CUDA base image | digest (`sha256:...`) |
| vLLM | git commit SHA |
| PyTorch / TorchVision / TorchAudio / Triton | exact version |
| NCCL | git commit SHA (built from source) |
| FlashInfer | git commit SHA |
| Ray, uv, and other runtime deps | lockfile hash |

All pins live in [`versions.env`](versions.env). All lockfiles live in [`locks/`](locks/).

## Image tags

Each build publishes four tags:

| Tag | Notes |
|---|---|
| `v0.20.1-gb10.0` | Canonical, immutable. vLLM version + stack revision. |
| `v0.20.1-cu13.2-torch2.11-gb10.0` | Same image - adds CUDA and PyTorch versions for quick scanning. |
| `latest` | Mutable - always points at the most recent green build of `main`. |
| `sha-<short_sha>` | Immutable, tied to the exact Git commit that produced it. |

`gb10.<N>` increments when any non-vLLM input changes (CUDA, PyTorch, NCCL,
FlashInfer, etc.) on the same vLLM version. It resets to `0` when `VLLM_REF`
bumps. There is intentionally no bare `v0.20.1` tag - it would be mutable.

## Bumping versions

**PR flow (no terminal required after the initial edit):**

1. Edit one or more `_REF` lines in `versions.env` on any branch
2. Open a pull request - CI runs `scripts/bump.sh` on the Spark, then commits
   the resolved `_COMMIT` SHAs, updated `GB10_BUILD`, and regenerated lockfiles
   back to your branch automatically
3. Review the diff, merge
4. A green build publishes updated image tags to GHCR, then creates a GitHub
   Release automatically

**Manual flow (run everything yourself):**

```bash
# On the DGX Spark (Linux aarch64) - must not run on macOS or x86:
bash scripts/bump.sh   # resolves new SHAs, regenerates lockfiles
git add versions.env locks/
git commit -m "chore(deps): bump ..."
git push
```

CI triggers on changes to `versions.env`, `Dockerfile`, `locks/`, `scripts/`,
or `checksums/`. A green build publishes updated image tags and creates a
GitHub Release automatically.

## `--max-model-len` and `max_position_embeddings`

vLLM does **not** hard-cap `--max-model-len` at the model's `max_position_embeddings`. It logs a warning at startup and loads anyway. The first inference request then triggers a CUDA device-side assert that kills the engine — even with `VLLM_ALLOW_LONG_MAX_MODEL_LEN=1`. On the GB10, where a 60+ GB model takes 13–15 minutes to load over NFS, this produces a painful crash loop.

See [issue #7](https://github.com/timothystewart6/vllm-gb10/issues/7) for the full trace and root cause.

### Known-safe values

| Model | `max_position_embeddings` |
|-------|--------------------------|
| `Qwen/Qwen3-32B` | 40,960 |
| `Qwen/Qwen3-14B` | 40,960 |
| `Qwen/Qwen3-8B` | 40,960 |
| `Qwen/Qwen2.5-32B-Instruct` | 32,768 |
| `meta-llama/Llama-3.3-70B-Instruct` | 131,072 |
| `meta-llama/Llama-3.1-8B-Instruct` | 131,072 |
| `mistralai/Mistral-Small-Instruct-2409` | 32,768 |
| `google/gemma-3-27b-it` | 131,072 |
| `microsoft/Phi-4` | 16,384 |
| `deepseek-ai/DeepSeek-V3` | 163,840 |

Check any model without downloading weights:

```bash
python3 -c "
from huggingface_hub import hf_hub_download
import json
cfg = json.load(open(hf_hub_download('Qwen/Qwen3-32B', 'config.json')))
print(cfg['max_position_embeddings'])
"
```

### Preflight wrapper

This image ships `vllm-serve-safe` — a thin wrapper around `vllm serve` that validates `--max-model-len` before launch:

```bash
# Default: refuse to start if --max-model-len exceeds the model's limit
docker run --gpus all \
  -v ~/.cache/huggingface:/root/.cache/huggingface \
  ghcr.io/timothystewart6/vllm-gb10:latest \
  vllm-serve-safe Qwen/Qwen3-32B --max-model-len 65536
# [vllm-serve-safe] REFUSING TO START. max_position_embeddings=40960 requested=65536

# Or auto-clamp to the model's actual maximum:
docker run --gpus all -e VLLM_GB10_AUTOCLAMP=1 \
  -v ~/.cache/huggingface:/root/.cache/huggingface \
  ghcr.io/timothystewart6/vllm-gb10:latest \
  vllm-serve-safe Qwen/Qwen3-32B --max-model-len 65536
# [vllm-serve-safe] clamping --max-model-len 65536 -> 40960
```

All other flags pass through unchanged. `vllm serve` still works as before — `vllm-serve-safe` is strictly opt-in.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Security issues: [SECURITY.md](SECURITY.md).

## License

MIT - see [LICENSE](LICENSE).
