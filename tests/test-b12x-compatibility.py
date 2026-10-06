#!/usr/bin/env python3
"""Regression tests for reviewed B12X compatibility policies."""

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "b12x_compatibility", ROOT / "scripts" / "b12x_compatibility.py"
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def expect_rejected(vllm_ref, upstream_version):
    try:
        MODULE.expected_b12x_version(vllm_ref, upstream_version)
    except MODULE.CompatibilityError:
        return
    raise AssertionError(
        f"accepted an unreviewed B12X policy for {vllm_ref} and {upstream_version}"
    )


def main():
    assert MODULE.expected_b12x_version("v0.31.0", "1.3.0") == "1.5.0"
    expect_rejected("v0.31.0", "1.4.0")
    expect_rejected("v0.31.1", "1.3.0")
    print("All B12X compatibility policy tests passed.")


if __name__ == "__main__":
    main()
