#!/usr/bin/env python3
"""Compute the GB10 build number without reusing an immutable image tag."""

from __future__ import annotations

import argparse


class BuildPolicyError(ValueError):
    """Raised when reviewed inputs do not authorize a build-number decision."""


def compute_build_number(
    old_vllm_ref: str,
    new_vllm_ref: str,
    old_vllm_commit: str,
    reviewed_vllm_commit: str,
    resolved_vllm_commit: str,
    old_build: int,
    other_input_changed: bool,
) -> int:
    """Validate ref movement, then allocate the next immutable image tag."""
    if (
        new_vllm_ref == old_vllm_ref
        and resolved_vllm_commit != old_vllm_commit
        and reviewed_vllm_commit != resolved_vllm_commit
    ):
        raise BuildPolicyError(
            f"{new_vllm_ref} moved from {old_vllm_commit} to "
            f"{resolved_vllm_commit}; update VLLM_COMMIT to the new SHA "
            "and review that exact commit before rerunning bump.sh"
        )
    if new_vllm_ref != old_vllm_ref:
        return 0
    if resolved_vllm_commit != old_vllm_commit or other_input_changed:
        return old_build + 1
    return old_build


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--old-vllm-ref", required=True)
    parser.add_argument("--new-vllm-ref", required=True)
    parser.add_argument("--old-vllm-commit", required=True)
    parser.add_argument("--reviewed-vllm-commit", required=True)
    parser.add_argument("--resolved-vllm-commit", required=True)
    parser.add_argument("--old-build", required=True, type=int)
    parser.add_argument("--other-input-changed", action="store_true")
    args = parser.parse_args()

    try:
        build_number = compute_build_number(
            old_vllm_ref=args.old_vllm_ref,
            new_vllm_ref=args.new_vllm_ref,
            old_vllm_commit=args.old_vllm_commit,
            reviewed_vllm_commit=args.reviewed_vllm_commit,
            resolved_vllm_commit=args.resolved_vllm_commit,
            old_build=args.old_build,
            other_input_changed=args.other_input_changed,
        )
    except BuildPolicyError as error:
        parser.error(str(error))
    print(build_number)


if __name__ == "__main__":
    main()
