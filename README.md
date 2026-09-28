# vllm-gb10

[![Build](https://github.com/timothystewart6/vllm-gb10/actions/workflows/build-image.yaml/badge.svg?branch=main)](https://github.com/timothystewart6/vllm-gb10/actions/workflows/build-image.yaml)
[![Latest release](https://img.shields.io/github/v/release/timothystewart6/vllm-gb10)](https://github.com/timothystewart6/vllm-gb10/releases/latest)
[![GHCR](https://img.shields.io/badge/ghcr.io-vllm--gb10-blue)](https://github.com/timothystewart6/vllm-gb10/pkgs/container/vllm-gb10)

> Built and verified on a DGX Spark generously donated by
> [NVIDIA](#thanks-nvidia).

Production-ready, bleeding-edge, upstream-first [vLLM](https://github.com/vllm-project/vllm)
Docker image for the **NVIDIA DGX Spark (GB10 / sm_121a)**, verified against
real models on every release.

The project takes an upstream-first approach, tracking vLLM and its dependency
stack as closely as possible without maintaining a separate Spark fork or
downstream feature set.

Build inputs are pinned by commit SHA, digest, or exact version, including the
CUDA base image, PyTorch stack, NCCL, FlashInfer, and vLLM. Releases record the
exact stack used to build each image.

Every release is served and tested against real models on the DGX Spark before
`latest` is promoted. CI loads the model catalog, sends real requests, checks
the runtime paths each model is meant to exercise, benchmarks the result, and
publishes the verification data with the release.

> **DGX Spark only**
>
> The image targets `linux/arm64` with `TORCH_CUDA_ARCH_LIST=12.1a`. It will not
> run on x86 or other GPU architectures.

## Quick start

Pull the latest verified release and serve a model.

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

For a pinned version, see the
[releases page](https://github.com/timothystewart6/vllm-gb10/releases) for the
full component table, model verification results, benchmarks, and immutable
tag for each build.

## Model verification

Every release is verified against real models on a DGX Spark before it is
published. The current catalog covers dense, MoE, Mamba, multimodal, reasoning,
tool calling, NVFP4, FP8 KV cache, and speculative decoding across four models.

### Current verification matrix

A check means the test is part of the release gate for that model. A dash means
the test does not apply to that model.

| Test | `google/gemma-4-12B-it` | `nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4` | `Qwen/Qwen3-0.6B` | `nvidia/Qwen3.8-27B-NVFP4` |
|---|:---:|:---:|:---:|:---:|
| Model startup (health) | ✓ | ✓ | ✓ | ✓ |
| `/v1/models` registration | ✓ | ✓ | ✓ | ✓ |
| Deterministic generation | ✓ | ✓ | ✓ | ✓ |
| Streaming generation | ✓ | ✓ | ✓ | ✓ |
| Response JSON validity | ✓ | ✓ | ✓ | ✓ |
| Tool calling | - | ✓ | - | ✓ |
| Reasoning parser | ✓ | ✓ | - | ✓ |
| Multimodal image input | ✓ | - | - | ✓ |
| NVFP4 execution | - | ✓ | - | ✓ |
| FP8 KV cache | - | ✓ | - | ✓ |
| MoE execution | - | ✓ | - | - |
| Mamba execution | - | ✓ | - | - |
| Speculative decoding | - | ✓ | - | - |
| llama-benchy pp/tg | ✓ | ✓ | ✓ | ✓ |
| llama-benchy concurrency | ✓ | ✓ | ✓ | ✓ |
| Post-bench health | ✓ | ✓ | ✓ | ✓ |
| Server log captured | ✓ | ✓ | ✓ | ✓ |
| Clean shutdown | ✓ | ✓ | ✓ | ✓ |

Verification covers functional serving behavior, model-specific runtime paths,
and benchmark performance. Any failing required check blocks the release, and
the verification matrix and benchmark results are published with each GitHub
release.

See [Model verification](docs/model-verification.md) for the model revisions,
test methodology, runtime markers, and benchmark configuration.

## What's in the image

Each release page lists the exact versions of every component.

| Component | Pinned by |
|---|---|
| CUDA base image | digest (`sha256:...`) |
| vLLM | git commit SHA |
| PyTorch / TorchVision / TorchAudio / Triton | exact version |
| NCCL | git commit SHA, built from source |
| FlashInfer | git commit SHA, built from source |
| vllm-rs Rust frontend | built from source, including the axum HTTP server and PyO3 tool-parser module |
| NVSHMEM, TVM-FFI, TileLang, Numba | exact version |
| bitsandbytes, accelerate | exact version |
| transformers, quack-kernels, fastsafetensors, instanttensor | exact version |
| Ray, uv, and other runtime dependencies | lockfile hash |

All pins live in [`versions.env`](versions.env). All lockfiles live in
[`locks/`](locks/).

## Upstream first

This repo is intended to stay close to upstream vLLM rather than become a
separate Spark distribution. The image follows upstream implementations,
interfaces, and behavior wherever possible while packaging a stack that builds
and runs on GB10.

## Known limitations

See the [issues tab](https://github.com/timothystewart6/vllm-gb10/issues) for
tracked upstream compatibility gaps.

## Image tags

A successful `main` pipeline publishes four tags.

| Tag | Notes |
|---|---|
| `v0.30.0-gb10.3` | Canonical, immutable. vLLM version + stack revision. |
| `v0.30.0-cu13.2-torch2.13-gb10.3` | Same image with CUDA and PyTorch versions included for quick scanning. |
| `latest` | Mutable, promoted only after model verification passes. |
| `sha-<short_sha>` | Immutable, tied to the exact Git commit that produced it. |

`gb10.<N>` increments when any non-vLLM input changes, including CUDA, PyTorch,
NCCL, FlashInfer, or lock generation changes on the same vLLM version. It resets
to `0` when `VLLM_REF` bumps.

There is intentionally no bare `v0.30.0` tag because it would be mutable.

## Bumping versions

1. Edit one or more `_REF` lines in `versions.env` on a branch.
2. Open a pull request. GitHub-hosted checks validate the proposed changes
   without running contributor code on the DGX Spark.
3. After reviewing an exact commit SHA, a maintainer promotes fork changes to
   an upstream integration branch by dispatching **Promote fork PR** from
   `main`. After the replacement PR's hosted checks pass, the maintainer
   dispatches `run-bump.yaml` from `main`. The workflow imports validated
   build inputs as data, runs trusted `main` scripts, and commits generated
   files to the integration branch.
4. Review the generated diff, then merge.
5. The image is built on `main`, verified against the model catalog on the
   DGX Spark, and then published as a GitHub Release. `latest` is promoted only
   after verification passes.

You do not need to SSH into the Spark or run anything locally.

Maintainers should follow the
[Contributor CI security workflow](docs/contributor-ci-security.md) for the
complete fork-promotion and approval process.

CI also triggers on changes to `Dockerfile`, `locks/`, `scripts/`, and
`checksums/`.

## Thanks NVIDIA

NVIDIA donated the DGX Spark used to build and verify releases of vllm-gb10.
Having dedicated hardware lets the project test real models directly on GB10
as vLLM and the platform continue to evolve. Thanks to NVIDIA for making that
possible.

Learn more about the hardware on the
[DGX Spark product page](https://www.nvidia.com/en-us/products/workstations/dgx-spark/)
or [NVIDIA Marketplace](https://marketplace.nvidia.com/en-us/enterprise/personal-ai-supercomputers/?superchip=GB10).

## Contributing

Start with the [repository guide](docs/repository-guide.md), then follow
[CONTRIBUTING.md](CONTRIBUTING.md).

For security issues, see [SECURITY.md](SECURITY.md).

## License

MIT. See [LICENSE](LICENSE).
