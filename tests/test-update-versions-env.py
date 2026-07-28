#!/usr/bin/env python3
"""Security and atomicity tests for update-versions-env.py."""

from __future__ import annotations

import importlib.util
import stat
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location(
    "update_versions_env",
    ROOT / "scripts" / "update-versions-env.py",
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

BASE_TEXT = (ROOT / "versions.env").read_text(encoding="utf-8")


def assert_rejected(updates, reason):
    try:
        MODULE.update_versions_text(BASE_TEXT, updates)
    except MODULE.VersionsEnvError:
        return
    raise AssertionError(f"accepted unsafe update: {reason}")


def test_single_update_preserves_all_other_bytes():
    candidate = MODULE.update_versions_text(BASE_TEXT, {"UV_VERSION": "9.8.7"})
    expected = BASE_TEXT.replace(
        "UV_VERSION=" + MODULE.parse_versions_env(BASE_TEXT)["UV_VERSION"],
        "UV_VERSION=9.8.7",
        1,
    )
    assert candidate == expected


def test_multiple_updates_are_one_valid_candidate():
    candidate = MODULE.update_versions_text(
        BASE_TEXT,
        {
            "UV_VERSION": "9.8.7",
            "RAY_VERSION": "9.8.7",
            "GB10_BUILD": "42",
            "VLLM_COMMIT": "1" * 40,
        },
    )
    values = MODULE.parse_versions_env(candidate)
    assert values["UV_VERSION"] == "9.8.7"
    assert values["RAY_VERSION"] == "9.8.7"
    assert values["GB10_BUILD"] == "42"
    assert values["VLLM_COMMIT"] == "1" * 40


def test_invalid_batch_does_not_modify_file():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "versions.env"
        path.write_text(BASE_TEXT, encoding="utf-8")
        before = path.read_bytes()
        try:
            MODULE.atomic_update(
                path,
                ["UV_VERSION=9.8.7", "RAY_VERSION=9.8.7|e"],
            )
        except MODULE.VersionsEnvError:
            pass
        else:
            raise AssertionError("accepted one invalid value in an update batch")
        assert path.read_bytes() == before
        assert list(path.parent.iterdir()) == [path]


def test_atomic_update_preserves_file_mode():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "versions.env"
        path.write_text(BASE_TEXT, encoding="utf-8")
        path.chmod(0o640)
        MODULE.atomic_update(path, ["UV_VERSION=9.8.7"])
        assert stat.S_IMODE(path.stat().st_mode) == 0o640


def test_assignment_parser_rejects_ambiguous_inputs():
    rejected = (
        [],
        ["UV_VERSION"],
        ["UV_VERSION=9.8.7", "UV_VERSION=9.8.8"],
    )
    for assignments in rejected:
        try:
            MODULE.parse_assignments(assignments)
        except MODULE.VersionsEnvError:
            continue
        raise AssertionError(f"accepted ambiguous assignments: {assignments}")


def test_unknown_and_missing_keys_are_rejected():
    assert_rejected({"UNKNOWN_VERSION": "1.2.3"}, "unknown key")
    assert_rejected({"": "1.2.3"}, "empty key")


def test_shell_python_sed_and_multiline_payloads_are_rejected():
    payloads = (
        "$(touch /tmp/owned)",
        "`touch /tmp/owned`",
        "9.8.7|e",
        "9.8.7|w /tmp/owned",
        "9.8.7';__import__('os').system('id')#",
        "9.8.7\nEVIL=value",
        "9.8.7\rEVIL=value",
        "${PATH}",
        "9.8.7;id",
        "9.8.7%0AATTACK=value",
        "9.8.7\u202eattack",
        "a" * 4097,
    )
    for payload in payloads:
        assert_rejected({"UV_VERSION": payload}, payload)


def test_type_specific_validation_still_applies():
    cases = (
        {"GB10_BUILD": "-1"},
        {"GB10_BUILD": "1000001"},
        {"VLLM_COMMIT": "1" * 39},
        {"VLLM_REF": "main"},
        {"CUDA_BASE_DIGEST": "sha256:" + "z" * 64},
        {"CUDA_BASE_IMAGE": "attacker.invalid/cuda:latest"},
        {"PYPI_INDEX_URL": "https://attacker.invalid/simple"},
    )
    for updates in cases:
        assert_rejected(updates, str(updates))


def test_symlink_target_is_rejected():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        target = root / "target.env"
        link = root / "versions.env"
        target.write_text(BASE_TEXT, encoding="utf-8")
        link.symlink_to(target)
        before = target.read_bytes()
        try:
            MODULE.atomic_update(link, ["UV_VERSION=9.8.7"])
        except MODULE.VersionsEnvError:
            pass
        else:
            raise AssertionError("updated versions.env through a symlink")
        assert target.read_bytes() == before


def test_atomic_replace_does_not_leave_temporary_files():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "versions.env"
        path.write_text(BASE_TEXT, encoding="utf-8")
        MODULE.atomic_update(path, ["UV_VERSION=9.8.7"])
        assert [entry.name for entry in path.parent.iterdir()] == ["versions.env"]


def main():
    tests = [
        test_single_update_preserves_all_other_bytes,
        test_multiple_updates_are_one_valid_candidate,
        test_invalid_batch_does_not_modify_file,
        test_atomic_update_preserves_file_mode,
        test_assignment_parser_rejects_ambiguous_inputs,
        test_unknown_and_missing_keys_are_rejected,
        test_shell_python_sed_and_multiline_payloads_are_rejected,
        test_type_specific_validation_still_applies,
        test_symlink_target_is_rejected,
        test_atomic_replace_does_not_leave_temporary_files,
    ]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"All {len(tests)} atomic versions update tests passed!")


if __name__ == "__main__":
    main()
