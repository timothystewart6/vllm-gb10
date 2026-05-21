# vllm-gb10

Reproducible [vLLM](https://github.com/vllm-project/vllm) Docker image for the
**NVIDIA DGX Spark (GB10 / sm_121a)**. Every input - CUDA base image, PyTorch
stack, NCCL, FlashInfer, vLLM - is pinned by commit SHA or digest. The same
`versions.env` always produces the same image.

## Quick start

```bash
docker pull ghcr.io/timothystewart6/vllm-gb10:v0.20.1-gb10.0
```

Run a model (single Spark):

```bash
docker run --rm -it \
  --gpus all \
  --ipc=host \
  --network host \
  -v ~/.cache/huggingface:/root/.cache/huggingface \
  ghcr.io/timothystewart6/vllm-gb10:v0.20.1-gb10.0 \
  vllm serve <model> --host 0.0.0.0 --port 8000 --gpu-memory-utilization 0.7
```

## Image tags

Each build publishes four tags:

| Tag | Notes |
|-----|-------|
| `v0.20.1-gb10.0` | Canonical, immutable. Identifies the vLLM version and our stack revision. |
| `v0.20.1-cu13.2-torch2.11-gb10.0` | Same image - adds CUDA and PyTorch versions for quick scanning. |
| `latest` | Mutable - always points at the most recent green build of `main`. |
| `sha-<short_sha>` | Immutable, tied to the exact Git commit that produced it. |

`gb10.<N>` increments when any non-vLLM input changes (CUDA, PyTorch, NCCL,
FlashInfer, etc.) on the same vLLM version. It resets to `0` when `VLLM_REF`
bumps. There is intentionally no bare `v0.20.1` tag - it would be mutable.

## Bumping versions

```bash
# Edit versions.env, then:
bash scripts/bump.sh   # resolves new SHAs, regenerates lockfiles
git add versions.env locks/
git commit -m "chore(deps): bump ..."
git push
```

CI triggers automatically on changes to `versions.env`, `Dockerfile`, `locks/`,
`scripts/`, or `checksums/`. A green build publishes updated image tags.

## License

MIT - see [LICENSE](LICENSE).
