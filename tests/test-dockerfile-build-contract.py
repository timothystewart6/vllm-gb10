#!/usr/bin/env python3
"""Regression tests for Dockerfile build-environment contracts."""

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def test_flashinfer_no_isolation_builds_use_system_python():
    """FlashInfer source builds must use the prepared system environment."""
    dockerfile = (ROOT / "Dockerfile").read_text()
    match = re.search(
        r"^FROM base AS flashinfer-builder$(.*?)(?=^FROM )",
        dockerfile,
        re.MULTILINE | re.DOTALL,
    )
    assert match, "flashinfer-builder stage not found"

    stage = match.group(1)
    pinned = "uv build --python /usr/bin/python3 --no-build-isolation"

    assert stage.count(pinned) == 3
    assert "uv build --no-build-isolation" not in stage


if __name__ == "__main__":
    test_flashinfer_no_isolation_builds_use_system_python()
    print("PASS: FlashInfer builds use the prepared system Python")
