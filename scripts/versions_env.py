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
        if not value or not VALUE_RE.fullmatch(value):
            raise VersionsEnvError(
                f"line {line_number}: unsafe or empty value for {key}"
            )
        values[key] = value
    missing = sorted(EXPECTED_KEYS - values.keys())
    if missing:
        raise VersionsEnvError(f"missing required keys: {', '.join(missing)}")
    return values


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", nargs="?", default="versions.env", type=Path)
    args = parser.parse_args()
    try:
        parse_versions_env(args.path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, VersionsEnvError) as error:
        parser.error(str(error))
    print(f"Validated {args.path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
