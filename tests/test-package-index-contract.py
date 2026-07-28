#!/usr/bin/env python3
"""Verify package indexes stay aligned across lock generation and image builds."""

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def read(path):
    return (ROOT / path).read_text()


def env_value(name):
    match = re.search(rf"^{re.escape(name)}=(.+)$", read("versions.env"), re.MULTILINE)
    assert match, f"{name} is missing from versions.env"
    return match.group(1)


def test_flashinfer_index_is_an_explicit_build_input():
    assert env_value("FLASHINFER_INDEX_URL") == "https://flashinfer.ai/whl"

    build_args = read("scripts/build-args.sh")
    assert "_arg FLASHINFER_INDEX_URL" in build_args

    workflow = read(".github/workflows/build-image.yaml")
    assert (
        "FLASHINFER_INDEX_URL=${{ env.FLASHINFER_INDEX_URL }}" in workflow
    )


def test_lock_generation_and_runtime_use_the_same_indexes():
    bump = read("scripts/bump.sh")
    dockerfile = read("Dockerfile")

    assert bump.count('--extra-index-url "${FLASHINFER_INDEX_URL}"') == 2
    assert dockerfile.count("--extra-index-url ${FLASHINFER_INDEX_URL}") == 1

    assert "ARG FLASHINFER_INDEX_URL" in dockerfile


def test_quack_is_pinned_for_cutlass_dsl_compatibility():
    assert env_value("QUACK_KERNELS_VERSION") == "0.6.1"

    bump = read("scripts/bump.sh")
    assert "quack-kernels==${QUACK_KERNELS_VERSION}" in bump


def test_instanttensor_supports_vllm_copy_api():
    assert env_value("INSTANTTENSOR_VERSION") == "0.1.9"

    bump = read("scripts/bump.sh")
    assert "instanttensor==${INSTANTTENSOR_VERSION}" in bump


def test_random_lock_input_paths_are_normalized():
    bump = read("scripts/bump.sh")
    assert bump.count('scripts/normalize-lockfile.py"') == 2
    assert '"/tmp/vllm-gb10-build.in"' in bump
    assert '"/tmp/vllm-gb10-runtime.in"' in bump


def test_build_revision_comparison_uses_trusted_main_schema_roles():
    bump = read("scripts/bump.sh")
    assert "_MAIN_VERSIONS=" in bump
    assert 'OLD_GB10_BUILD="$(_main_get GB10_BUILD)"' in bump
    assert "--list-build-inputs increment" in bump
    assert 'python3 "${REPO_ROOT}/scripts/compute-gb10-build.py"' in bump


def main():
    tests = [
        test_flashinfer_index_is_an_explicit_build_input,
        test_lock_generation_and_runtime_use_the_same_indexes,
        test_quack_is_pinned_for_cutlass_dsl_compatibility,
        test_instanttensor_supports_vllm_copy_api,
        test_random_lock_input_paths_are_normalized,
        test_build_revision_comparison_uses_trusted_main_schema_roles,
    ]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"All {len(tests)} package-index contract tests passed!")


if __name__ == "__main__":
    main()
