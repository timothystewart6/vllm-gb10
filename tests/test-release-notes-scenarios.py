#!/usr/bin/env python3
"""
Test harness for the "Changed components" logic in generate-release-notes.sh.

Tests every realistic scenario by simulating git tags and versions.env
content, then running the same Python logic that the shell script uses.
Output is written to the path specified by OUTPUT_PATH env var, or
defaults to /tmp/test-release-notes-output.md.
"""

import os
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SOURCE_ROOT / "scripts"))
from versions_diff import (
    COMPONENTS,
    LOCKFILES,
    extract_apt_snapshot_date,
    file_sha256,
    parse_versions_env,
)


def test_release_notes_script_imports_shared_module():
    """Every supported launch form must import the repository's shared module."""
    repo_root = Path(__file__).resolve().parent.parent
    script = repo_root / "scripts" / "generate-release-notes.sh"
    launch_commands = [
        ["bash", "scripts/generate-release-notes.sh"],  # Exact CI invocation
        ["./scripts/generate-release-notes.sh"],
        ["bash", str(script)],
    ]

    with tempfile.TemporaryDirectory() as fake_module_dir:
        # A conflicting module must not override the repository copy. This also
        # verifies that an inherited REPO_ROOT cannot redirect imports.
        fake_module = Path(fake_module_dir) / "versions_diff.py"
        fake_module.write_text("raise RuntimeError('imported fake versions_diff')\n")

        env = os.environ.copy()
        env.update({
            "TAG": "v-import-regression-gb10.0",
            "GITHUB_SHA": "0123456789abcdef0123456789abcdef01234567",
            "PYTHONPATH": fake_module_dir,
            "REPO_ROOT": fake_module_dir,
        })

        for command in launch_commands:
            result = subprocess.run(
                command,
                cwd=repo_root,
                env=env,
                capture_output=True,
                text=True,
                timeout=20,
            )
            assert result.returncode == 0, (
                f"release-note command failed: {command}\n{result.stderr}"
            )
            assert "## v-import-regression-gb10.0" in result.stdout
            assert "ModuleNotFoundError" not in result.stderr
            assert "imported fake versions_diff" not in result.stderr


test_release_notes_script_imports_shared_module()

# ---------------------------------------------------------------------------
# Release-note scenario helpers
# ---------------------------------------------------------------------------

def get_previous_tag(current_tag, all_tags):
    """Simulated version: pass a sorted list of all tags (newest first)."""
    if current_tag in all_tags:
        idx = all_tags.index(current_tag)
        if idx + 1 < len(all_tags):
            return all_tags[idx + 1]
    return None

def get_changed_section(current_tag, current_env, prev_tag, prev_env,
                        lockfile_map=None):
    """Return (prev_tag, changed_lines_list).
    
    lockfile_map: dict of lock_path -> (old_content, new_content) for testing.
    """
    if not prev_tag or not prev_env:
        return (None, [])
    changed = []
    for var, label in COMPONENTS:
        old_val = prev_env.get(var, "")
        new_val = current_env.get(var, "")
        if old_val != new_val:
            changed.append(f"- **{label}**: {old_val} -> {new_val}")

    # Lockfile diffs
    if lockfile_map:
        for lock_path, lock_label in LOCKFILES:
            pair = lockfile_map.get(lock_path)
            if pair:
                old_content, new_content = pair
                if old_content is not None and new_content is not None:
                    old_hash = file_sha256(old_content)
                    new_hash = file_sha256(new_content)
                    if old_hash != new_hash:
                        if lock_path == "locks/apt-sources.list":
                            old_date = extract_apt_snapshot_date(old_content)
                            new_date = extract_apt_snapshot_date(new_content)
                            if old_date and new_date and old_date != new_date:
                                changed.append(f"- **{lock_label}**: {old_date} -> {new_date}")
                            else:
                                changed.append(f"- **{lock_label}**: `{old_hash}` -> `{new_hash}`")
                        else:
                            changed.append(f"- **{lock_label}**: `{old_hash}` -> `{new_hash}`")

    return (prev_tag, changed)

def build_changed_section(prev_tag, changed_lines):
    """Build the changed-components block (same logic as the shell script)."""
    if changed_lines:
        changed_block = "\n".join(changed_lines)
        return f"""

### Changed components (vs {prev_tag})

{changed_block}
"""
    elif prev_tag:
        return f"""

### Changed components (vs {prev_tag})

No changes - identical component pins to the previous release.
"""
    else:
        return ""

def render_body(tag, short_sha, full_sha, cuda_base_image, cuda_base_digest,
                gb10_build, uv_version, torch_version, torchvision, torchaudio,
                triton_version, pytorch_index, nvshmem_version, tvm_ffi_version,
                tilelang_version, numba_version, nccl_ref, nccl_commit,
                vllm_ref, vllm_commit, flashinfer_ref, flashinfer_commit,
                ray_version, fastsafe_version, instant_version, arch_list,
                changed_section):
    """Render the full release body (same logic as the script)."""
    cuda_ver = cuda_base_image.split(":")[1].split("-")[0]
    cuda_short = "cu" + ".".join(cuda_ver.split(".")[:2])
    torch_short = "torch" + ".".join(torch_version.split(".")[:2])

    registry = "ghcr.io/timothystewart6/vllm-gb10"
    tag_canonical = f"{registry}:{vllm_ref}-gb10.{gb10_build}"
    tag_component = f"{registry}:{vllm_ref}-{cuda_short}-{torch_short}-gb10.{gb10_build}"
    tag_latest = f"{registry}:latest"
    tag_sha = f"{registry}:sha-{short_sha}"

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
    return textwrap.dedent(body)


# ---------------------------------------------------------------------------
# Scenario definitions
# ---------------------------------------------------------------------------

# Base env values shared across scenarios (matching current HEAD)
BASE_ENV = {
    "CUDA_BASE_IMAGE": "nvidia/cuda:13.2.0-devel-ubuntu24.04",
    "CUDA_BASE_DIGEST": "sha256:a5b6256e470196fc1d5f8f62139d57d3662867746dfe1cb352d7652024047020",
    "GB10_BUILD": "1",
    "UV_VERSION": "0.11.29",
    "TORCH_VERSION": "2.11.0",
    "TORCHVISION_VERSION": "0.26.0",
    "TORCHAUDIO_VERSION": "2.11.0",
    "TRITON_VERSION": "3.6.0",
    "PYTORCH_INDEX_URL": "https://download.pytorch.org/whl/cu130",
    "NVSHMEM_VERSION": "3.4.5",
    "TVM_FFI_VERSION": "0.1.9",
    "TILELANG_VERSION": "0.1.9",
    "NUMBA_VERSION": "0.65.0",
    "NCCL_REF": "v2.30.7-1",
    "NCCL_COMMIT": "73cf112295c33aee2b895f329f592f2a9b4b0f97",
    "VLLM_REF": "v0.25.1",
    "VLLM_COMMIT": "752a3a504485790a2e8491cacbb35c137339ad34",
    "FLASHINFER_REF": "v0.6.13",
    "FLASHINFER_COMMIT": "57ba7eeb7ea3003a2d6ad5d9a057c4f952709bac",
    "RAY_VERSION": "2.56.0",
    "FASTSAFETENSORS_VERSION": "0.2.2",
    "INSTANTTENSOR_VERSION": "0.1.8",
    "QUACK_KERNELS_VERSION": "0.6.1",
    "TORCH_CUDA_ARCH_LIST": "12.1a",
}

# Shared constants for rendering
SHORT_SHA = "abcdef1"
FULL_SHA = "abcdef1234567890abcdef1234567890abcdef12"

# ---------------------------------------------------------------------------
# Lockfile content helpers
# ---------------------------------------------------------------------------

def make_apt_sources(snapshot_date):
    """Generate apt-sources.list content for a given snapshot date."""
    return f"""# Pinned apt repositories for reproducible builds.
deb https://snapshot.ubuntu.com/ubuntu/{snapshot_date}/ noble main restricted universe multiverse
deb https://snapshot.ubuntu.com/ubuntu/{snapshot_date}/ noble-updates main restricted universe multiverse
deb https://snapshot.ubuntu.com/ubuntu/{snapshot_date}/ noble-security main restricted universe multiverse
"""

APT_SOURCES_OLD = make_apt_sources("20260701T000000Z")
APT_SOURCES_NEW = make_apt_sources("20260714T000000Z")

APT_PACKAGES_OLD = """build-essential=12.10ubuntu1
ca-certificates=20260601~24.04.1
cmake=3.28.3-1build7
curl=8.5.0-2ubuntu10.9
git=1:2.43.0-1ubuntu7.2
ninja-build=1.11.1-2
python3-dev=3.12.3-0ubuntu2.1
"""

APT_PACKAGES_NEW = """build-essential=12.10ubuntu1
ca-certificates=20260601~24.04.1
cmake=3.28.3-1build7
curl=8.5.0-2ubuntu10.11
git=1:2.43.0-1ubuntu7.3
ninja-build=1.11.1-2
python3-dev=3.12.3-0ubuntu2.1
"""

PYTHON_BOOTSTRAP_OLD = """# uv==0.11.26
uv==0.11.26 \\
    --hash=sha256:aaaa...
"""

PYTHON_BOOTSTRAP_NEW = """# uv==0.11.28
uv==0.11.28 \\
    --hash=sha256:bbbb...
"""

PYTHON_BUILD_OLD = """# apache-tvm-ffi==0.1.9
apache-tvm-ffi==0.1.9 \\
    --hash=sha256:cccc...
"""

PYTHON_BUILD_NEW = """# apache-tvm-ffi==0.1.11
apache-tvm-ffi==0.1.11 \\
    --hash=sha256:dddd...
"""

PYTHON_RUNTIME_OLD = """# ray==2.54.0
ray==2.54.0 \\
    --hash=sha256:eeee...
"""

PYTHON_RUNTIME_NEW = """# ray==2.56.0
ray==2.56.0 \\
    --hash=sha256:ffff...
"""

# ---------------------------------------------------------------------------
# Systematic scenario generator - tests every combination of what can change
# ---------------------------------------------------------------------------

# Shared constants for rendering
SHORT_SHA = "abcdef1"
FULL_SHA = "abcdef1234567890abcdef1234567890abcdef12"

# Lockfile content helpers
def make_apt_sources(snapshot_date):
    return f"""# Pinned apt repositories for reproducible builds.
deb https://snapshot.ubuntu.com/ubuntu/{snapshot_date}/ noble main restricted universe multiverse
deb https://snapshot.ubuntu.com/ubuntu/{snapshot_date}/ noble-updates main restricted universe multiverse
deb https://snapshot.ubuntu.com/ubuntu/{snapshot_date}/ noble-security main restricted universe multiverse
"""

APT_SOURCES_A = make_apt_sources("20260701T000000Z")
APT_SOURCES_B = make_apt_sources("20260714T000000Z")

APT_PACKAGES_A = """build-essential=12.10ubuntu1
ca-certificates=20260601~24.04.1
cmake=3.28.3-1build7
curl=8.5.0-2ubuntu10.9
git=1:2.43.0-1ubuntu7.2
ninja-build=1.11.1-2
python3-dev=3.12.3-0ubuntu2.1
"""

APT_PACKAGES_B = """build-essential=12.10ubuntu1
ca-certificates=20260601~24.04.1
cmake=3.28.3-1build7
curl=8.5.0-2ubuntu10.11
git=1:2.43.0-1ubuntu7.3
ninja-build=1.11.1-2
python3-dev=3.12.3-0ubuntu2.1
"""

PYTHON_BOOTSTRAP_A = """# uv==0.11.26
uv==0.11.26 \\
    --hash=sha256:aaaa...
"""

PYTHON_BOOTSTRAP_B = """# uv==0.11.28
uv==0.11.28 \\
    --hash=sha256:bbbb...
"""

PYTHON_BUILD_A = """# apache-tvm-ffi==0.1.9
apache-tvm-ffi==0.1.9 \\
    --hash=sha256:cccc...
"""

PYTHON_BUILD_B = """# apache-tvm-ffi==0.1.11
apache-tvm-ffi==0.1.11 \\
    --hash=sha256:dddd...
"""

PYTHON_RUNTIME_A = """# ray==2.54.0
ray==2.54.0 \\
    --hash=sha256:eeee...
"""

PYTHON_RUNTIME_B = """# ray==2.56.0
ray==2.56.0 \\
    --hash=sha256:ffff...
"""

# Lockfile set A (old) and B (new)
LOCKFILES_A = {
    "locks/apt-packages.txt": APT_PACKAGES_A,
    "locks/apt-sources.list": APT_SOURCES_A,
    "locks/python-bootstrap.txt": PYTHON_BOOTSTRAP_A,
    "locks/python-build.txt": PYTHON_BUILD_A,
    "locks/python-runtime.txt": PYTHON_RUNTIME_A,
}

LOCKFILES_B = {
    "locks/apt-packages.txt": APT_PACKAGES_B,
    "locks/apt-sources.list": APT_SOURCES_B,
    "locks/python-bootstrap.txt": PYTHON_BOOTSTRAP_B,
    "locks/python-build.txt": PYTHON_BUILD_B,
    "locks/python-runtime.txt": PYTHON_RUNTIME_B,
}

# Base env values (matching current HEAD)
BASE_ENV = {
    "CUDA_BASE_IMAGE": "nvidia/cuda:13.2.0-devel-ubuntu24.04",
    "CUDA_BASE_DIGEST": "sha256:a5b6256e470196fc1d5f8f62139d57d3662867746dfe1cb352d7652024047020",
    "GB10_BUILD": "1",
    "UV_VERSION": "0.11.29",
    "TORCH_VERSION": "2.11.0",
    "TORCHVISION_VERSION": "0.26.0",
    "TORCHAUDIO_VERSION": "2.11.0",
    "TRITON_VERSION": "3.6.0",
    "PYTORCH_INDEX_URL": "https://download.pytorch.org/whl/cu130",
    "NVSHMEM_VERSION": "3.4.5",
    "TVM_FFI_VERSION": "0.1.9",
    "TILELANG_VERSION": "0.1.9",
    "NUMBA_VERSION": "0.65.0",
    "NCCL_REF": "v2.30.7-1",
    "NCCL_COMMIT": "73cf112295c33aee2b895f329f592f2a9b4b0f97",
    "VLLM_REF": "v0.25.1",
    "VLLM_COMMIT": "752a3a504485790a2e8491cacbb35c137339ad34",
    "FLASHINFER_REF": "v0.6.13",
    "FLASHINFER_COMMIT": "57ba7eeb7ea3003a2d6ad5d9a057c4f952709bac",
    "RAY_VERSION": "2.56.0",
    "FASTSAFETENSORS_VERSION": "0.2.2",
    "INSTANTTENSOR_VERSION": "0.1.8",
    "TORCH_CUDA_ARCH_LIST": "12.1a",
}

# Helper: create a scenario tuple
def make_scenario(name, current_tag, tags, current_env_mods, prev_env_mods, lockfiles_old, lockfiles_new):
    """Create a scenario definition.
    
    current_env_mods: dict of overrides for current env (applied on top of BASE_ENV)
    prev_env_mods: dict of overrides for previous env (applied on top of current)
    lockfiles_old: dict of lockfile content at previous tag (or None = same as new)
    lockfiles_new: dict of lockfile content at current tag (or None = same as old)
    """
    current_env = dict(BASE_ENV)
    current_env.update(current_env_mods)
    
    if prev_env_mods is not None:
        prev_env = dict(current_env)
        prev_env.update(prev_env_mods)
    else:
        prev_env = None
    
    # Build lockfile map: (old_content, new_content) pairs
    lockfile_map = {}
    if lockfiles_old is not None and lockfiles_new is not None:
        for path in set(list(lockfiles_old.keys()) + list(lockfiles_new.keys())):
            old = lockfiles_old.get(path)
            new = lockfiles_new.get(path)
            if old != new:
                lockfile_map[path] = (old, new)
    elif lockfiles_old is not None and lockfiles_new is None:
        # Only old lockfiles provided - new ones match old (no change)
        pass
    elif lockfiles_new is not None and lockfiles_old is None:
        # Only new lockfiles provided - old ones match new (no change)
        pass
    
    return (name, current_tag, tags, current_env, prev_env, lockfile_map)


# ---------------------------------------------------------------------------
# Define all scenarios systematically
# ---------------------------------------------------------------------------

# Each scenario is: (name, current_tag, tags_list, current_env_mods, prev_env_mods, lockfiles_old, lockfiles_new)
# - prev_env_mods=None means no previous tag (first release / tag not found)
# - lockfiles_old=None means lockfiles didn't exist at previous tag
# - lockfiles_new=None means lockfiles don't exist at current tag

scenario_defs = [
    # --- No previous tag ---
    ("First release ever (no previous tag)",
     "v0.20.1-gb10.0", ["v0.20.1-gb10.0"],
     {"VLLM_REF": "v0.20.1", "VLLM_COMMIT": "a"*40, "GB10_BUILD": "0", "UV_VERSION": "0.9.9",
      "NCCL_REF": "v2.30.4-1", "NCCL_COMMIT": "b"*40, "FLASHINFER_REF": "v0.6.8.post1",
      "FLASHINFER_COMMIT": "c"*40, "RAY_VERSION": "2.55.1"},
     None, None, None),

    ("Tag not in tag list (hypothetical new tag)",
     "v0.26.0-gb10.0", ["v0.25.1-gb10.0", "v0.25.0-gb10.0"],
     {"VLLM_REF": "v0.26.0", "VLLM_COMMIT": "l"*40, "GB10_BUILD": "0",
      "FLASHINFER_REF": "v0.6.14", "FLASHINFER_COMMIT": "m"*40},
     None, None, None),

    # --- No changes (identical pins) ---
    ("No changes - identical pins, rebuild",
     "v0.25.1-gb10.1", ["v0.25.1-gb10.1", "v0.25.1-gb10.0", "v0.25.0-gb10.0"],
     {"VLLM_REF": "v0.25.1", "VLLM_COMMIT": "h"*40, "GB10_BUILD": "1", "UV_VERSION": "0.11.28",
      "FLASHINFER_REF": "v0.6.13", "FLASHINFER_COMMIT": "g"*40},
     {"GB10_BUILD": "0"},
     LOCKFILES_B, LOCKFILES_B),

    # --- Only versions.env components changed (no lockfile changes) ---
    ("Only vLLM ref changed (lockfiles identical)",
     "v0.25.1-gb10.0", ["v0.25.1-gb10.0", "v0.25.0-gb10.0", "v0.24.0-gb10.5"],
     {"VLLM_REF": "v0.25.1", "VLLM_COMMIT": "h"*40, "GB10_BUILD": "0", "UV_VERSION": "0.11.28",
      "FLASHINFER_REF": "v0.6.13", "FLASHINFER_COMMIT": "g"*40},
     {"VLLM_REF": "v0.25.0", "VLLM_COMMIT": "f"*40},
     LOCKFILES_B, LOCKFILES_B),

    ("Only CUDA base image changed (lockfiles identical)",
     "v0.25.1-gb10.1", ["v0.25.1-gb10.1", "v0.25.1-gb10.0"],
     {"CUDA_BASE_IMAGE": "nvidia/cuda:13.3.0-devel-ubuntu24.04",
      "CUDA_BASE_DIGEST": "sha256:n"*64, "GB10_BUILD": "1"},
     {"CUDA_BASE_IMAGE": "nvidia/cuda:13.2.0-devel-ubuntu24.04",
      "CUDA_BASE_DIGEST": "sha256:a5b6256e470196fc1d5f8f62139d57d3662867746dfe1cb352d7652024047020",
      "GB10_BUILD": "0"},
     LOCKFILES_B, LOCKFILES_B),

    ("Only uv version changed (lockfiles identical)",
     "v0.25.1-gb10.1", ["v0.25.1-gb10.1", "v0.25.1-gb10.0"],
     {"VLLM_REF": "v0.25.1", "VLLM_COMMIT": "h"*40, "GB10_BUILD": "1", "UV_VERSION": "0.11.30",
      "FLASHINFER_REF": "v0.6.13", "FLASHINFER_COMMIT": "g"*40},
     {"GB10_BUILD": "0", "UV_VERSION": "0.11.28"},
     LOCKFILES_B, LOCKFILES_B),

    # --- Only lockfiles changed (no versions.env changes) ---
    ("Only apt packages changed (versions.env identical)",
     "v0.25.1-gb10.1", ["v0.25.1-gb10.1", "v0.25.1-gb10.0"],
     {"VLLM_REF": "v0.25.1", "VLLM_COMMIT": "h"*40, "GB10_BUILD": "1", "UV_VERSION": "0.11.28",
      "FLASHINFER_REF": "v0.6.13", "FLASHINFER_COMMIT": "g"*40},
     {"GB10_BUILD": "0"},
     LOCKFILES_A, LOCKFILES_B),

    ("Only apt snapshot date changed (versions.env identical)",
     "v0.25.1-gb10.1", ["v0.25.1-gb10.1", "v0.25.1-gb10.0"],
     {"VLLM_REF": "v0.25.1", "VLLM_COMMIT": "h"*40, "GB10_BUILD": "1", "UV_VERSION": "0.11.28",
      "FLASHINFER_REF": "v0.6.13", "FLASHINFER_COMMIT": "g"*40},
     {"GB10_BUILD": "0"},
     # Only apt-sources.list differs
     {"locks/apt-packages.txt": APT_PACKAGES_B, "locks/apt-sources.list": APT_SOURCES_A,
      "locks/python-bootstrap.txt": PYTHON_BOOTSTRAP_B, "locks/python-build.txt": PYTHON_BUILD_B,
      "locks/python-runtime.txt": PYTHON_RUNTIME_B},
     LOCKFILES_B),

    ("Only python bootstrap lock changed (versions.env identical)",
     "v0.25.1-gb10.1", ["v0.25.1-gb10.1", "v0.25.1-gb10.0"],
     {"VLLM_REF": "v0.25.1", "VLLM_COMMIT": "h"*40, "GB10_BUILD": "1", "UV_VERSION": "0.11.28",
      "FLASHINFER_REF": "v0.6.13", "FLASHINFER_COMMIT": "g"*40},
     {"GB10_BUILD": "0"},
     # Only python-bootstrap.txt differs
     {"locks/apt-packages.txt": APT_PACKAGES_B, "locks/apt-sources.list": APT_SOURCES_B,
      "locks/python-bootstrap.txt": PYTHON_BOOTSTRAP_A, "locks/python-build.txt": PYTHON_BUILD_B,
      "locks/python-runtime.txt": PYTHON_RUNTIME_B},
     LOCKFILES_B),

    ("Only python build lock changed (versions.env identical)",
     "v0.25.1-gb10.1", ["v0.25.1-gb10.1", "v0.25.1-gb10.0"],
     {"VLLM_REF": "v0.25.1", "VLLM_COMMIT": "h"*40, "GB10_BUILD": "1", "UV_VERSION": "0.11.28",
      "FLASHINFER_REF": "v0.6.13", "FLASHINFER_COMMIT": "g"*40},
     {"GB10_BUILD": "0"},
     # Only python-build.txt differs
     {"locks/apt-packages.txt": APT_PACKAGES_B, "locks/apt-sources.list": APT_SOURCES_B,
      "locks/python-bootstrap.txt": PYTHON_BOOTSTRAP_B, "locks/python-build.txt": PYTHON_BUILD_A,
      "locks/python-runtime.txt": PYTHON_RUNTIME_B},
     LOCKFILES_B),

    ("Only python runtime lock changed (versions.env identical)",
     "v0.25.1-gb10.1", ["v0.25.1-gb10.1", "v0.25.1-gb10.0"],
     {"VLLM_REF": "v0.25.1", "VLLM_COMMIT": "h"*40, "GB10_BUILD": "1", "UV_VERSION": "0.11.28",
      "FLASHINFER_REF": "v0.6.13", "FLASHINFER_COMMIT": "g"*40},
     {"GB10_BUILD": "0"},
     # Only python-runtime.txt differs
     {"locks/apt-packages.txt": APT_PACKAGES_B, "locks/apt-sources.list": APT_SOURCES_B,
      "locks/python-bootstrap.txt": PYTHON_BOOTSTRAP_B, "locks/python-build.txt": PYTHON_BUILD_B,
      "locks/python-runtime.txt": PYTHON_RUNTIME_A},
     LOCKFILES_B),

    # --- Mixed: versions.env + lockfiles changed ---
    ("vLLM bump + all lockfiles changed",
     "v0.25.0-gb10.0", ["v0.25.0-gb10.0", "v0.24.0-gb10.5", "v0.24.0-gb10.0"],
     {"VLLM_REF": "v0.25.0", "VLLM_COMMIT": "f"*40, "GB10_BUILD": "0", "UV_VERSION": "0.11.28",
      "FLASHINFER_REF": "v0.6.13", "FLASHINFER_COMMIT": "g"*40},
     {"VLLM_REF": "v0.24.0", "VLLM_COMMIT": "d"*40, "GB10_BUILD": "5",
      "FLASHINFER_REF": "v0.6.12", "FLASHINFER_COMMIT": "e"*40},
     LOCKFILES_A, LOCKFILES_B),

    ("GB10_BUILD increment + all lockfiles changed",
     "v0.24.0-gb10.5", ["v0.24.0-gb10.5", "v0.24.0-gb10.0"],
     {"VLLM_REF": "v0.24.0", "VLLM_COMMIT": "d"*40, "GB10_BUILD": "5", "UV_VERSION": "0.11.28",
      "FLASHINFER_REF": "v0.6.12", "FLASHINFER_COMMIT": "e"*40},
     {"GB10_BUILD": "0", "UV_VERSION": "0.11.26"},
     LOCKFILES_A, LOCKFILES_B),

    ("Multiple versions.env + all lockfiles changed",
     "v0.23.0-gb10.1", ["v0.23.0-gb10.1", "v0.23.0-gb10.0"],
     {"VLLM_REF": "v0.23.0", "VLLM_COMMIT": "i"*40, "GB10_BUILD": "1", "UV_VERSION": "0.11.26",
      "NCCL_REF": "v2.30.5-1", "NCCL_COMMIT": "j"*40, "FLASHINFER_REF": "v0.6.10",
      "FLASHINFER_COMMIT": "k"*40, "RAY_VERSION": "2.55.1"},
     {"GB10_BUILD": "0", "UV_VERSION": "0.11.25", "NCCL_REF": "v2.30.4-1",
      "NCCL_COMMIT": "b"*40, "RAY_VERSION": "2.54.0"},
     LOCKFILES_A, LOCKFILES_B),

    # --- Edge cases ---
    ("Apt snapshot date same but content differs (hash fallback)",
     "v0.25.1-gb10.1", ["v0.25.1-gb10.1", "v0.25.1-gb10.0"],
     {"VLLM_REF": "v0.25.1", "VLLM_COMMIT": "h"*40, "GB10_BUILD": "1", "UV_VERSION": "0.11.28",
      "FLASHINFER_REF": "v0.6.13", "FLASHINFER_COMMIT": "g"*40},
     {"GB10_BUILD": "0"},
     # Same date but different content (e.g. different mirror URL)
     {"locks/apt-sources.list": "# Old mirror\n" + make_apt_sources("20260714T000000Z"),
      "locks/apt-packages.txt": APT_PACKAGES_B, "locks/python-bootstrap.txt": PYTHON_BOOTSTRAP_B,
      "locks/python-build.txt": PYTHON_BUILD_B, "locks/python-runtime.txt": PYTHON_RUNTIME_B},
     LOCKFILES_B),

    ("Apt snapshot date changed but hash same (impossible - just verifying)",
     "v0.25.1-gb10.1", ["v0.25.1-gb10.1", "v0.25.1-gb10.0"],
     {"VLLM_REF": "v0.25.1", "VLLM_COMMIT": "h"*40, "GB10_BUILD": "1", "UV_VERSION": "0.11.28",
      "FLASHINFER_REF": "v0.6.13", "FLASHINFER_COMMIT": "g"*40},
     {"GB10_BUILD": "0"},
     LOCKFILES_B, LOCKFILES_B),
]

# Build the scenario tuples
scenarios = []
for defn in scenario_defs:
    name, current_tag, tags, cur_mods, prev_mods, lockfiles_old, lockfiles_new = defn
    scenarios.append(make_scenario(name, current_tag, tags, cur_mods, prev_mods, lockfiles_old, lockfiles_new))

# ---------------------------------------------------------------------------
# Run all scenarios
# ---------------------------------------------------------------------------

results = []

for title, current_tag, all_tags, current_env, prev_env, lockfile_map in scenarios:
    print(f"\n{'='*72}")
    print(f"  {title}")
    print(f"{'='*72}")

    prev_tag = get_previous_tag(current_tag, all_tags)
    print(f"  Current tag: {current_tag}")
    print(f"  Previous tag: {prev_tag}")

    prev_tag_resolved, changed = get_changed_section(
        current_tag, current_env, prev_tag, prev_env, lockfile_map=lockfile_map
    )

    changed_section = build_changed_section(prev_tag_resolved, changed)

    if changed:
        print(f"  Changed components: {len(changed)}")
        for line in changed:
            print(f"    {line}")
    else:
        if prev_tag:
            print(f"  Result: No components changed (showing 'no changes' message)")
        else:
            print(f"  Result: No previous tag found - no diff section")

    # Render full body to verify it integrates correctly
    body = render_body(
        tag=current_tag,
        short_sha=SHORT_SHA,
        full_sha=FULL_SHA,
        cuda_base_image=current_env["CUDA_BASE_IMAGE"],
        cuda_base_digest=current_env["CUDA_BASE_DIGEST"],
        gb10_build=current_env["GB10_BUILD"],
        uv_version=current_env["UV_VERSION"],
        torch_version=current_env["TORCH_VERSION"],
        torchvision=current_env.get("TORCHVISION_VERSION", "0.26.0"),
        torchaudio=current_env.get("TORCHAUDIO_VERSION", "2.11.0"),
        triton_version=current_env.get("TRITON_VERSION", "3.6.0"),
        pytorch_index=current_env.get("PYTORCH_INDEX_URL", "https://download.pytorch.org/whl/cu130"),
        nvshmem_version=current_env.get("NVSHMEM_VERSION", "3.4.5"),
        tvm_ffi_version=current_env.get("TVM_FFI_VERSION", "0.1.9"),
        tilelang_version=current_env.get("TILELANG_VERSION", "0.1.9"),
        numba_version=current_env.get("NUMBA_VERSION", "0.65.0"),
        nccl_ref=current_env["NCCL_REF"],
        nccl_commit=current_env["NCCL_COMMIT"],
        vllm_ref=current_env["VLLM_REF"],
        vllm_commit=current_env["VLLM_COMMIT"],
        flashinfer_ref=current_env["FLASHINFER_REF"],
        flashinfer_commit=current_env["FLASHINFER_COMMIT"],
        ray_version=current_env["RAY_VERSION"],
        fastsafe_version=current_env.get("FASTSAFETENSORS_VERSION", "0.2.2"),
        instant_version=current_env.get("INSTANTTENSOR_VERSION", "0.1.8"),
        arch_list=current_env.get("TORCH_CUDA_ARCH_LIST", "12.1a"),
        changed_section=changed_section,
    )

    results.append({
        "title": title,
        "current_tag": current_tag,
        "prev_tag": prev_tag,
        "changed_count": len(changed),
        "changed_lines": changed,
        "body": body,
    })

# ---------------------------------------------------------------------------
# Write output markdown
# ---------------------------------------------------------------------------
# Check OUTPUT_PATH env var, then .env file, then fall back to /tmp.
output_path = os.environ.get("OUTPUT_PATH")
if not output_path:
    try:
        with open(".env") as f:
            for line in f:
                line = line.strip()
                if line.startswith("OUTPUT_PATH="):
                    output_path = line.split("=", 1)[1].strip().strip("\"'")
                    break
    except FileNotFoundError:
        pass
if not output_path:
    output_path = "/tmp/test-release-notes-output.md"
# If the value is a directory, append the default filename
if os.path.isdir(output_path):
    output_path = os.path.join(output_path, "test-release-notes-output.md")

with open(output_path, "w") as f:
    f.write("# Release Notes - Test Scenarios Output\n\n")
    f.write(f"Generated: 2026-07-16\n\n")
    f.write("## Summary\n\n")
    f.write("| # | Scenario | Current Tag | Previous Tag | Components Changed |\n")
    f.write("|---|----------|-------------|--------------|--------------------|\n")
    for i, r in enumerate(results, 1):
        changed_str = str(r["changed_count"]) if r["changed_count"] > 0 else "0 (no diff section)"
        if r["prev_tag"] is None:
            changed_str = "N/A (first release)"
        f.write(f"| {i} | {r['title']} | `{r['current_tag']}` | `{r['prev_tag'] or 'N/A'}` | {changed_str} |\n")

    f.write("\n---\n\n")

    for i, r in enumerate(results, 1):
        f.write(f"## Scenario {i}: {r['title']}\n\n")
        f.write(f"**Current tag:** `{r['current_tag']}`  \n")
        f.write(f"**Previous tag:** `{r['prev_tag'] or 'N/A'}`  \n")
        f.write(f"**Components changed:** {r['changed_count'] if r['changed_count'] > 0 else 'None'}\n\n")

        if r["changed_lines"]:
            f.write("### Detected changes\n\n")
            for line in r["changed_lines"]:
                f.write(f"{line}\n")
            f.write("\n")

        f.write("### Rendered output\n\n")
        f.write("```markdown\n")
        # Escape any triple backtick fences inside the body so they don't
        # break the outer ```markdown fence. Replace ``` with `\u200b``
        # (zero-width space after the first backtick) to visually preserve
        # the fence while preventing markdown parser confusion.
        escaped_body = r["body"].replace("```", "`\u200b``")
        f.write(escaped_body)
        f.write("```\n\n")
        f.write("---\n\n")

print(f"\n\nOutput written to {output_path}")
