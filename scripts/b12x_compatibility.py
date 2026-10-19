#!/usr/bin/env python3
"""Resolve the B12X version for a vLLM release."""

from __future__ import annotations

import argparse


class CompatibilityError(ValueError):
    """Raised when a reviewed B12X override does not match upstream."""


# vLLM 0.31.0 declares b12x==1.3.0, but that release conflicts with the
# CUTLASS DSL version selected by vLLM. B12X 1.5.0 is the reviewed override.
OVERRIDES = {
    "v0.31.0": ("1.3.0", "1.5.0"),
}


def expected_b12x_version(vllm_ref: str, upstream_version: str) -> str:
    """Return an override when needed, otherwise follow vLLM upstream."""
    override = OVERRIDES.get(vllm_ref)
    if override is not None:
        expected_upstream_version, resolved_version = override
        if upstream_version != expected_upstream_version:
            raise CompatibilityError(
                f"vLLM {vllm_ref} declares b12x=={upstream_version}, but the reviewed "
                f"override expects b12x=={expected_upstream_version}. Recompute the "
                "B12X and CUTLASS dependency graph before updating the override."
            )
        return resolved_version

    return upstream_version


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vllm-ref", required=True)
    parser.add_argument("--upstream-version", required=True)
    args = parser.parse_args()
    try:
        print(expected_b12x_version(args.vllm_ref, args.upstream_version))
    except CompatibilityError as error:
        parser.error(str(error))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
