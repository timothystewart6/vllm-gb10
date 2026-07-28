#!/usr/bin/env python3
"""Strict validation for the repository's versions.env build inputs."""

from __future__ import annotations

import argparse
import re
from pathlib import Path


EXPECTED_KEYS = {
    "ACCELERATE_VERSION", "BITSANDBYTES_VERSION", "CUDA_BASE_DIGEST",
    "CUDA_BASE_IMAGE", "FASTSAFETENSORS_VERSION", "FLASHINFER_COMMIT",
    "FLASHINFER_CUDA_ARCH_LIST", "FLASHINFER_INDEX_URL", "FLASHINFER_REF",
    "FLASHINFER_REPO", "GB10_BUILD", "INSTANTTENSOR_VERSION", "NCCL_COMMIT",
    "NCCL_REF", "NCCL_REPO", "NUMBA_VERSION", "NVSHMEM_VERSION",
    "PYPI_INDEX_URL", "PYTORCH_INDEX_URL", "QUACK_KERNELS_VERSION",
    "RAY_VERSION", "TILELANG_VERSION", "TORCHAUDIO_VERSION",
    "TORCHVISION_VERSION", "TORCH_CUDA_ARCH_LIST", "TORCH_VERSION",
    "TRITON_VERSION", "TVM_FFI_VERSION", "UV_VERSION", "VLLM_COMMIT",
    "VLLM_REF", "VLLM_REPO",
}
KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
VALUE_RE = re.compile(r"^[A-Za-z0-9._:/+@-]+$")
MAX_VALUE_LENGTH = 2048
HEX_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
VERSION_RE = re.compile(r"^[0-9]+(?:\.[0-9]+)+(?:[.-][A-Za-z0-9]+)*$")
REF_RE = re.compile(r"^v[0-9]+(?:\.[0-9]+)+(?:-[0-9]+)?$")
CUDA_IMAGE_RE = re.compile(
    r"^nvidia/cuda:[0-9]+\.[0-9]+\.[0-9]+-devel-ubuntu24\.04$"
)
EXACT_VALUES = {
    "NCCL_REPO": "https://github.com/NVIDIA/nccl.git",
    "VLLM_REPO": "https://github.com/vllm-project/vllm.git",
    "FLASHINFER_REPO": "https://github.com/flashinfer-ai/flashinfer.git",
    "PYPI_INDEX_URL": "https://pypi.org/simple",
    "PYTORCH_INDEX_URL": "https://download.pytorch.org/whl/cu130",
    "FLASHINFER_INDEX_URL": "https://flashinfer.ai/whl",
    "TORCH_CUDA_ARCH_LIST": "12.1a",
    "FLASHINFER_CUDA_ARCH_LIST": "12.1a",
}
COMMIT_KEYS = {"NCCL_COMMIT", "VLLM_COMMIT", "FLASHINFER_COMMIT"}
REF_KEYS = {"NCCL_REF", "VLLM_REF", "FLASHINFER_REF"}
VERSION_KEYS = EXPECTED_KEYS - {
    "CUDA_BASE_DIGEST", "CUDA_BASE_IMAGE", "GB10_BUILD",
    *COMMIT_KEYS, *REF_KEYS, *EXACT_VALUES.keys(),
}

# Every build input has exactly one GB10_BUILD role. VLLM_REF resets the image
# series, VLLM_COMMIT requires reviewed ref-resolution policy, GB10_BUILD is the
# counter itself, and every other input increments the counter. Keep the
# increment role derived from EXPECTED_KEYS so new keys fail closed into build
# revision accounting.
BUILD_COUNTER_KEY = "GB10_BUILD"
BUILD_RESET_INPUT_KEYS = {"VLLM_REF"}
REVIEWED_RESOLUTION_INPUT_KEYS = {"VLLM_COMMIT"}
BUILD_INCREMENT_INPUT_KEYS = (
    EXPECTED_KEYS
    - BUILD_RESET_INPUT_KEYS
    - REVIEWED_RESOLUTION_INPUT_KEYS
    - {BUILD_COUNTER_KEY}
)


class VersionsEnvError(ValueError):
    """Raised when versions.env is not safe declarative data."""


def parse_versions_env(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(text.splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if raw_line != line:
            raise VersionsEnvError(
                f"line {line_number}: leading or trailing whitespace is not allowed"
            )
        if "=" not in line:
            raise VersionsEnvError(f"line {line_number}: expected KEY=value")
        key, value = line.split("=", 1)
        if not KEY_RE.fullmatch(key):
            raise VersionsEnvError(f"line {line_number}: invalid key {key!r}")
        if key in values:
            raise VersionsEnvError(f"line {line_number}: duplicate key {key}")
        if key not in EXPECTED_KEYS:
            raise VersionsEnvError(f"line {line_number}: unknown key {key}")
        if (
            not value
            or len(value) > MAX_VALUE_LENGTH
            or not VALUE_RE.fullmatch(value)
        ):
            raise VersionsEnvError(
                f"line {line_number}: unsafe or empty value for {key}"
            )
        values[key] = value
    missing = sorted(EXPECTED_KEYS - values.keys())
    if missing:
        raise VersionsEnvError(f"missing required keys: {', '.join(missing)}")
    for key, expected in EXACT_VALUES.items():
        if values[key] != expected:
            raise VersionsEnvError(f"{key} must be {expected!r}")
    for key in COMMIT_KEYS:
        if not HEX_SHA_RE.fullmatch(values[key]):
            raise VersionsEnvError(f"{key} must be a 40-character commit SHA")
    for key in REF_KEYS:
        if not REF_RE.fullmatch(values[key]):
            raise VersionsEnvError(f"{key} must be a released v-prefixed tag")
    for key in VERSION_KEYS:
        if not VERSION_RE.fullmatch(values[key]):
            raise VersionsEnvError(f"{key} must be an exact numeric version")
    if not DIGEST_RE.fullmatch(values["CUDA_BASE_DIGEST"]):
        raise VersionsEnvError("CUDA_BASE_DIGEST must be a SHA-256 digest")
    if not CUDA_IMAGE_RE.fullmatch(values["CUDA_BASE_IMAGE"]):
        raise VersionsEnvError(
            "CUDA_BASE_IMAGE must be an NVIDIA CUDA devel image for Ubuntu 24.04"
        )
    if (
        not values["GB10_BUILD"].isdigit()
        or int(values["GB10_BUILD"]) > 1_000_000
    ):
        raise VersionsEnvError("GB10_BUILD must be a non-negative integer")
    return values


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--list-build-inputs",
        choices=("increment",),
        help="print the schema keys for one GB10_BUILD change role",
    )
    parser.add_argument("path", nargs="?", default="versions.env", type=Path)
    args = parser.parse_args()
    if args.list_build_inputs:
        print("\n".join(sorted(BUILD_INCREMENT_INPUT_KEYS)))
        return 0
    try:
        parse_versions_env(args.path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, VersionsEnvError) as error:
        parser.error(str(error))
    print(f"Validated {args.path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
