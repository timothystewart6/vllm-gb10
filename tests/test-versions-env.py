#!/usr/bin/env python3
"""Security tests for strict versions.env parsing."""

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "versions_env", ROOT / "scripts" / "versions_env.py"
)
assert SPEC and SPEC.loader
VERSIONS_ENV = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VERSIONS_ENV)


def expect_rejected(text, reason):
    try:
        VERSIONS_ENV.parse_versions_env(text)
    except VERSIONS_ENV.VersionsEnvError:
        return
    raise AssertionError(f"accepted unsafe versions.env: {reason}")


def replace_value(text, key, value):
    current = next(line for line in text.splitlines() if line.startswith(f"{key}="))
    return text.replace(current, f"{key}={value}")


def main():
    valid = (ROOT / "versions.env").read_text()
    parsed = VERSIONS_ENV.parse_versions_env(valid)
    assert parsed["VLLM_REF"].startswith("v")
    uv_line = f"UV_VERSION={parsed['UV_VERSION']}"
    expect_rejected(
        valid.replace(uv_line, "UV_VERSION=$(touch /tmp/pwned)"),
        "command substitution",
    )
    expect_rejected(
        valid.replace(uv_line, "UV_VERSION=0.11.32;id"),
        "shell command separator",
    )
    expect_rejected(
        valid.replace(uv_line, "UV_VERSION='0.11.32'"),
        "shell quoting",
    )
    expect_rejected(
        valid.replace(uv_line, "UV_VERSION=`touch /tmp/pwned`"),
        "backtick substitution",
    )
    expect_rejected(
        valid.replace(uv_line, "UV_VERSION=${PATH}"),
        "parameter expansion",
    )
    expect_rejected(
        valid.replace(uv_line, "UV_VERSION=ok%0AATTACK=1"),
        "encoded environment newline",
    )
    expect_rejected(
        valid.replace(uv_line, "UV_VERSION=ok\u202eattack"),
        "Unicode direction control",
    )
    expect_rejected(
        valid.replace(uv_line, "UV_VERSION=ok\rATTACK=1"),
        "embedded carriage return",
    )
    expect_rejected(
        valid.replace(uv_line, f"UV_VERSION={'a' * 4097}"),
        "unreasonably long value",
    )
    expect_rejected(
        valid.replace(uv_line, f" {uv_line}"),
        "leading whitespace",
    )
    expect_rejected(valid + f"\n{uv_line}\n", "duplicate key")
    expect_rejected(valid + "\nUNREVIEWED_INPUT=1\n", "unknown key")
    expect_rejected(valid.replace(f"{uv_line}\n", ""), "missing key")
    expect_rejected(
        replace_value(valid, "VLLM_REPO", "https://attacker.example/vllm.git"),
        "unapproved source repository",
    )
    expect_rejected(
        replace_value(valid, "PYPI_INDEX_URL", "https://attacker.example/simple"),
        "unapproved package index",
    )
    expect_rejected(
        replace_value(valid, "CUDA_BASE_IMAGE", "attacker/cuda:13.2.0"),
        "unapproved container image",
    )
    expect_rejected(
        replace_value(valid, "VLLM_COMMIT", "deadbeef"),
        "short commit SHA",
    )
    expect_rejected(
        replace_value(valid, "CUDA_BASE_DIGEST", "sha256:deadbeef"),
        "short image digest",
    )
    expect_rejected(
        replace_value(valid, "GB10_BUILD", "-1"),
        "negative build number",
    )
    expect_rejected(
        replace_value(valid, "TORCH_CUDA_ARCH_LIST", "12.1a+9.0"),
        "unexpected GPU architecture",
    )
    VERSIONS_ENV.parse_versions_env(
        replace_value(valid, "FLASHINFER_REF", "v0.6.16.post3")
    )
    expect_rejected(
        replace_value(valid, "FLASHINFER_REF", "v0.6.17rc5"),
        "pre-release ref",
    )
    print("All versions.env security tests passed!")


if __name__ == "__main__":
    main()
