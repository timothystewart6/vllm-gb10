#!/usr/bin/env python3
"""Reject unsafe or unpinned Python lockfile entries."""

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LOCKFILES = (
    ROOT / "locks" / "python-bootstrap.txt",
    ROOT / "locks" / "python-build.txt",
    ROOT / "locks" / "python-runtime.txt",
)
PACKAGE = re.compile(
    r"^[A-Za-z0-9_.-]+(?:\[[A-Za-z0-9_,.-]+\])?==[^\s\\]+(?:\s+\\)?$"
)
HASH = re.compile(r"--hash=sha256:[0-9a-f]{64}")
FORBIDDEN = re.compile(
    r"(^|\s)(?:-e|--editable|--index-url|--extra-index-url|--find-links|"
    r"git\+|file:|https?://)|\s@\s"
)


def active_lines(content):
    return [
        line for line in content.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def requirement_blocks(content):
    blocks = []
    current = []
    for line in active_lines(content):
        if line and not line[0].isspace():
            if current:
                blocks.append(current)
            current = [line]
        else:
            current.append(line)
    if current:
        blocks.append(current)
    return blocks


def main():
    for path in LOCKFILES:
        content = path.read_text(encoding="utf-8")
        assert not FORBIDDEN.search("\n".join(active_lines(content))), path
        blocks = requirement_blocks(content)
        assert blocks, f"{path}: no requirements found"
        for block in blocks:
            requirement = block[0]
            assert PACKAGE.fullmatch(requirement), (
                f"{path}: requirement is not exactly pinned: {requirement}"
            )
            assert HASH.search("\n".join(block)), (
                f"{path}: requirement has no SHA-256 hash: {requirement}"
            )
    print("All lockfile security tests passed!")


if __name__ == "__main__":
    main()
