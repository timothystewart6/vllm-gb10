#!/usr/bin/env bash
# scripts/check-updates.sh
#
# Compares the versions pinned in versions.env against the latest available
# upstream release for each component and prints a summary.
#
# Checks:
#   uv           - GitHub releases (astral-sh/uv)
#   vLLM         - GitHub releases (vllm-project/vllm)
#   NCCL         - GitHub releases (NVIDIA/nccl)
#   FlashInfer   - GitHub releases (flashinfer-ai/flashinfer)
#   CUDA base    - Docker Hub manifest for nvidia/cuda tag on linux/arm64
#   PyTorch      - PyPI (torch)
#   TorchVision  - PyPI (torchvision)
#   TorchAudio   - PyPI (torchaudio)
#   Triton       - PyPI (triton)
#   NVSHMEM      - PyPI (nvidia-nvshmem-cu13)
#   TVM FFI      - PyPI (tvm-ffi)
#   TileLang     - PyPI (tilelang)
#   Numba        - PyPI (numba)
#
# PREREQUISITES
#   curl, python3
#
# USAGE
#   scripts/check-updates.sh            # check only, never writes anything
#   scripts/check-updates.sh --update   # write updated _REF/_VERSION lines to
#                                       # versions.env, then open a PR to let
#                                       # bump.sh resolve _COMMIT SHAs
#
# --update does NOT touch _COMMIT fields. Those are resolved by bump.sh running
# on the Spark. The intended flow after --update is:
#   git checkout -b deps/bump-$(date +%Y-%m-%d)
#   scripts/check-updates.sh --update
#   git add versions.env
#   git commit -m "chore(deps): bump versions"
#   git push && gh pr create
#
# WARNING: PyTorch/Triton/TorchVision/TorchAudio versions must stay in sync
# with what vLLM requires. When VLLM_REF changes, check
# requirements/build/cuda.txt in the vLLM repo before bumping those.
# --update will write the PyPI latest but print a warning.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSIONS="${REPO_ROOT}/versions.env"

DO_UPDATE=0
for arg in "$@"; do
  case "${arg}" in
    --update) DO_UPDATE=1 ;;
    *) printf 'Unknown argument: %s\n' "${arg}" >&2; exit 1 ;;
  esac
done

log()  { printf '[check-updates] %s\n' "$*" >&2; }
need() { command -v "$1" &>/dev/null || { log "Required tool '$1' not found."; exit 1; }; }

need curl
need python3

# shellcheck source=../versions.env
set -a; source "${VERSIONS}"; set +a

OK="OK     "
OUT="UPDATE "

# Tracks whether any updates were found
UPDATES=0

# ---------------------------------------------------------------------------
# sed -i is not portable between macOS and Linux. Use a temp-file swap.
# ---------------------------------------------------------------------------
update_env() {
  local key="$1"
  local val="$2"
  local tmp
  tmp="$(mktemp)"
  sed "s|^${key}=.*|${key}=${val}|" "${VERSIONS}" > "${tmp}"
  mv "${tmp}" "${VERSIONS}"
  log "  updated ${key}=${val}"
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

gh_latest_tag() {
  # Returns the tag_name of the latest GitHub release for owner/repo.
  # Skips pre-releases and drafts (uses /releases/latest endpoint).
  local repo="$1"
  curl -fsSL "https://api.github.com/repos/${repo}/releases/latest" \
    | python3 -c "import json,sys; print(json.load(sys.stdin)['tag_name'])"
}

pypi_latest() {
  # Returns the latest stable version from PyPI for a package.
  local pkg="$1"
  curl -fsSL "https://pypi.org/pypi/${pkg}/json" \
    | python3 -c "import json,sys; print(json.load(sys.stdin)['info']['version'])"
}

report() {
  local label="$1"
  local key="$2"
  local current="$3"
  local latest="$4"
  if [ "${current}" = "${latest}" ]; then
    printf '%s %-30s current=%-20s\n' "${OK}" "${label}" "${current}"
  else
    printf '%s %-30s current=%-20s latest=%s\n' "${OUT}" "${label}" "${current}" "${latest}"
    UPDATES=$((UPDATES + 1))
    if [ "${DO_UPDATE}" -eq 1 ]; then
      update_env "${key}" "${latest}"
    fi
  fi
}

# ---------------------------------------------------------------------------
# GitHub-tagged components
# ---------------------------------------------------------------------------

log "Checking GitHub releases..."

VLLM_LATEST=$(gh_latest_tag "vllm-project/vllm")
report "vLLM (VLLM_REF)" "VLLM_REF" "${VLLM_REF}" "${VLLM_LATEST}"

NCCL_LATEST=$(gh_latest_tag "NVIDIA/nccl")
report "NCCL (NCCL_REF)" "NCCL_REF" "${NCCL_REF}" "${NCCL_LATEST}"

FLASHINFER_LATEST=$(gh_latest_tag "flashinfer-ai/flashinfer")
report "FlashInfer (FLASHINFER_REF)" "FLASHINFER_REF" "${FLASHINFER_REF}" "${FLASHINFER_LATEST}"

UV_LATEST=$(gh_latest_tag "astral-sh/uv")
report "uv (UV_VERSION)" "UV_VERSION" "${UV_VERSION}" "${UV_LATEST}"

# ---------------------------------------------------------------------------
# PyPI components
# ---------------------------------------------------------------------------

log "Checking PyPI..."

TORCH_LATEST=$(pypi_latest "torch")
report "PyTorch (TORCH_VERSION)" "TORCH_VERSION" "${TORCH_VERSION}" "${TORCH_LATEST}"

TORCHVISION_LATEST=$(pypi_latest "torchvision")
report "TorchVision (TORCHVISION_VERSION)" "TORCHVISION_VERSION" "${TORCHVISION_VERSION}" "${TORCHVISION_LATEST}"

TORCHAUDIO_LATEST=$(pypi_latest "torchaudio")
report "TorchAudio (TORCHAUDIO_VERSION)" "TORCHAUDIO_VERSION" "${TORCHAUDIO_VERSION}" "${TORCHAUDIO_LATEST}"

TRITON_LATEST=$(pypi_latest "triton")
report "Triton (TRITON_VERSION)" "TRITON_VERSION" "${TRITON_VERSION}" "${TRITON_LATEST}"

NVSHMEM_LATEST=$(pypi_latest "nvidia-nvshmem-cu13")
report "NVSHMEM (NVSHMEM_VERSION)" "NVSHMEM_VERSION" "${NVSHMEM_VERSION}" "${NVSHMEM_LATEST}"

TVM_FFI_LATEST=$(pypi_latest "tvm-ffi")
report "TVM FFI (TVM_FFI_VERSION)" "TVM_FFI_VERSION" "${TVM_FFI_VERSION}" "${TVM_FFI_LATEST}"

TILELANG_LATEST=$(pypi_latest "tilelang")
report "TileLang (TILELANG_VERSION)" "TILELANG_VERSION" "${TILELANG_VERSION}" "${TILELANG_LATEST}"

NUMBA_LATEST=$(pypi_latest "numba")
report "Numba (NUMBA_VERSION)" "NUMBA_VERSION" "${NUMBA_VERSION}" "${NUMBA_LATEST}"

# ---------------------------------------------------------------------------
# CUDA base image - check if the pinned digest is still current for this tag
# ---------------------------------------------------------------------------

log "Checking CUDA base image digest..."

CUDA_TAG="${CUDA_BASE_IMAGE#*:}"    # e.g. 13.2.0-devel-ubuntu24.04
CUDA_REPO="${CUDA_BASE_IMAGE%%:*}"  # e.g. nvidia/cuda

# Fetch the current arm64 digest for this tag from Docker Hub
CUDA_CURRENT_DIGEST=$(
  curl -fsSL \
    "https://hub.docker.com/v2/repositories/${CUDA_REPO}/tags/${CUDA_TAG}" \
    | python3 -c "
import json, sys
data = json.load(sys.stdin)
images = data.get('images', [])
arm = next((i for i in images if i.get('architecture') == 'arm64'), None)
print(arm['digest'] if arm else 'NOT_FOUND')
"
)

if [ "${CUDA_CURRENT_DIGEST}" = "${CUDA_BASE_DIGEST}" ]; then
  printf '%s %-30s digest unchanged\n' "${OK}" "CUDA base (${CUDA_TAG})"
elif [ "${CUDA_CURRENT_DIGEST}" = "NOT_FOUND" ]; then
  printf '%s %-30s could not fetch arm64 digest\n' "WARN   " "CUDA base (${CUDA_TAG})"
else
  printf '%s %-30s digest changed\n' "${OUT}" "CUDA base (${CUDA_TAG})"
  printf '         current:  %s\n' "${CUDA_BASE_DIGEST}"
  printf '         upstream: %s\n' "${CUDA_CURRENT_DIGEST}"
  UPDATES=$((UPDATES + 1))
  if [ "${DO_UPDATE}" -eq 1 ]; then
    update_env "CUDA_BASE_DIGEST" "${CUDA_CURRENT_DIGEST}"
  fi
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

echo ""
if [ "${UPDATES}" -eq 0 ]; then
  echo "All components are current."
else
  if [ "${DO_UPDATE}" -eq 1 ]; then
    printf '%d component(s) updated in versions.env.\n' "${UPDATES}"
    echo ""
    echo "Next steps:"
    echo "  1. git diff versions.env          # review changes"
    echo "  2. git checkout -b deps/bump-\$(date +%Y-%m-%d)"
    echo "  3. git add versions.env && git commit -m 'chore(deps): bump versions'"
    echo "  4. git push && gh pr create"
    echo "  CI will run bump.sh on the Spark to resolve _COMMIT SHAs and lockfiles."
    echo ""
    echo "WARNING: if VLLM_REF changed, verify PyTorch/Triton/TorchVision/TorchAudio"
    echo "against requirements/build/cuda.txt in the vLLM repo at the new tag before merging."
  else
    printf '%d component(s) have updates available.\n' "${UPDATES}"
    echo "Run with --update to write changes to versions.env."
    echo ""
    echo "Note: PyTorch/Triton/TorchVision/TorchAudio must stay in sync with what vLLM"
    echo "requires at VLLM_REF. Check requirements/build/cuda.txt in the vLLM repo before"
    echo "bumping those independently."
  fi
fi
