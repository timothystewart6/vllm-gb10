#!/usr/bin/env python3
"""Resolve the reviewed B12X version policy for a vLLM release."""

from __future__ import annotations

import argparse


class CompatibilityError(ValueError):
    """Raised when no reviewed B12X compatibility decision exists."""


# Each vLLM release needs an explicit compatibility decision. Do not add a
# fallback that carries a B12X pin to a different vLLM release.
POLICIES = {
    "v0.31.0": ("1.3.0", "1.5.0"),
}


def expected_b12x_version(vllm_ref: str, upstream_version: str) -> str:
    """Return the reviewed B12X version for one vLLM release."""
    policy = POLICIES.get(vllm_ref)
    if policy is None:
        raise CompatibilityError(
            f"No B12X compatibility policy exists for {vllm_ref}. "
            "Recompute the B12X and CUTLASS dependency graph before updating VLLM_REF."
        )
    expected_upstream_version, resolved_version = policy
    if upstream_version != expected_upstream_version:
        raise CompatibilityError(
            f"vLLM {vllm_ref} declares b12x=={upstream_version}, but the reviewed "
            f"policy expects b12x=={expected_upstream_version}. Recompute the B12X and "
            "CUTLASS dependency graph before updating the policy."
        )
    return resolved_version


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
