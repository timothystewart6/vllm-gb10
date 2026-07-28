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

OLD_COMMIT = "a" * 40
NEW_COMMIT = "b" * 40


def compute(
    *,
    old_ref="v0.26.0",
    new_ref="v0.26.0",
    old_commit=OLD_COMMIT,
    reviewed_commit=OLD_COMMIT,
    resolved_commit=OLD_COMMIT,
    old_build=2,
    other_changed=False,
):
    return MODULE.compute_build_number(
        old_vllm_ref=old_ref,
        new_vllm_ref=new_ref,
        old_vllm_commit=old_commit,
        reviewed_vllm_commit=reviewed_commit,
        resolved_vllm_commit=resolved_commit,
        old_build=old_build,
        other_input_changed=other_changed,
    )


def test_new_vllm_ref_starts_new_build_series():
    assert compute(
        old_ref="v0.25.0",
        new_ref="v0.26.0",
        resolved_commit=NEW_COMMIT,
        old_build=5,
        other_changed=True,
    ) == 0


def test_unreviewed_moved_tag_is_rejected():
    try:
        compute(resolved_commit=NEW_COMMIT)
    except MODULE.BuildPolicyError as error:
        assert "update VLLM_COMMIT" in str(error)
        return
    raise AssertionError("accepted an unreviewed moved vLLM tag")


def test_reviewed_moved_tag_advances_existing_build_series():
    assert compute(
        reviewed_commit=NEW_COMMIT,
        resolved_commit=NEW_COMMIT,
    ) == 3


def test_other_input_change_advances_existing_build_series():
    assert compute(other_changed=True) == 3


def test_no_change_preserves_build_number():
    assert compute() == 2


def test_bump_script_passes_reviewed_and_resolved_commits_to_policy():
    bump = (ROOT / "scripts" / "bump.sh").read_text(encoding="utf-8")
    for argument in (
        '--old-vllm-commit "${OLD_VLLM_COMMIT}"',
        '--reviewed-vllm-commit "${REVIEWED_VLLM_COMMIT}"',
        '--resolved-vllm-commit "${VLLM_COMMIT}"',
    ):
        assert argument in bump
    input_detection = bump.split("OTHER_INPUT_CHANGED=0", 1)[1].split(
        "BUILD_ARGS=(", 1
    )[0]
    assert "VLLM_COMMIT" not in input_detection


def main():
    tests = (
        test_new_vllm_ref_starts_new_build_series,
        test_unreviewed_moved_tag_is_rejected,
        test_reviewed_moved_tag_advances_existing_build_series,
        test_other_input_change_advances_existing_build_series,
        test_no_change_preserves_build_number,
        test_bump_script_passes_reviewed_and_resolved_commits_to_policy,
    )
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"All {len(tests)} GB10 build-number tests passed!")


if __name__ == "__main__":
    main()
