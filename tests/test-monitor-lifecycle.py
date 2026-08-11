#!/usr/bin/env python3
"""End-to-end contracts for monitored inputs after a dependency PR is created."""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MONITOR = load_module(
    "validate_monitor_update",
    "scripts/validate-monitor-update.py",
)
VERSIONS = load_module("versions_env", "scripts/versions_env.py")
BUILD = load_module("compute_gb10_build", "scripts/compute-gb10-build.py")
DUPLICATES = load_module(
    "check_duplicate_pr",
    "scripts/check-duplicate-pr.py",
)
DIFF = load_module("versions_diff", "scripts/versions_diff.py")

BASE_TEXT = (ROOT / "versions.env").read_text(encoding="utf-8")
BASE_VALUES = VERSIONS.parse_versions_env(BASE_TEXT)


def read(path):
    return (ROOT / path).read_text(encoding="utf-8")


def replacement_for(key):
    if key == "CUDA_BASE_DIGEST":
        return "sha256:" + "1" * 64
    if key == "NCCL_REF":
        return "v99.88.77-1"
    if key.endswith("_REF"):
        return "v99.88.77"
    return "99.88.77"


def candidate_for(key):
    old = f"{key}={BASE_VALUES[key]}"
    assert old in BASE_TEXT
    return BASE_TEXT.replace(old, f"{key}={replacement_for(key)}", 1)


def test_every_monitored_change_survives_pr_contracts():
    for key in MONITOR.ALLOWED_UPDATE_KEYS:
        candidate = candidate_for(key)
        assert MONITOR.validate_monitor_update(BASE_TEXT, candidate) == {key}
        candidate_values = VERSIONS.parse_versions_env(candidate)
        changes = DIFF.diff_env_dicts(BASE_VALUES, candidate_values)
        assert changes == {
            key: (BASE_VALUES[key], candidate_values[key])
        }
        assert key in DIFF.COMPONENT_LABELS
        if key != "CUDA_BASE_DIGEST":
            assert key in {name for name, _ in DIFF.COMPONENTS}
        assert DIFF.format_change_lines(changes) == [
            f"- **{DIFF.COMPONENT_LABELS[key]}**: "
            f"{BASE_VALUES[key]} -> {candidate_values[key]}"
        ]
        unified = (
            f"-{key}={BASE_VALUES[key]}\n"
            f"+{key}={candidate_values[key]}\n"
        )
        assert DUPLICATES.diff_has_substantive_changes(unified)


def test_every_monitored_change_has_build_revision_behavior():
    assert "VLLM_REF" in VERSIONS.BUILD_RESET_INPUT_KEYS
    ordinary = MONITOR.ALLOWED_UPDATE_KEYS - {"VLLM_REF"}
    assert ordinary <= VERSIONS.BUILD_INCREMENT_INPUT_KEYS

    for key in MONITOR.ALLOWED_UPDATE_KEYS:
        if key == "VLLM_REF":
            result = BUILD.compute_build_number(
                old_vllm_ref=BASE_VALUES["VLLM_REF"],
                new_vllm_ref=replacement_for(key),
                old_vllm_commit=BASE_VALUES["VLLM_COMMIT"],
                reviewed_vllm_commit=BASE_VALUES["VLLM_COMMIT"],
                resolved_vllm_commit="1" * 40,
                old_build=int(BASE_VALUES["GB10_BUILD"]),
                other_input_changed=True,
            )
            assert result == 0
        else:
            result = BUILD.compute_build_number(
                old_vllm_ref=BASE_VALUES["VLLM_REF"],
                new_vllm_ref=BASE_VALUES["VLLM_REF"],
                old_vllm_commit=BASE_VALUES["VLLM_COMMIT"],
                reviewed_vllm_commit=BASE_VALUES["VLLM_COMMIT"],
                resolved_vllm_commit=BASE_VALUES["VLLM_COMMIT"],
                old_build=int(BASE_VALUES["GB10_BUILD"]),
                other_input_changed=True,
            )
            assert result == int(BASE_VALUES["GB10_BUILD"]) + 1


def test_every_monitored_change_reaches_build_and_release_metadata():
    build_args = read("scripts/build-args.sh")
    metadata = read("scripts/render-metadata.sh")
    for key in MONITOR.ALLOWED_UPDATE_KEYS:
        assert f"_arg {key}" in build_args
        assert f"${{{key}}}" in metadata


def test_uv_update_reaches_bootstrap_lock_and_advances_build():
    bump = read("scripts/bump.sh")
    assert "UV_VERSION" in VERSIONS.BUILD_INCREMENT_INPUT_KEYS
    assert "uv==%s" in bump
    assert '"${UV_VERSION}"' in bump
    assert BUILD.compute_build_number(
        old_vllm_ref=BASE_VALUES["VLLM_REF"],
        new_vllm_ref=BASE_VALUES["VLLM_REF"],
        old_vllm_commit=BASE_VALUES["VLLM_COMMIT"],
        reviewed_vllm_commit=BASE_VALUES["VLLM_COMMIT"],
        resolved_vllm_commit=BASE_VALUES["VLLM_COMMIT"],
        old_build=3,
        other_input_changed=True,
    ) == 4


def test_upstream_values_use_the_atomic_validated_writer():
    check_updates = read("scripts/check-updates.sh")
    bump = read("scripts/bump.sh")
    helper = "scripts/update-versions-env.py"
    assert helper in check_updates
    assert helper in bump
    assert not re.search(r"^\s*sed -i\b", check_updates, re.MULTILINE)
    assert not re.search(r"^\s*sed -i\b", bump, re.MULTILINE)

    resolved_outputs = {
        "CUDA_BASE_DIGEST",
        "GB10_BUILD",
        "NCCL_COMMIT",
        "VLLM_COMMIT",
        "FLASHINFER_COMMIT",
        "RAY_VERSION",
    }
    for key in resolved_outputs:
        assert f'"{key}=${{{key}}}"' in bump


def test_workflows_preserve_generated_pr_handoff_order():
    monitor = read(".github/workflows/monitor-upstream-releases.yaml")
    create_job = monitor.split("\n  create-pr:", 1)[1]
    assert create_job.index("scripts/validate-monitor-update.py") < (
        create_job.index("secrets.RELEASE_MONITOR_PAT")
    )
    assert "base: main" in create_job
    assert "add-paths: versions.env" in create_job

    bump = read(".github/workflows/run-bump.yaml")
    ordered_bump_controls = (
        "Verify integration branch still points to approved SHA",
        "Import reviewed declarative inputs",
        "Validate imported inputs with trusted validators",
        "Run trusted bump.sh",
        "Preserve generated outputs",
        "Apply outputs to exact approved commit",
        "Validate generated changes",
        "Commit generated files",
        "Recheck branch and push",
    )
    positions = [bump.index(control) for control in ordered_bump_controls]
    assert positions == sorted(positions)

    build = read(".github/workflows/build-image.yaml")
    trigger = build.split("\npermissions:", 1)[0]
    assert "- versions.env" in trigger
    assert "- tests/**" not in trigger
    for required_path in (
        "- Dockerfile",
        "- locks/**",
        "- scripts/render-metadata.sh",
        "- scripts/build-args.sh",
        "- checksums/**",
    ):
        assert required_path in trigger
    assert build.index("Validate versions.env") < build.index(
        "Source versions.env into GITHUB_ENV"
    )


def test_lifecycle_guidance_is_visible_to_agents_and_humans():
    anchor = "automated-release-monitor-lifecycle"
    assert anchor in read("AGENTS.md")
    assert "## Automated release monitor lifecycle" in read("CONTRIBUTING.md")
    assert anchor in read("docs/contributor-ci-security.md")


def main():
    tests = [
        test_every_monitored_change_survives_pr_contracts,
        test_every_monitored_change_has_build_revision_behavior,
        test_every_monitored_change_reaches_build_and_release_metadata,
        test_uv_update_reaches_bootstrap_lock_and_advances_build,
        test_upstream_values_use_the_atomic_validated_writer,
        test_workflows_preserve_generated_pr_handoff_order,
        test_lifecycle_guidance_is_visible_to_agents_and_humans,
    ]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"All {len(tests)} monitor lifecycle tests passed!")


if __name__ == "__main__":
    main()
