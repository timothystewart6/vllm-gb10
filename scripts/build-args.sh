#!/usr/bin/env bash
# scripts/build-args.sh
#
# Sources versions.env and emits one --build-arg NAME=VALUE for every variable
# that the Docker build accepts. Output is consumed by the caller via $():
#
#   docker buildx build $(scripts/build-args.sh) ...
#
# Some values are emitted as pass-through metadata even when no Dockerfile ARG
# consumes them directly. The versions contract test requires every validated
# versions.env key to be emitted here so new inputs cannot be silently omitted.
#
# SOURCE_DATE_EPOCH is read from the environment (set by CI or the caller),
# not from versions.env, because it is derived from `git log -1 --format=%ct`.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

python3 "${REPO_ROOT}/scripts/versions_env.py" "${REPO_ROOT}/versions.env" >/dev/null
set -a
# shellcheck disable=SC1091
source "${REPO_ROOT}/versions.env"
set +a

_arg() {
  local name="$1"
  local val="${!name}"
  printf ' --build-arg %s=%s' "${name}" "${val}"
}

# Base image
_arg CUDA_BASE_IMAGE
_arg CUDA_BASE_DIGEST

# Build revision
_arg GB10_BUILD

# uv bootstrap
_arg UV_VERSION

# PyTorch stack
_arg TORCH_VERSION
_arg TORCHVISION_VERSION
_arg TORCHAUDIO_VERSION
_arg TRITON_VERSION
_arg PYTORCH_INDEX_URL
_arg PYPI_INDEX_URL
_arg FLASHINFER_INDEX_URL

# CUDA companion packages
_arg NVSHMEM_VERSION
_arg TVM_FFI_VERSION
_arg TILELANG_VERSION
_arg NUMBA_VERSION

# NCCL
_arg NCCL_REPO
_arg NCCL_REF
_arg NCCL_COMMIT

# vLLM
_arg VLLM_REPO
_arg VLLM_REF
_arg VLLM_COMMIT

# FlashInfer
_arg FLASHINFER_REPO
_arg FLASHINFER_REF
_arg FLASHINFER_COMMIT

# Runtime lock seed versions (pass-through metadata)
_arg RAY_VERSION
_arg FASTSAFETENSORS_VERSION
_arg INSTANTTENSOR_VERSION
_arg BITSANDBYTES_VERSION
_arg ACCELERATE_VERSION
_arg QUACK_KERNELS_VERSION
_arg TRANSFORMERS_VERSION

# GPU architecture
_arg TORCH_CUDA_ARCH_LIST
_arg FLASHINFER_CUDA_ARCH_LIST

# SOURCE_DATE_EPOCH - from the environment, not versions.env
if [[ -n "${SOURCE_DATE_EPOCH:-}" ]]; then
  printf ' --build-arg SOURCE_DATE_EPOCH=%s' "${SOURCE_DATE_EPOCH}"
fi

printf '\n'
