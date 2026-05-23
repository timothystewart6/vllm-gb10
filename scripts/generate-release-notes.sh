#!/usr/bin/env bash
# scripts/generate-release-notes.sh
#
# Renders the GitHub Release body for a vllm-gb10 build to stdout.
# Used by:
#   - .github/workflows/build-image.yaml (release job, after a green build)
#   - .github/workflows/create-release.yaml (fallback for manually pushed tags)
#
# Required env:
#   TAG         - e.g. v0.21.0-gb10.0
#   GITHUB_SHA  - full commit sha of the build
# Required: versions.env in CWD (the script sources it).

set -euo pipefail

: "${TAG:?TAG env var required}"
: "${GITHUB_SHA:?GITHUB_SHA env var required}"

# shellcheck disable=SC1091
set -a; . ./versions.env; set +a

SHORT_SHA="${GITHUB_SHA::7}"

python3 - <<'PYEOF'
import os, textwrap

tag               = os.environ["TAG"]
short_sha         = os.environ["GITHUB_SHA"][:7]
full_sha          = os.environ["GITHUB_SHA"]
cuda_base_image   = os.environ["CUDA_BASE_IMAGE"]
cuda_base_digest  = os.environ["CUDA_BASE_DIGEST"]
gb10_build        = os.environ["GB10_BUILD"]
uv_version        = os.environ["UV_VERSION"]
torch_version     = os.environ["TORCH_VERSION"]
torchvision       = os.environ["TORCHVISION_VERSION"]
torchaudio        = os.environ["TORCHAUDIO_VERSION"]
triton_version    = os.environ["TRITON_VERSION"]
pytorch_index     = os.environ["PYTORCH_INDEX_URL"]
nvshmem_version   = os.environ["NVSHMEM_VERSION"]
tvm_ffi_version   = os.environ["TVM_FFI_VERSION"]
tilelang_version  = os.environ["TILELANG_VERSION"]
numba_version     = os.environ["NUMBA_VERSION"]
nccl_ref          = os.environ["NCCL_REF"]
nccl_commit       = os.environ["NCCL_COMMIT"]
vllm_ref          = os.environ["VLLM_REF"]
vllm_commit       = os.environ["VLLM_COMMIT"]
flashinfer_ref    = os.environ["FLASHINFER_REF"]
flashinfer_commit = os.environ["FLASHINFER_COMMIT"]
ray_version       = os.environ["RAY_VERSION"]
fastsafe_version  = os.environ["FASTSAFETENSORS_VERSION"]
instant_version   = os.environ["INSTANTTENSOR_VERSION"]
arch_list         = os.environ["TORCH_CUDA_ARCH_LIST"]

# e.g. "nvidia/cuda:13.2.0-devel-ubuntu24.04" -> cu13.2
cuda_ver    = cuda_base_image.split(":")[1].split("-")[0]
cuda_short  = "cu" + ".".join(cuda_ver.split(".")[:2])
torch_short = "torch" + ".".join(torch_version.split(".")[:2])

registry       = "ghcr.io/timothystewart6/vllm-gb10"
tag_canonical  = f"{registry}:{vllm_ref}-gb10.{gb10_build}"
tag_component  = f"{registry}:{vllm_ref}-{cuda_short}-{torch_short}-gb10.{gb10_build}"
tag_latest     = f"{registry}:latest"
tag_sha        = f"{registry}:sha-{short_sha}"

body = f"""## {tag}

> Reproducible vLLM image for NVIDIA DGX Spark (GB10 / sm_121a)

### Image tags

| Tag | Notes |
|---|---|
| `{tag_canonical}` | Canonical, immutable |
| `{tag_component}` | Same image - CUDA and PyTorch versions visible at a glance |
| `{tag_latest}` | Mutable - always the most recent green build |
| `{tag_sha}` | Immutable, tied to this exact commit |

```bash
docker pull {tag_canonical}
```

### Component versions

| Component | Version / Ref | Commit / Digest |
|---|---|---|
| **vLLM** | {vllm_ref} | [{vllm_commit[:12]}](https://github.com/vllm-project/vllm/commit/{vllm_commit}) |
| **FlashInfer** | {flashinfer_ref} | [{flashinfer_commit[:12]}](https://github.com/flashinfer-ai/flashinfer/commit/{flashinfer_commit}) |
| **NCCL** | {nccl_ref} | [{nccl_commit[:12]}](https://github.com/NVIDIA/nccl/commit/{nccl_commit}) |
| **PyTorch** | {torch_version} | {pytorch_index} |
| **torchvision** | {torchvision} | - |
| **torchaudio** | {torchaudio} | - |
| **Triton** | {triton_version} | - |
| **CUDA base** | [{cuda_base_image.split(":")[1]}](https://hub.docker.com/layers/nvidia/cuda/{cuda_base_image.split(":")[1]}/images/{cuda_base_digest.replace(":", "-")}) | {cuda_base_digest[:32]}... |
| **uv** | {uv_version} | - |
| **Ray** | {ray_version} | - |
| **NVSHMEM** | {nvshmem_version} | - |
| **TVM-FFI** | {tvm_ffi_version} | - |
| **TileLang** | {tilelang_version} | - |
| **Numba** | {numba_version} | - |
| **fastsafetensors** | {fastsafe_version} | - |
| **instanttensor** | {instant_version} | - |
| **Target arch** | {arch_list} | - |
| **GB10_BUILD** | {gb10_build} | - |
| **Repo SHA** | {short_sha} | [{short_sha}](https://github.com/timothystewart6/vllm-gb10/commit/{full_sha}) |
"""

print(textwrap.dedent(body))
PYEOF
