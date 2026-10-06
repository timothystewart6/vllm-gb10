#!/usr/bin/env python3
"""Regression tests for B12X declarative input boundaries."""

import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SHA = "0123456789abcdef0123456789abcdef01234567"


def run(command, env):
    result = subprocess.run(
        command,
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"Command failed ({result.returncode}): {command}\n{result.stderr}"
        )
    return result.stdout


def main():
    env = os.environ.copy()
    env.update(
        {
            "B12X_VERSION": "99.99.99",
            "SOURCE_DATE_EPOCH": "0",
            "REPO_COMMIT": SHA,
            "TAG": "v0.31.0-gb10.0",
            "GITHUB_SHA": SHA,
        }
    )

    build_args = run(["bash", "scripts/build-args.sh"], env)
    assert "--build-arg B12X_VERSION=1.3.0" in build_args
    assert "99.99.99" not in build_args

    metadata = run(["bash", "scripts/render-metadata.sh"], env)
    assert '\n  b12x: "1.3.0"' in metadata
    assert "99.99.99" not in metadata

    release_notes = run(["bash", "scripts/generate-release-notes.sh"], env)
    assert "| **B12X** | 1.3.0 | - |" in release_notes
    assert "99.99.99" not in release_notes

    print("PASS: declared B12X_VERSION overrides inherited values")


if __name__ == "__main__":
    main()
