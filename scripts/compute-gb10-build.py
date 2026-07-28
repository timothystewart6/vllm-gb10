#!/usr/bin/env python3
"""Compute the GB10 build number without reusing an immutable image tag."""

from __future__ import annotations

import argparse


def compute_build_number(
    old_vllm_ref: str,
    new_vllm_ref: str,
    old_build: int,
    build_input_changed: bool,
) -> int:
    """Reset for a new release ref, otherwise advance for changed inputs."""
    if new_vllm_ref != old_vllm_ref:
        return 0
    if build_input_changed:
        return old_build + 1
    return old_build


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--old-vllm-ref", required=True)
    parser.add_argument("--new-vllm-ref", required=True)
    parser.add_argument("--old-build", required=True, type=int)
    parser.add_argument("--build-input-changed", action="store_true")
    args = parser.parse_args()

    print(
        compute_build_number(
            args.old_vllm_ref,
            args.new_vllm_ref,
            args.old_build,
            args.build_input_changed,
        )
    )


if __name__ == "__main__":
    main()
