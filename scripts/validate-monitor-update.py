#!/usr/bin/env python3
"""Validate a release-monitor versions.env candidate against a trusted base."""

from __future__ import annotations

import argparse
from pathlib import Path

from versions_env import VersionsEnvError, parse_versions_env


ALLOWED_UPDATE_KEYS = {
    "CUDA_BASE_DIGEST",
    "FLASHINFER_REF",
    "NCCL_REF",
    "NUMBA_VERSION",
    "QUACK_KERNELS_VERSION",
    "TILELANG_VERSION",
    "TORCHAUDIO_VERSION",
    "TORCHVISION_VERSION",
    "TORCH_VERSION",
    "TRANSFORMERS_VERSION",
    "TRITON_VERSION",
    "TVM_FFI_VERSION",
    "UV_VERSION",
    "VLLM_REF",
}


def render_expected_candidate(
    base_text: str,
    candidate_values: dict[str, str],
    changed_keys: set[str],
) -> str:
    rendered = []
    for raw_line in base_text.splitlines(keepends=True):
        content = raw_line.rstrip("\r\n")
        line_ending = raw_line[len(content):]
        if "=" in content:
            key = content.split("=", 1)[0]
            if key in changed_keys:
                raw_line = f"{key}={candidate_values[key]}{line_ending}"
        rendered.append(raw_line)
    return "".join(rendered)


def validate_monitor_update(base_text: str, candidate_text: str) -> set[str]:
    base_values = parse_versions_env(base_text)
    candidate_values = parse_versions_env(candidate_text)
    changed_keys = {
        key for key in base_values if base_values[key] != candidate_values[key]
    }
    if not changed_keys:
        raise VersionsEnvError("release monitor candidate has no changes")
    unexpected = changed_keys - ALLOWED_UPDATE_KEYS
    if unexpected:
        raise VersionsEnvError(
            "release monitor changed disallowed keys: "
            + ", ".join(sorted(unexpected))
        )
    if "TRITON_VERSION" in changed_keys and "TORCH_VERSION" not in changed_keys:
        raise VersionsEnvError(
            "release monitor cannot change Triton without changing Torch"
        )
    expected_text = render_expected_candidate(
        base_text,
        candidate_values,
        changed_keys,
    )
    if candidate_text != expected_text:
        raise VersionsEnvError(
            "release monitor candidate contains changes outside approved values"
        )
    return changed_keys


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base", type=Path)
    parser.add_argument("candidate", type=Path)
    args = parser.parse_args()
    try:
        changed_keys = validate_monitor_update(
            args.base.read_text(encoding="utf-8"),
            args.candidate.read_text(encoding="utf-8"),
        )
    except (OSError, UnicodeError, VersionsEnvError) as error:
        parser.error(str(error))
    print("Validated release monitor changes: " + ", ".join(sorted(changed_keys)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
