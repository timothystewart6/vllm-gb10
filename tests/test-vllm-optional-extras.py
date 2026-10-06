#!/usr/bin/env python3
"""Tests for the fail-closed vLLM optional-extra metadata parser."""

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "vllm_optional_extras", ROOT / "scripts" / "vllm_optional_extras.py"
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def source(requirements='["b12x==1.3.0"]'):
    return f"setup(name='vllm', extras_require={{'b12x': {requirements}}})\n"


def expect_rejected(value, reason):
    try:
        MODULE.b12x_version(value)
    except MODULE.OptionalExtraError:
        return
    raise AssertionError(f"accepted unsupported B12X metadata: {reason}")


def main():
    assert MODULE.b12x_version(source()) == "1.3.0"
    assert MODULE.b12x_version(source('["b12x==1.4.0"]')) == "1.4.0"
    expect_rejected(source("[]"), "empty extra")
    expect_rejected(source('["b12x>=1.3.0"]'), "range")
    expect_rejected(source('["b12x==1.3.0", "other==1.0.0"]'), "additional dependency")
    expect_rejected("setup(name='vllm', extras_require={})", "missing extra")
    expect_rejected("extras = {'b12x': ['b12x==1.3.0']}\nsetup(extras_require=extras)", "dynamic extra")
    expect_rejected(
        "setup(extras_require={'b12x': ['b12x==1.3.0'], "
        "'b12x': ['b12x==1.4.0']})",
        "duplicate extra",
    )
    expect_rejected("not python", "malformed source")
    print("All vLLM optional-extra tests passed!")


if __name__ == "__main__":
    main()
