#!/usr/bin/env bash
# scripts/bump.sh
#
# Resolves commit SHAs for every pinned git ref in versions.env, updates the
# base image digest, increments GB10_BUILD if non-vLLM inputs changed, and
# regenerates all four lockfiles from scratch (first run) or refreshes them
# against new pins (subsequent runs).
#
# PREREQUISITES
#   git, docker (with buildx), uv, python3, curl
#
# WHERE TO RUN
#   On the DGX Spark (Linux aarch64) or any Linux aarch64 machine with Docker
#   and internet access. The lockfiles contain Linux aarch64 wheel hashes and
#   MUST NOT be generated on macOS or x86 - they would contain the wrong hashes.
#
# USAGE
#   scripts/bump.sh
#
# After this script completes:
#   git diff                                     # review changes
#   git add versions.env locks/
#   git commit -m "chore(pins): bump.sh for ${VLLM_REF} (GB10_BUILD=${GB10_BUILD})"

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSIONS="${REPO_ROOT}/versions.env"
LOCKS="${REPO_ROOT}/locks"

# ---------------------------------------------------------------------------
log()  { printf '[bump.sh] %s\n' "$*" >&2; }
die()  { printf '[bump.sh] ERROR: %s\n' "$*" >&2; exit 1; }
need() { command -v "$1" &>/dev/null || die "Required tool '$1' not found - please install it."; }

need git
need docker
need uv
need python3
need curl

[[ "$(uname -m)" == "aarch64" ]] \
  || log "WARNING: not running on aarch64 - lockfile wheel hashes will be WRONG. Run on the Spark."

# ---------------------------------------------------------------------------
# 1. Load current versions.env
# ---------------------------------------------------------------------------
# shellcheck source=../versions.env
set -a; source "${VERSIONS}"; set +a

log "Loaded versions.env"
log "  VLLM_REF=${VLLM_REF}"
log "  FLASHINFER_REF=${FLASHINFER_REF}"
log "  NCCL_REF=${NCCL_REF}"

# ---------------------------------------------------------------------------
# 2. Snapshot old values for GB10_BUILD change detection
# ---------------------------------------------------------------------------
OLD_NCCL_COMMIT="${NCCL_COMMIT:-UNSET}"
OLD_VLLM_COMMIT="${VLLM_COMMIT:-UNSET}"
OLD_FLASHINFER_COMMIT="${FLASHINFER_COMMIT:-UNSET}"
OLD_CUDA_BASE_DIGEST="${CUDA_BASE_DIGEST:-UNSET}"
OLD_UV_VERSION="${UV_VERSION}"
OLD_TORCH_VERSION="${TORCH_VERSION}"
OLD_TORCHVISION_VERSION="${TORCHVISION_VERSION}"
OLD_GB10_BUILD="${GB10_BUILD:-0}"

# ---------------------------------------------------------------------------
# 3. Resolve git commit SHAs via ls-remote (no auth needed for public repos)
# ---------------------------------------------------------------------------
resolve_git_sha() {
  local url="$1" ref="$2"
  local sha
  # Try annotated tag peel first (^{} dereferences the tag to the commit it points to)
  sha=$(git ls-remote "${url}" "refs/tags/${ref}^{}" 2>/dev/null | awk '{print $1}')
  # Fall back to lightweight tag
  if [[ -z "${sha}" ]]; then
    sha=$(git ls-remote "${url}" "refs/tags/${ref}" 2>/dev/null | awk '{print $1}')
  fi
  [[ -n "${sha}" ]] || die "Cannot resolve ref '${ref}' from ${url}"
  printf '%s' "${sha}"
}

log "Resolving NCCL_COMMIT for ${NCCL_REF}..."
NCCL_COMMIT=$(resolve_git_sha "${NCCL_REPO}" "${NCCL_REF}")
log "  NCCL_COMMIT=${NCCL_COMMIT}"

log "Resolving VLLM_COMMIT for ${VLLM_REF}..."
VLLM_COMMIT=$(resolve_git_sha "${VLLM_REPO}" "${VLLM_REF}")
log "  VLLM_COMMIT=${VLLM_COMMIT}"

log "Resolving FLASHINFER_COMMIT for ${FLASHINFER_REF}..."
FLASHINFER_COMMIT=$(resolve_git_sha "${FLASHINFER_REPO}" "${FLASHINFER_REF}")
log "  FLASHINFER_COMMIT=${FLASHINFER_COMMIT}"

# ---------------------------------------------------------------------------
# 4. Resolve base image aarch64 digest via docker buildx imagetools inspect
# ---------------------------------------------------------------------------
log "Resolving aarch64 digest for ${CUDA_BASE_IMAGE}..."
CUDA_BASE_DIGEST=$(docker buildx imagetools inspect \
  "${CUDA_BASE_IMAGE}" --format '{{json .Manifest}}' 2>/dev/null \
  | python3 -c "
import json, sys
data = json.load(sys.stdin)
for m in data.get('manifests', []):
    p = m.get('platform', {})
    if p.get('os') == 'linux' and p.get('architecture') == 'arm64':
        print(m['digest'])
        break
") || die "docker buildx imagetools inspect failed. Is Docker running with buildx support?"

[[ -n "${CUDA_BASE_DIGEST}" ]] \
  || die "No arm64 entry found in the image manifest for ${CUDA_BASE_IMAGE}."
log "  CUDA_BASE_DIGEST=${CUDA_BASE_DIGEST}"

# ---------------------------------------------------------------------------
# 5. Compute GB10_BUILD
# Rule: reset to 0 when VLLM_REF changes.
#       Increment by 1 when any other pinned input changes with the same VLLM_REF.
# ---------------------------------------------------------------------------
if [[ "${VLLM_COMMIT}" != "${OLD_VLLM_COMMIT}" ]]; then
  GB10_BUILD=0
  log "VLLM_COMMIT changed -> GB10_BUILD reset to 0"
elif [[ "${NCCL_COMMIT}"         != "${OLD_NCCL_COMMIT}"         ||
        "${FLASHINFER_COMMIT}"   != "${OLD_FLASHINFER_COMMIT}"   ||
        "${CUDA_BASE_DIGEST}"    != "${OLD_CUDA_BASE_DIGEST}"    ||
        "${UV_VERSION}"          != "${OLD_UV_VERSION}"          ||
        "${TORCH_VERSION}"       != "${OLD_TORCH_VERSION}"       ||
        "${TORCHVISION_VERSION}" != "${OLD_TORCHVISION_VERSION}" ]]; then
  GB10_BUILD=$(( OLD_GB10_BUILD + 1 ))
  log "Non-vLLM input changed -> GB10_BUILD incremented to ${GB10_BUILD}"
else
  log "No pinned inputs changed - GB10_BUILD stays at ${GB10_BUILD}"
fi

# ---------------------------------------------------------------------------
# 6. Write resolved values back to versions.env
# ---------------------------------------------------------------------------
_update_env() {
  local key="$1" val="$2"
  if grep -q "^${key}=" "${VERSIONS}"; then
    # Use | as sed delimiter to safely handle values that contain /
    sed -i "s|^${key}=.*|${key}=${val}|" "${VERSIONS}"
  else
    printf '\n%s=%s\n' "${key}" "${val}" >> "${VERSIONS}"
  fi
}

_update_env CUDA_BASE_DIGEST  "${CUDA_BASE_DIGEST}"
_update_env GB10_BUILD        "${GB10_BUILD}"
_update_env NCCL_COMMIT       "${NCCL_COMMIT}"
_update_env VLLM_COMMIT       "${VLLM_COMMIT}"
_update_env FLASHINFER_COMMIT "${FLASHINFER_COMMIT}"

log "versions.env updated with resolved SHAs and digest."

# Re-source to pick up the freshly written values
set -a; source "${VERSIONS}"; set +a

# ---------------------------------------------------------------------------
# Helper: fetch a vLLM requirements file from the pinned commit.
# Strips -r (include) and -c (constraint) directives - caller inlines each
# referenced file explicitly to avoid uv pip compile path resolution issues.
# ---------------------------------------------------------------------------
_fetch_vllm_req() {
  local relpath="$1"
  local url="https://raw.githubusercontent.com/vllm-project/vllm/${VLLM_COMMIT}/${relpath}"
  local body
  if body=$(curl -fsSL --retry 3 "${url}" 2>/dev/null); then
    printf '%s\n' "${body}" | grep -vE '^\s*-[rc]\s'
  else
    log "  (${relpath} not found at ${VLLM_COMMIT} - skipping)"
  fi
}

# ---------------------------------------------------------------------------
# 7. Generate locks/python-bootstrap.txt
# Only uv itself, with hashes. Installed via plain pip before uv is available.
# ---------------------------------------------------------------------------
log "Generating locks/python-bootstrap.txt..."
printf 'uv==%s\n' "${UV_VERSION}" \
  | uv pip compile \
      --generate-hashes \
      --no-deps \
      --python-version 3.12 \
      --index-url "${PYPI_INDEX_URL}" \
      --output-file "${LOCKS}/python-bootstrap.txt" \
      -
log "  Done -> ${LOCKS}/python-bootstrap.txt"

# ---------------------------------------------------------------------------
# 8. Generate locks/python-build.txt
# Seed: PyTorch stack + vLLM build requirements + FlashInfer build deps.
# ---------------------------------------------------------------------------
log "Generating locks/python-build.txt..."
TMP_BUILD=$(mktemp /tmp/vllm-gb10-build-XXXXX.in)
# shellcheck disable=SC2064
trap "rm -f '${TMP_BUILD}'" EXIT

# PyTorch stack (exact versions from cu130 index)
cat >> "${TMP_BUILD}" <<REQS
torch==${TORCH_VERSION}
torchvision==${TORCHVISION_VERSION}
torchaudio==${TORCHAUDIO_VERSION}
triton==${TRITON_VERSION}
REQS

# vLLM build requirements at VLLM_COMMIT (try both possible paths)
_fetch_vllm_req "requirements/build.txt"      >> "${TMP_BUILD}"
_fetch_vllm_req "requirements/build/cuda.txt" >> "${TMP_BUILD}"

# FlashInfer [build-system].requires from pyproject.toml at FLASHINFER_COMMIT
python3 -c "
import urllib.request, re, sys
commit = '${FLASHINFER_COMMIT}'
url = f'https://raw.githubusercontent.com/flashinfer-ai/flashinfer/{commit}/pyproject.toml'
try:
    with urllib.request.urlopen(url, timeout=30) as r:
        content = r.read().decode()
    in_build = False
    for line in content.splitlines():
        stripped = line.strip()
        if stripped == '[build-system]':
            in_build = True
            continue
        if stripped.startswith('[') and in_build:
            break
        if in_build:
            for dep in re.findall(r'\"([^\"]+)\"', stripped):
                print(dep)
except Exception as e:
    print(f'# WARNING: could not fetch FlashInfer pyproject.toml: {e}', file=sys.stderr)
" >> "${TMP_BUILD}"

uv pip compile \
  --generate-hashes \
  --python-version 3.12 \
  --index-url "${PYPI_INDEX_URL}" \
  --extra-index-url "${PYTORCH_INDEX_URL}" \
  --output-file "${LOCKS}/python-build.txt" \
  "${TMP_BUILD}"

rm -f "${TMP_BUILD}"
trap - EXIT
log "  Done -> ${LOCKS}/python-build.txt"

# ---------------------------------------------------------------------------
# 9. Generate locks/python-runtime.txt
# Seed: vLLM runtime requirements + explicit seed versions from versions.env.
# ---------------------------------------------------------------------------
log "Generating locks/python-runtime.txt..."
TMP_RUNTIME=$(mktemp /tmp/vllm-gb10-runtime-XXXXX.in)
# shellcheck disable=SC2064
trap "rm -f '${TMP_RUNTIME}'" EXIT

# vLLM runtime requirements at VLLM_COMMIT
_fetch_vllm_req "requirements/common.txt" >> "${TMP_RUNTIME}"
_fetch_vllm_req "requirements/cuda.txt"   >> "${TMP_RUNTIME}"

# Explicit seed versions from versions.env.
# These supplement or override what vLLM's requirements declare.
cat >> "${TMP_RUNTIME}" <<REQS
ray[default]==${RAY_VERSION}
fastsafetensors>=${FASTSAFETENSORS_VERSION}
instanttensor==${INSTANTTENSOR_VERSION}
nvidia-nvshmem-cu13==${NVSHMEM_VERSION}
apache-tvm-ffi==${TVM_FFI_VERSION}
tilelang==${TILELANG_VERSION}
numba==${NUMBA_VERSION}
REQS

uv pip compile \
  --generate-hashes \
  --python-version 3.12 \
  --index-url "${PYPI_INDEX_URL}" \
  --extra-index-url "${PYTORCH_INDEX_URL}" \
  --output-file "${LOCKS}/python-runtime.txt" \
  "${TMP_RUNTIME}"

rm -f "${TMP_RUNTIME}"
trap - EXIT
log "  Done -> ${LOCKS}/python-runtime.txt"

# ---------------------------------------------------------------------------
# 10. Generate locks/apt-packages.txt (versioned entries)
# Resolves exact apt package versions by running apt-cache madison inside a
# container using the pinned CUDA base image and the pinned apt snapshot.
# Requires Docker with linux/arm64 support (native on Spark; emulated on x86).
# ---------------------------------------------------------------------------
log "Generating locks/apt-packages.txt versioned entries..."

PKG_NAMES=$(grep -vE '^\s*(#|$)' "${LOCKS}/apt-packages.txt" \
  | sed 's/=.*//' \
  | tr '\n' ' ')

if [[ -z "$(printf '%s' "${PKG_NAMES}" | tr -d '[:space:]')" ]]; then
  log "  WARNING: apt-packages.txt has no package names - skipping apt resolution."
else
  APT_VERSIONED=$(docker run --rm --platform linux/arm64 \
    "${CUDA_BASE_IMAGE}@${CUDA_BASE_DIGEST}" \
    bash -c "
      rm -f /etc/apt/sources.list.d/*.list /etc/apt/sources.list.d/*.sources 2>/dev/null
      cat > /etc/apt/sources.list <<'SOURCES'
$(cat "${LOCKS}/apt-sources.list" | grep -v '^#' | grep -v '^$')
SOURCES
      apt-get update -qq 2>/dev/null
      apt-cache madison ${PKG_NAMES} 2>/dev/null
    " 2>/dev/null \
  | python3 -c "
import sys
seen = {}
for line in sys.stdin:
    parts = [p.strip() for p in line.split('|')]
    if len(parts) >= 2:
        pkg, ver = parts[0].strip(), parts[1].strip()
        if pkg and ver and pkg not in seen:
            seen[pkg] = ver
for pkg, ver in sorted(seen.items()):
    print(f'{pkg}={ver}')
" 2>/dev/null) || true

  if [[ -n "${APT_VERSIONED}" ]]; then
    {
      grep -E '^\s*(#|$)' "${LOCKS}/apt-packages.txt" || true
      printf '%s\n' "${APT_VERSIONED}"
    } > "${LOCKS}/apt-packages.txt.new"
    mv "${LOCKS}/apt-packages.txt.new" "${LOCKS}/apt-packages.txt"
    log "  Done -> ${LOCKS}/apt-packages.txt"
  else
    log "  WARNING: apt-cache madison returned no output."
    log "  apt-packages.txt was NOT updated. Check Docker platform support for linux/arm64."
  fi
fi

# ---------------------------------------------------------------------------
log ""
log "bump.sh complete."
log ""
log "Suggested next steps:"
log "  git -C '${REPO_ROOT}' diff --stat"
log "  git -C '${REPO_ROOT}' add versions.env locks/"
log "  git -C '${REPO_ROOT}' commit -m 'chore(pins): bump.sh for ${VLLM_REF} (GB10_BUILD=${GB10_BUILD})'"
