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
        valid.replace(uv_line, f" {uv_line}"),
        "leading whitespace",
    )
    expect_rejected(valid + f"\n{uv_line}\n", "duplicate key")
    expect_rejected(valid + "\nUNREVIEWED_INPUT=1\n", "unknown key")
    expect_rejected(valid.replace(f"{uv_line}\n", ""), "missing key")
    print("All versions.env security tests passed!")


if __name__ == "__main__":
    main()
