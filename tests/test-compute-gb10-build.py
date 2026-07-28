#!/usr/bin/env python3
"""Contract tests for immutable GB10 image-tag allocation."""

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "compute-gb10-build.py"
SPEC = importlib.util.spec_from_file_location("compute_gb10_build", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_new_vllm_ref_starts_new_build_series():
    assert MODULE.compute_build_number("v0.25.0", "v0.26.0", 5, True) == 0


def test_moved_tag_advances_existing_build_series():
    assert MODULE.compute_build_number("v0.26.0", "v0.26.0", 2, True) == 3


def test_other_input_change_advances_existing_build_series():
    assert MODULE.compute_build_number("v0.26.0", "v0.26.0", 2, True) == 3


def test_no_change_preserves_build_number():
    assert MODULE.compute_build_number("v0.26.0", "v0.26.0", 2, False) == 2


def main():
    tests = (
        test_new_vllm_ref_starts_new_build_series,
        test_moved_tag_advances_existing_build_series,
        test_other_input_change_advances_existing_build_series,
        test_no_change_preserves_build_number,
    )
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"All {len(tests)} GB10 build-number tests passed!")


if __name__ == "__main__":
    main()
