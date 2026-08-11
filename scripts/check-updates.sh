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
#   apt snapshot - age of the Ubuntu snapshot in locks/apt-sources.list
#
# PREREQUISITES
#   curl, python3
#
# USAGE
#   scripts/check-updates.sh                  # check only, never writes anything
#   scripts/check-updates.sh --update         # write updated _REF/_VERSION lines to
#                                             # versions.env, then open a PR to let
#                                             # bump.sh resolve _COMMIT SHAs
#   scripts/check-updates.sh --bump-apt-snapshot
#                                             # advance locks/apt-sources.list to
#                                             # today's date; then open a dedicated
#                                             # PR so bump.sh re-resolves apt-packages.txt
#
# --update does NOT touch _COMMIT fields. Those are resolved by bump.sh running
# on the Spark. The intended flow after --update is:
#   git checkout -b deps/bump-$(date +%Y-%m-%d)
#   scripts/check-updates.sh --update
#   git add versions.env
#   git commit -m "chore(deps): bump versions"
#   git push && gh pr create
#
# --bump-apt-snapshot is intentionally NOT wired into CI. Advancing the apt
# snapshot busts the apt-base Docker layer cache and triggers a full rebuild
# (NCCL + FlashInfer + vLLM from source). This should be a deliberate,
# infrequent decision - typically driven by security updates in noble-security.
# The intended flow after --bump-apt-snapshot is:
#   git checkout -b chore/apt-snapshot-$(date +%Y-%m-%d)
#   scripts/check-updates.sh --bump-apt-snapshot
#   git add locks/apt-sources.list
#   git commit -m "chore(apt): advance Ubuntu snapshot to $(date +%Y-%m-%d)"
#   git push && gh pr create
#   CI will run bump.sh on the Spark to regenerate locks/apt-packages.txt.
#
# WARNING: PyTorch/Triton/TorchVision/TorchAudio versions must stay in sync
# with what vLLM requires. When VLLM_REF changes, check
# requirements/build/cuda.txt in the vLLM repo before bumping those.
# --update will write the PyPI latest but print a warning.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSIONS="${REPO_ROOT}/versions.env"
VERSIONS_TARGET="${VERSIONS}"
VERSIONS_CANDIDATE=""

DO_UPDATE=0
DO_BUMP_APT=0
for arg in "$@"; do
  case "${arg}" in
    --update)            DO_UPDATE=1 ;;
    --bump-apt-snapshot) DO_BUMP_APT=1 ;;
    *) printf 'Unknown argument: %s\n' "${arg}" >&2; exit 1 ;;
  esac
done

if [[ "${DO_UPDATE}" -eq 1 && "${DO_BUMP_APT}" -eq 1 ]]; then
  printf '%s\n' 'Choose either --update or --bump-apt-snapshot, not both.' >&2
  exit 1
fi

cleanup() {
  if [[ -n "${VERSIONS_CANDIDATE}" ]]; then
    rm -f "${VERSIONS_CANDIDATE}"
  fi
}
trap cleanup EXIT

if [[ "${DO_UPDATE}" -eq 1 ]]; then
  VERSIONS_CANDIDATE="$(mktemp)"
  cp "${VERSIONS_TARGET}" "${VERSIONS_CANDIDATE}"
  VERSIONS="${VERSIONS_CANDIDATE}"
fi

log()  { printf '[check-updates] %s\n' "$*" >&2; }
need() { command -v "$1" &>/dev/null || { log "Required tool '$1' not found."; exit 1; }; }

need curl
need python3

set -a
# shellcheck disable=SC1090,SC1091
source "${VERSIONS}"
set +a

OK="OK     "
OUT="UPDATE "

# Tracks whether any updates were found
UPDATES=0
ENV_UPDATES=0

# ---------------------------------------------------------------------------
# Pass upstream values as data to the shared atomic schema-validating writer.
# ---------------------------------------------------------------------------
update_env() {
  local key="$1"
  local val="$2"
  if ! grep -q "^${key}=" "${VERSIONS}"; then
    log "Cannot update missing key ${key} in versions.env."
    return 1
  fi
  if ! python3 "${REPO_ROOT}/scripts/update-versions-env.py" \
      "${VERSIONS}" "${key}=${val}"; then
    log "Rejected unsafe ${key} candidate from upstream."
    return 1
  fi
  ENV_UPDATES=$((ENV_UPDATES + 1))
  log "  updated ${key}=${val}"
}

# ---------------------------------------------------------------------------
# Advance the snapshot timestamp in locks/apt-sources.list to today's date.
# Updates both the human-readable comment and the three deb URLs.
# ---------------------------------------------------------------------------
update_apt_snapshot() {
  local new_stamp="$1"   # e.g. 20260626T000000Z
  local new_display
  new_display="${new_stamp:0:4}-${new_stamp:4:2}-${new_stamp:6:2}"
  local sources="${REPO_ROOT}/locks/apt-sources.list"
  local tmp
  tmp="$(mktemp)"
  sed \
    -e "s|/[0-9]\{8\}T[0-9]\{6\}Z/|/${new_stamp}/|g" \
    -e "s|# Snapshot date: .*|# Snapshot date: ${new_display}T00:00:00Z|" \
    "${sources}" > "${tmp}"
  chmod 0644 "${tmp}"
  mv "${tmp}" "${sources}"
  log "  updated apt-sources.list snapshot -> ${new_display}T00:00:00Z"
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

torch_triton_pin() {
  # Returns the exact Triton dependency for Torch's Python 3.12 arm64 wheel.
  local torch_version="$1"
  local torch_index="${PYTORCH_INDEX_URL%/}/torch/"
  local torch_variant="${PYTORCH_INDEX_URL%/}"
  torch_variant="${torch_variant##*/}"
  local metadata_url
  metadata_url=$(curl -fsSL "${torch_index}" \
  | python3 -c '
import html.parser
import sys
import urllib.parse

base_url, version, variant = sys.argv[1:]
wheel_fragment = f"torch-{version}%2B{variant}-cp312-cp312-"

class WheelIndexParser(html.parser.HTMLParser):
  def handle_starttag(self, tag, attrs):
    attributes = dict(attrs)
    href = attributes.get("href", "")
    if (
      tag == "a"
      and wheel_fragment in href
      and "aarch64.whl" in href
      and (
        "data-core-metadata" in attributes
        or "data-dist-info-metadata" in attributes
      )
    ):
      print(urllib.parse.urljoin(base_url, href.split("#", 1)[0]) + ".metadata")

parser = WheelIndexParser()
parser.feed(sys.stdin.read())
' "${torch_index}" "${torch_version}" "${torch_variant}")
  if [[ -z "${metadata_url}" ]] || [[ "$(printf '%s\n' "${metadata_url}" | wc -l)" -ne 1 ]]; then
  log "Could not identify one Python 3.12 arm64 Torch wheel for ${torch_version}."
  return 1
  fi
  curl -fsSL "${metadata_url}" \
    | python3 -c '
import re
import sys

environment = {
  "platform_machine": "aarch64",
  "platform_system": "Linux",
  "python_full_version": "3.12.0",
  "python_version": "3.12",
  "sys_platform": "linux",
}

def comparable(variable, value):
  if variable.startswith("python_"):
    return tuple(int(part) for part in value.split("."))
  return value.lower()

def marker_applies(marker):
  if not marker:
    return True
  for clause in re.split(r"\s+and\s+", marker, flags=re.IGNORECASE):
    match = re.fullmatch(
      r"\s*([a-z_]+)\s*(==|!=|<=|>=|<|>)\s*([\x22\x27])(.*?)\3\s*",
      clause,
      re.IGNORECASE,
    )
    if not match or match.group(1) not in environment:
      return False
    variable, operator, _, expected = match.groups()
    actual = comparable(variable, environment[variable])
    expected = comparable(variable, expected)
    comparisons = {
      "==": actual == expected,
      "!=": actual != expected,
      "<": actual < expected,
      "<=": actual <= expected,
      ">": actual > expected,
      ">=": actual >= expected,
    }
    if not comparisons[operator]:
      return False
  return True

pins = set()
for line in sys.stdin:
  match = re.match(
    r"^Requires-Dist:\s*triton\s*==\s*([^\s;]+)(?:\s*;\s*(.*))?$",
    line.strip(),
    re.IGNORECASE,
  )
  if match and marker_applies(match.group(2)):
    pins.add(match.group(1))

if len(pins) != 1:
  raise SystemExit(
    "arm64 Torch metadata must have one applicable exact Triton dependency"
  )
print(pins.pop())
'
}

# ---------------------------------------------------------------------------
# vLLM pins lookup
# Many of the runtime/build deps must exactly match what vLLM declares at the
# pinned tag (torch, torchvision, torchaudio, apache-tvm-ffi, tilelang, numba,
# flashinfer-python). Querying PyPI latest for these produces conflicts when
# vLLM has not caught up. We fetch vLLM's requirements files once and prefer
# those pins over PyPI 'latest'.
# ---------------------------------------------------------------------------
VLLM_REQS_RAW=""
load_vllm_reqs() {
  local tag="$1"
  local base="https://raw.githubusercontent.com/vllm-project/vllm/refs/tags/${tag}"
  VLLM_REQS_RAW="$(
    {
      curl -fsSL "${base}/requirements/cuda.txt"       2>/dev/null || true
      printf '\n'
      curl -fsSL "${base}/requirements/build/cuda.txt" 2>/dev/null || true
    }
  )"
  if [[ -z "${VLLM_REQS_RAW//[[:space:]]/}" ]]; then
    log "Could not fetch vLLM requirements for ${tag}."
    return 1
  fi
}

vllm_pin() {
  # Print the version pinned by vLLM for ${pkg} (first '==' line), or nothing.
  local pkg="$1"
  printf '%s\n' "${VLLM_REQS_RAW}" \
    | python3 -c "
import re, sys
pkg = sys.argv[1]
pat = re.compile(r'^\s*' + re.escape(pkg) + r'\s*==\s*([^\s#;]+)')
for line in sys.stdin:
    m = pat.match(line)
    if m:
        print(m.group(1))
        break
" "${pkg}"
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
# Use semver comparison so that a pinned pre-release (e.g. v0.23.1rc0) that is
# newer than the latest stable (e.g. v0.23.0) is reported as INFO, not UPDATE.
# Only flag UPDATE when the stable release is genuinely ahead of our pin.
VLLM_CMP=$(MONITOR_CURRENT_VLLM="${VLLM_REF}" \
  MONITOR_LATEST_VLLM="${VLLM_LATEST}" \
  python3 -c "
import os
import re

def parse(value):
    match = re.fullmatch(r'v?(\d+)\.(\d+)\.(\d+)(?:(a|b|rc)(\d+))?', value)
    if not match:
        raise ValueError(value)
    major, minor, patch, stage, number = match.groups()
    stage_rank = {'a': 0, 'b': 1, 'rc': 2, None: 3}[stage]
    return (int(major), int(minor), int(patch), stage_rank, int(number or 0))

cur = parse(os.environ['MONITOR_CURRENT_VLLM'])
lat = parse(os.environ['MONITOR_LATEST_VLLM'])
print('ahead' if cur >= lat else 'behind')
" 2>/dev/null || echo 'unknown')
if [ "${VLLM_CMP}" = "behind" ]; then
  VLLM_TARGET="${VLLM_LATEST}"
  report "vLLM (VLLM_REF)" "VLLM_REF" "${VLLM_REF}" "${VLLM_LATEST}"
elif [ "${VLLM_CMP}" = "ahead" ] && [ "${VLLM_REF}" != "${VLLM_LATEST}" ]; then
  VLLM_TARGET="${VLLM_REF}"
  printf 'INFO    %-30s current=%-20s stable=%s (pinned to newer pre-release)\n' \
    "vLLM (VLLM_REF)" "${VLLM_REF}" "${VLLM_LATEST}"
elif [ "${VLLM_CMP}" = "ahead" ]; then
  VLLM_TARGET="${VLLM_REF}"
  printf '%s %-30s current=%-20s\n' "${OK}" "vLLM (VLLM_REF)" "${VLLM_REF}"
else
  log "Could not compare vLLM versions '${VLLM_REF}' and '${VLLM_LATEST}'."
  exit 1
fi

# Load vLLM's pinned dep versions at the *target* tag so dependent components
# below can be aligned to it (rather than blindly tracking PyPI latest).
log "Fetching vLLM ${VLLM_TARGET} requirements for cross-checks..."
load_vllm_reqs "${VLLM_TARGET}"

NCCL_LATEST=$(gh_latest_tag "NVIDIA/nccl")
report "NCCL (NCCL_REF)" "NCCL_REF" "${NCCL_REF}" "${NCCL_LATEST}"

# FlashInfer is constrained by vLLM's flashinfer-python pin at VLLM_TARGET, which
# is the authoritative source of truth for this stack.  If our FLASHINFER_REF
# already matches that constraint we are aligned -- do NOT flag a GitHub
# "latest" as an update (upstream FlashInfer releases that are newer than what
# the target vLLM expects would cause pip conflicts or ABI mismatches).
# Fall back to GH latest only when vLLM does not pin flashinfer-python at
# VLLM_TARGET.
FLASHINFER_PIN=$(vllm_pin "flashinfer-python")
if [[ -n "${FLASHINFER_PIN}" ]]; then
  FLASHINFER_LATEST="v${FLASHINFER_PIN}"
  if [[ "${FLASHINFER_REF#v}" == "${FLASHINFER_PIN}" ]]; then
    printf '%s %-30s current=%-20s (aligned to VLLM %s pin)\n' \
      "${OK}" "FlashInfer (FLASHINFER_REF)" "${FLASHINFER_REF}" "${VLLM_TARGET}"
  else
    printf '%s %-30s current=%-20s vLLM=%s (mismatch!)\n' \
      "${OUT}" "FlashInfer (FLASHINFER_REF)" "${FLASHINFER_REF}" "v${FLASHINFER_PIN}"
    UPDATES=$((UPDATES + 1))
    if [[ "${DO_UPDATE}" -eq 1 ]]; then
      update_env "FLASHINFER_REF" "v${FLASHINFER_PIN}"
    fi
  fi
else
  FLASHINFER_LATEST=$(gh_latest_tag "flashinfer-ai/flashinfer")
  report "FlashInfer (FLASHINFER_REF)" "FLASHINFER_REF" "${FLASHINFER_REF}" "${FLASHINFER_LATEST}"
fi

UV_LATEST=$(gh_latest_tag "astral-sh/uv")
report "uv (UV_VERSION)" "UV_VERSION" "${UV_VERSION}" "${UV_LATEST}"

# ---------------------------------------------------------------------------
# PyPI components
# ---------------------------------------------------------------------------

log "Checking PyPI..."

# vllm_or_pypi: prefer vLLM's pin; fall back to PyPI 'latest' if vLLM has none.
# Components vLLM pins explicitly (torch/torchvision/torchaudio/apache-tvm-ffi/
# tilelang/numba) CANNOT be bumped independently or pip will fail to resolve.
vllm_or_pypi() {
  local pkg="$1"
  local v
  v=$(vllm_pin "${pkg}")
  if [[ -n "${v}" ]]; then printf '%s' "${v}"; else pypi_latest "${pkg}"; fi
}

TORCH_LATEST=$(vllm_or_pypi "torch")
report "PyTorch (TORCH_VERSION)" "TORCH_VERSION" "${TORCH_VERSION}" "${TORCH_LATEST}"

TORCHVISION_LATEST=$(vllm_or_pypi "torchvision")
report "TorchVision (TORCHVISION_VERSION)" "TORCHVISION_VERSION" "${TORCHVISION_VERSION}" "${TORCHVISION_LATEST}"

TORCHAUDIO_LATEST=$(vllm_or_pypi "torchaudio")
report "TorchAudio (TORCHAUDIO_VERSION)" "TORCHAUDIO_VERSION" "${TORCHAUDIO_VERSION}" "${TORCHAUDIO_LATEST}"

# Triton is transitively pinned by Torch. Derive it from the selected Torch
# release instead of tracking its independent PyPI latest version.
TRITON_TARGET=$(torch_triton_pin "${TORCH_LATEST}")
if [ "${TRITON_VERSION}" != "${TRITON_TARGET}" ]; then
  printf '%s %-30s current=%-20s torch=%s\n' \
    "${OUT}" "Triton (TRITON_VERSION)" "${TRITON_VERSION}" "${TRITON_TARGET}"
  UPDATES=$((UPDATES + 1))
  if [ "${DO_UPDATE}" -eq 1 ]; then
    if [ "${TORCH_VERSION}" = "${TORCH_LATEST}" ]; then
      log "Refusing to update Triton without a Torch update."
      exit 1
    fi
    update_env "TRITON_VERSION" "${TRITON_TARGET}"
  fi
else
  printf '%s %-30s current=%-20s\n' "${OK}" "Triton (TRITON_VERSION)" "${TRITON_VERSION}"
fi

# NVSHMEM is transitively pinned by torch's cu130 wheel
# (torch==2.11.0+cu130 depends on nvidia-nvshmem-cu13==3.4.5). Report newer
# upstream versions but never auto-bump - the resolver will reject them.
NVSHMEM_LATEST_PYPI=$(pypi_latest "nvidia-nvshmem-cu13")
if [ "${NVSHMEM_VERSION}" != "${NVSHMEM_LATEST_PYPI}" ]; then
  printf '%s %-30s current=%-20s pypi=%s (locked by torch - not auto-bumped)\n' \
    "INFO   " "NVSHMEM (NVSHMEM_VERSION)" "${NVSHMEM_VERSION}" "${NVSHMEM_LATEST_PYPI}"
else
  printf '%s %-30s current=%-20s\n' "${OK}" "NVSHMEM (NVSHMEM_VERSION)" "${NVSHMEM_VERSION}"
fi

# PyPI package is 'apache-tvm-ffi' (not 'tvm-ffi'). vLLM pins it explicitly.
TVM_FFI_LATEST=$(vllm_or_pypi "apache-tvm-ffi")
report "TVM FFI (TVM_FFI_VERSION)" "TVM_FFI_VERSION" "${TVM_FFI_VERSION}" "${TVM_FFI_LATEST}"

TILELANG_LATEST=$(vllm_or_pypi "tilelang")
report "TileLang (TILELANG_VERSION)" "TILELANG_VERSION" "${TILELANG_VERSION}" "${TILELANG_LATEST}"

NUMBA_LATEST=$(vllm_or_pypi "numba")
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
# apt snapshot age - locks/apt-sources.list
# Flag stale snapshots so noble-security patches don't go unnoticed.
# Threshold: INFO >= 7 days, UPDATE >= 60 days.
# Use --bump-apt-snapshot (locally, never in CI) to advance the date.
# ---------------------------------------------------------------------------

APT_SOURCES="${REPO_ROOT}/locks/apt-sources.list"
APT_SNAPSHOT_STAMP=$(grep -m1 '^deb .*snapshot.ubuntu.com' "${APT_SOURCES}" \
  | python3 -c "import re,sys; m=re.search(r'/(\d{8}T\d{6}Z)/', sys.stdin.read()); print(m.group(1) if m else '')" 2>/dev/null || true)

if [[ -z "${APT_SNAPSHOT_STAMP}" ]]; then
  printf 'WARN    %-30s could not parse snapshot date\n' "apt snapshot"
else
  APT_SNAPSHOT_AGE=$(python3 -c '
import sys
from datetime import datetime, timezone
snap = datetime.strptime(sys.argv[1], "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
print((datetime.now(timezone.utc) - snap).days)
' "${APT_SNAPSHOT_STAMP}")
  APT_SNAPSHOT_DISPLAY="${APT_SNAPSHOT_STAMP:0:4}-${APT_SNAPSHOT_STAMP:4:2}-${APT_SNAPSHOT_STAMP:6:2}"
  TODAY_STAMP=$(date -u +"%Y%m%dT000000Z")

  if [[ "${APT_SNAPSHOT_AGE}" -ge 60 ]]; then
    printf '%s %-30s age=%d days (snapshot=%s) - security updates may be missing\n' \
      "${OUT}" "apt snapshot" "${APT_SNAPSHOT_AGE}" "${APT_SNAPSHOT_DISPLAY}"
    UPDATES=$((UPDATES + 1))
    if [[ "${DO_BUMP_APT}" -eq 1 ]]; then
      update_apt_snapshot "${TODAY_STAMP}"
    fi
  elif [[ "${APT_SNAPSHOT_AGE}" -ge 7 ]]; then
    printf 'INFO    %-30s age=%d days (snapshot=%s) - consider refreshing soon\n' \
      "apt snapshot" "${APT_SNAPSHOT_AGE}" "${APT_SNAPSHOT_DISPLAY}"
    if [[ "${DO_BUMP_APT}" -eq 1 ]]; then
      update_apt_snapshot "${TODAY_STAMP}"
    fi
  else
    printf '%s %-30s age=%d days (snapshot=%s)\n' \
      "${OK}" "apt snapshot" "${APT_SNAPSHOT_AGE}" "${APT_SNAPSHOT_DISPLAY}"
  fi
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

if [[ "${DO_UPDATE}" -eq 1 ]]; then
  if [[ "${ENV_UPDATES}" -gt 0 ]]; then
    chmod 0644 "${VERSIONS_CANDIDATE}"
    mv "${VERSIONS_CANDIDATE}" "${VERSIONS_TARGET}"
  else
    rm -f "${VERSIONS_CANDIDATE}"
  fi
  VERSIONS_CANDIDATE=""
  VERSIONS="${VERSIONS_TARGET}"
fi

echo ""
if [ "${UPDATES}" -eq 0 ]; then
  echo "All components are current."
else
  if [ "${DO_UPDATE}" -eq 1 ]; then
    printf '%d component(s) updated in versions.env.\n' "${ENV_UPDATES}"
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
  elif [ "${DO_BUMP_APT}" -eq 1 ]; then
    echo "locks/apt-sources.list snapshot advanced to today."
    echo ""
    echo "Next steps:"
    echo "  1. git diff locks/apt-sources.list"
    echo "  2. git checkout -b chore/apt-snapshot-\$(date +%Y-%m-%d)"
    echo "  3. git add locks/apt-sources.list"
    echo "  4. git commit -m 'chore(apt): advance Ubuntu snapshot to \$(date +%Y-%m-%d)'"
    echo "  5. git push && gh pr create"
    echo "  CI will run bump.sh on the Spark to regenerate locks/apt-packages.txt."
    echo ""
    echo "NOTE: this PR will bust the apt-base Docker layer cache and trigger a full"
    echo "rebuild (NCCL + FlashInfer + vLLM from source). That is expected and intentional."
  else
    printf '%d component(s) have updates available.\n' "${UPDATES}"
    echo "Run with --update to write changes to versions.env."
    if [[ "${APT_SNAPSHOT_AGE:-0}" -ge 7 ]]; then
      echo "Run with --bump-apt-snapshot to advance locks/apt-sources.list to today."
      echo "  NOTE: this busts the apt-base Docker layer cache (full rebuild). Open a"
      echo "  dedicated PR so the cache bust is intentional and isolated."
    fi
    echo ""
    echo "Note: PyTorch/Triton/TorchVision/TorchAudio must stay in sync with what vLLM"
    echo "requires at VLLM_REF. Check requirements/build/cuda.txt in the vLLM repo before"
    echo "bumping those independently."
  fi
fi
