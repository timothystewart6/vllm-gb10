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

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export REPO_ROOT
cd "${REPO_ROOT}"

set -a
# shellcheck disable=SC1090,SC1091
. "${REPO_ROOT}/versions.env"
set +a

python3 - <<'PYEOF'
import os, subprocess, textwrap, sys

# Python is reading this program from stdin, so __file__ cannot locate the
# script directory. Use the path resolved by the shell wrapper instead.
sys.path.insert(0, os.path.join(os.environ["REPO_ROOT"], "scripts"))

from versions_diff import (
    COMPONENT_LABELS,
    COMPONENTS,
    LOCKFILES,
    parse_versions_env,
    file_sha256,
    extract_apt_snapshot_date,
    format_change_lines,
)

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

# ---------------------------------------------------------------------------
# Determine previous tag and diff component versions
# ---------------------------------------------------------------------------

def get_previous_tag(current_tag):
    """Find the previous v*-gb10.* tag before current_tag, sorted by version."""
    try:
        result = subprocess.run(
            ["git", "tag", "--list", "*-gb10.*", "--sort=-version:refname"],
            capture_output=True, text=True, check=True, timeout=15,
        )
        tags = [t.strip() for t in result.stdout.splitlines() if t.strip()]
        if current_tag in tags:
            idx = tags.index(current_tag)
            if idx + 1 < len(tags):
                return tags[idx + 1]
        return None
    except Exception:
        return None

def get_previous_versions(prev_tag):
    """Checkout previous tag's versions.env and parse it."""
    try:
        result = subprocess.run(
            ["git", "show", f"{prev_tag}:versions.env"],
            capture_output=True, text=True, check=True, timeout=15,
        )
        return parse_versions_env(result.stdout)
    except Exception:
        return None

def git_show_file(prev_tag, path):
    """Return the content of a file at a given tag, or None."""
    try:
        result = subprocess.run(
            ["git", "show", f"{prev_tag}:{path}"],
            capture_output=True, text=True, check=True, timeout=15,
        )
        return result.stdout
    except Exception:
        return None

def read_current_file(path):
    """Read a file from the working tree."""
    try:
        with open(os.path.join(os.environ["REPO_ROOT"], path), "r") as f:
            return f.read()
    except Exception:
        return None

prev_tag = get_previous_tag(tag)
changed_lines = []

if prev_tag:
    prev_env = get_previous_versions(prev_tag)
    if prev_env:
        changes = {}
        for var, label in COMPONENTS:
            old_val = prev_env.get(var, "")
            new_val = os.environ.get(var, "")
            if old_val != new_val:
                changes[var] = (old_val, new_val)
        changed_lines = format_change_lines(changes, COMPONENT_LABELS)

    # --- Lockfile diffs ---
    for lock_path, lock_label in LOCKFILES:
        old_content = git_show_file(prev_tag, lock_path)
        new_content = read_current_file(lock_path)
        if old_content is not None and new_content is not None:
            old_hash = file_sha256(old_content)
            new_hash = file_sha256(new_content)
            if old_hash != new_hash:
                if lock_path == "locks/apt-sources.list":
                    old_date = extract_apt_snapshot_date(old_content)
                    new_date = extract_apt_snapshot_date(new_content)
                    if old_date and new_date and old_date != new_date:
                        changed_lines.append(f"- **{lock_label}**: {old_date} -> {new_date}")
                    else:
                        changed_lines.append(f"- **{lock_label}**: `{old_hash}` -> `{new_hash}`")
                else:
                    changed_lines.append(f"- **{lock_label}**: `{old_hash}` -> `{new_hash}`")

# Build the changed-components block
if changed_lines:
    changed_block = "\n".join(changed_lines)
    changed_section = f"""

### Changed components (vs {prev_tag})

{changed_block}
"""
elif prev_tag:
    changed_section = f"""

### Changed components (vs {prev_tag})

No changes - identical component pins to the previous release.
"""
else:
    changed_section = ""

body = f"""## {tag}

> Reproducible vLLM image for NVIDIA DGX Spark (GB10 / sm_121a)
{changed_section}
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
