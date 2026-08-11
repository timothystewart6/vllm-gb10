#!/usr/bin/env python3
"""Security tests for the release monitor artifact boundary."""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
MODULE_PATH = ROOT / "scripts" / "validate-monitor-update.py"
SPEC = importlib.util.spec_from_file_location("validate_monitor_update", MODULE_PATH)
VALIDATOR = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(VALIDATOR)

BASE_TEXT = (ROOT / "versions.env").read_text(encoding="utf-8")
BASE_VALUES = VALIDATOR.parse_versions_env(BASE_TEXT)

VALID_REPLACEMENTS = {
    "CUDA_BASE_DIGEST": "sha256:" + "1" * 64,
    "FLASHINFER_REF": "v9.8.7",
    "NCCL_REF": "v9.8.7-1",
    "NUMBA_VERSION": "9.8.7",
    "TILELANG_VERSION": "9.8.7",
    "TORCHAUDIO_VERSION": "9.8.7",
    "TORCHVISION_VERSION": "9.8.7",
    "TORCH_VERSION": "9.8.7",
    "TRITON_VERSION": "9.8.7",
    "TVM_FFI_VERSION": "9.8.7",
    "UV_VERSION": "9.8.7",
    "VLLM_REF": "v9.8.7",
}


def replace_value(text, key, value):
    old = f"{key}={BASE_VALUES[key]}"
    assert old in text
    return text.replace(old, f"{key}={value}", 1)


def assert_rejected(candidate, reason):
    try:
        VALIDATOR.validate_monitor_update(BASE_TEXT, candidate)
    except VALIDATOR.VersionsEnvError:
        return
    raise AssertionError(f"accepted unsafe monitor candidate: {reason}")


def test_every_monitor_key_is_individually_allowed():
    assert set(VALID_REPLACEMENTS) == VALIDATOR.ALLOWED_UPDATE_KEYS
    for key, value in VALID_REPLACEMENTS.items():
        candidate = replace_value(BASE_TEXT, key, value)
        assert VALIDATOR.validate_monitor_update(BASE_TEXT, candidate) == {key}


def test_multiple_approved_values_are_allowed_together():
    candidate = BASE_TEXT
    expected = {"VLLM_REF", "TORCH_VERSION", "UV_VERSION"}
    for key in expected:
        candidate = replace_value(candidate, key, VALID_REPLACEMENTS[key])
    assert VALIDATOR.validate_monitor_update(BASE_TEXT, candidate) == expected


def test_policy_covers_every_scripted_monitor_update():
    script = (ROOT / "scripts" / "check-updates.sh").read_text(encoding="utf-8")
    report_keys = set(
        re.findall(
            r'^\s*report\s+"[^"]+"\s+"([A-Z0-9_]+)"',
            script,
            re.MULTILINE,
        )
    )
    explicit_keys = set(re.findall(r'update_env\s+"([A-Z0-9_]+)"', script))
    assert report_keys | explicit_keys == VALIDATOR.ALLOWED_UPDATE_KEYS


def test_every_other_schema_key_is_rejected():
    disallowed = set(BASE_VALUES) - VALIDATOR.ALLOWED_UPDATE_KEYS
    assert disallowed
    for key in disallowed:
        if key.endswith("_COMMIT"):
            replacement = "1" * 40
        elif key == "GB10_BUILD":
            replacement = str(int(BASE_VALUES[key]) + 1)
        elif key == "CUDA_BASE_IMAGE":
            replacement = "nvidia/cuda:99.9.9-devel-ubuntu24.04"
        elif key.endswith("_VERSION"):
            replacement = "99.9.9"
        else:
            replacement = BASE_VALUES[key] + "x"
        candidate = replace_value(BASE_TEXT, key, replacement)
        assert_rejected(candidate, key)


def test_comment_addition_is_rejected():
    candidate = replace_value(BASE_TEXT, "UV_VERSION", "9.8.7")
    assert_rejected(candidate + "# injected comment\n", "comment addition")


def test_comment_edit_is_rejected():
    candidate = replace_value(BASE_TEXT, "UV_VERSION", "9.8.7")
    candidate = candidate.replace(
        "# versions.env - the authoritative source of truth for all build inputs.",
        "# modified by upstream",
    )
    assert_rejected(candidate, "comment edit")


def test_reordering_is_rejected():
    lines = replace_value(BASE_TEXT, "UV_VERSION", "9.8.7").splitlines(True)
    lines[0], lines[1] = lines[1], lines[0]
    assert_rejected("".join(lines), "line reordering")


def test_no_change_is_rejected():
    assert_rejected(BASE_TEXT, "no change")


def test_missing_key_is_rejected():
    candidate = replace_value(BASE_TEXT, "UV_VERSION", "9.8.7")
    candidate = candidate.replace(
        f"NCCL_COMMIT={BASE_VALUES['NCCL_COMMIT']}\n",
        "",
    )
    assert_rejected(candidate, "missing key")


def test_duplicate_key_is_rejected():
    candidate = replace_value(BASE_TEXT, "UV_VERSION", "9.8.7")
    candidate += "UV_VERSION=9.8.7\n"
    assert_rejected(candidate, "duplicate key")


def test_shell_and_python_payloads_are_rejected():
    payloads = (
        "$(touch /tmp/owned)",
        "9.8.7|e",
        "9.8.7';__import__('os').system('id')#",
        "9.8.7\nEVIL=value",
        "https://attacker.invalid/package.whl",
    )
    for payload in payloads:
        candidate = replace_value(BASE_TEXT, "UV_VERSION", payload)
        assert_rejected(candidate, payload)


def main():
    tests = [
        test_every_monitor_key_is_individually_allowed,
        test_multiple_approved_values_are_allowed_together,
        test_policy_covers_every_scripted_monitor_update,
        test_every_other_schema_key_is_rejected,
        test_comment_addition_is_rejected,
        test_comment_edit_is_rejected,
        test_reordering_is_rejected,
        test_no_change_is_rejected,
        test_missing_key_is_rejected,
        test_duplicate_key_is_rejected,
        test_shell_and_python_payloads_are_rejected,
    ]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"All {len(tests)} monitor update policy tests passed!")


if __name__ == "__main__":
    main()
