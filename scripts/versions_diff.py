"""
Shared logic for diffing versions.env across releases or working-tree changes.

Provides the canonical mapping of version variable names to human-readable
labels, and functions to produce markdown change lists from two env dicts.

Used by:
  - scripts/generate-release-notes.sh  (release notes)
  - .github/workflows/monitor-upstream-releases.yaml  (automated PR body)
  - tests/test-pr-body-generation.py  (test harness)
"""

import hashlib
import re

# Map variable names to human-readable labels.
# This is the single source of truth for version display names.
COMPONENT_LABELS = {
    "VLLM_REF": "vLLM",
    "FLASHINFER_REF": "FlashInfer",
    "NCCL_REF": "NCCL",
    "UV_VERSION": "uv",
    "TORCH_VERSION": "PyTorch",
    "TORCHVISION_VERSION": "torchvision",
    "TORCHAUDIO_VERSION": "torchaudio",
    "TRITON_VERSION": "Triton",
    "NVSHMEM_VERSION": "NVSHMEM",
    "TVM_FFI_VERSION": "TVM-FFI",
    "TILELANG_VERSION": "TileLang",
    "NUMBA_VERSION": "Numba",
    "RAY_VERSION": "Ray",
    "FASTSAFETENSORS_VERSION": "fastsafetensors",
    "INSTANTTENSOR_VERSION": "instanttensor",
    "CUDA_BASE_IMAGE": "CUDA base",
    "CUDA_BASE_DIGEST": "CUDA base digest",
    "BITSANDBYTES_VERSION": "bitsandbytes",
    "ACCELERATE_VERSION": "Accelerate",
    "TORCH_CUDA_ARCH_LIST": "Target arch",
    "GB10_BUILD": "GB10_BUILD",
}

# Versions.env components (variable, label) list for release notes ordering.
COMPONENTS = [(k, v) for k, v in COMPONENT_LABELS.items()
              if k not in ("CUDA_BASE_DIGEST", "GB10_BUILD")]

# Lockfile change tracking for release notes.
LOCKFILES = [
    ("locks/apt-packages.txt", "apt packages"),
    ("locks/apt-sources.list", "apt snapshot"),
    ("locks/python-bootstrap.txt", "python bootstrap lock"),
    ("locks/python-build.txt", "python build lock"),
    ("locks/python-runtime.txt", "python runtime lock"),
]


def parse_versions_env(text):
    """Parse versions.env text into a dict of variable -> value."""
    env = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", line)
        if m:
            env[m.group(1)] = m.group(2)
    return env


def diff_env_dicts(old_env, new_env):
    """Compare two env dicts and return {key: (old_val, new_val)} for changes."""
    changes = {}
    all_keys = set(old_env.keys()) | set(new_env.keys())
    for key in sorted(all_keys):
        old_val = old_env.get(key, "(added)")
        new_val = new_env.get(key, "(removed)")
        if old_val != new_val:
            changes[key] = (old_val, new_val)
    return changes


def diff_from_git_diff(diff_text):
    """Parse a unified git diff text and return {key: (old_val, new_val)}.

    Matches patterns like:
        -VLLM_REF=v0.24.0
        +VLLM_REF=v0.25.1
    """
    old_env = {}
    new_env = {}
    for line in diff_text.splitlines():
        if line.startswith("---") or line.startswith("+++"):
            continue
        match = re.match(r"^([-+])([A-Za-z_][A-Za-z0-9_]*)=(.*)$", line)
        if not match:
            continue
        destination = old_env if match.group(1) == "-" else new_env
        destination[match.group(2)] = match.group(3)

    changes = {}
    for key in sorted(old_env.keys() | new_env.keys()):
        old_val = old_env.get(key, "(added)")
        new_val = new_env.get(key, "(removed)")
        if old_val != new_val:
            changes[key] = (old_val, new_val)
    return changes


def format_change_lines(changes, labels=None):
    """Format a changes dict into a list of markdown bullet lines."""
    if labels is None:
        labels = COMPONENT_LABELS
    lines = []
    for key, (old_val, new_val) in sorted(changes.items()):
        label = labels.get(key, key)
        lines.append(f"- **{label}**: {old_val} -> {new_val}")
    return lines


def _format_lockfile_value(val):
    """Apply backtick code styling to SHA256 hashes, leave other values plain."""
    stripped = val.removeprefix("sha256:")
    if stripped != val:
        return f"`{stripped}`"
    return val


def format_changes_with_lockfiles(component_changes, lock_changes, labels=None):
    """Format both component and lockfile changes into a single sorted list.

    Separates known components (via format_change_lines) from lockfile changes
    (with human-readable labels), then combines them in sorted order.
    Lockfile values starting with 'sha256:' get backtick-styled (e.g. `` `abc123` ``).
    Other values (snapshot dates, etc.) remain plain text.
    """
    if labels is None:
        labels = COMPONENT_LABELS
    change_lines = format_change_lines(component_changes, labels)

    lock_labels = dict(LOCKFILES)
    for key in sorted(lock_changes):
        lock_path = key[5:]  # strip "LOCK:" prefix
        old_val, new_val = lock_changes[key]
        label = lock_labels.get(lock_path, lock_path)
        change_lines.append(
            f"- **{label}**: {_format_lockfile_value(old_val)}"
            f" -> {_format_lockfile_value(new_val)}"
        )

    return change_lines


def file_sha256(content):
    """Return first 12 chars of SHA256 hex digest of content."""
    return hashlib.sha256(content.encode()).hexdigest()[:12]


def extract_apt_snapshot_date(content):
    """Extract the snapshot timestamp from apt-sources.list, e.g. 20260714T000000Z."""
    if not content:
        return None
    m = re.search(r"snapshot\.ubuntu\.com/ubuntu/(\d{8}T\d{6}Z)", content)
    return m.group(1) if m else None
