# vllm-gb10

[![Build](https://github.com/timothystewart6/vllm-gb10/actions/workflows/build-image.yaml/badge.svg?branch=main)](https://github.com/timothystewart6/vllm-gb10/actions/workflows/build-image.yaml)
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
| FlashInfer | git commit SHA (built from source) |
| vllm-rs Rust frontend | built from source (axum HTTP server + PyO3 tool-parser module) |
| bitsandbytes, accelerate | exact version (4-bit/8-bit quantization and HuggingFace model loading) |
| Ray, uv, and other runtime deps | lockfile hash |

All pins live in [`versions.env`](versions.env). All lockfiles live in [`locks/`](locks/).

## Known limitations

See the [issues tab](https://github.com/timothystewart6/vllm-gb10/issues) for tracked upstream compatibility gaps.

## Image tags

Each build publishes four tags:

| Tag | Notes |
|---|---|
| `v0.24.0-gb10.0` | Canonical, immutable. vLLM version + stack revision. |
| `v0.24.0-cu13.2-torch2.11-gb10.0` | Same image - adds CUDA and PyTorch versions for quick scanning. |
| `latest` | Mutable - always points at the most recent green build of `main`. |
| `sha-<short_sha>` | Immutable, tied to the exact Git commit that produced it. |

`gb10.<N>` increments when any non-vLLM input changes (CUDA, PyTorch, NCCL,
FlashInfer, etc.) on the same vLLM version. It resets to `0` when `VLLM_REF`
bumps. There is intentionally no bare `v0.24.0` tag - it would be mutable.

## Bumping versions

1. Edit one or more `_REF` lines in `versions.env` on a branch
2. Open a pull request. GitHub-hosted checks validate the proposed changes
   without running contributor code on the DGX Spark
3. After reviewing an exact commit SHA, a maintainer promotes fork changes to
   an upstream integration branch, opens a replacement PR, and manually
   dispatches `run-bump.yaml` from `main`. The workflow imports validated build
   inputs as data, runs trusted `main` scripts, and commits generated files to
   the integration branch
4. Review the generated diff, then merge
5. A green build on `main` publishes updated image tags to GHCR and creates
   a GitHub Release automatically

You do not need to SSH into the Spark or run anything locally.

Maintainers should follow the
[Contributor CI security workflow](docs/contributor-ci-security.md) for the
complete fork-promotion and approval process.

CI also triggers on changes to `Dockerfile`, `locks/`, `scripts/`, and
`checksums/`.

## Contributing

Start with the [repository guide](docs/repository-guide.md), then follow
[CONTRIBUTING.md](CONTRIBUTING.md). Security issues:
[SECURITY.md](SECURITY.md).

## License

MIT - see [LICENSE](LICENSE).
