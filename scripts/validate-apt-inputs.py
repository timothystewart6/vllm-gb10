#!/usr/bin/env python3
"""Validate declarative apt inputs before using them on a trusted runner."""

from __future__ import annotations

import argparse
import re
from pathlib import Path


SOURCE_RE = re.compile(
    r"^deb https://snapshot\.ubuntu\.com/ubuntu/"
    r"([0-9]{8}T000000Z)/ noble(?:-updates|-security)? "
    r"main restricted universe multiverse$"
)
PACKAGE_RE = re.compile(
    r"^[a-z0-9][a-z0-9+.-]*(?::[a-z0-9]+)?"
    r"(?:=[A-Za-z0-9.+:~_-]+)?$"
)
EXPECTED_SUITES = {"noble", "noble-updates", "noble-security"}


def active_lines(path: Path) -> list[str]:
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def validate_sources(path: Path) -> None:
    lines = active_lines(path)
    if len(lines) != 3:
        raise ValueError("apt sources must contain exactly three Ubuntu suites")
    timestamps = set()
    suites = set()
    for line in lines:
        match = SOURCE_RE.fullmatch(line)
        if not match:
            raise ValueError(f"unsafe apt source: {line!r}")
        timestamps.add(match.group(1))
        suites.add(line.split()[2])
    if len(timestamps) != 1 or suites != EXPECTED_SUITES:
        raise ValueError("apt sources must use one snapshot for all expected suites")


def validate_packages(path: Path) -> None:
    lines = active_lines(path)
    if not lines:
        raise ValueError("apt package list must not be empty")
    for line in lines:
        if not PACKAGE_RE.fullmatch(line):
            raise ValueError(f"unsafe apt package entry: {line!r}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sources", type=Path)
    parser.add_argument("packages", type=Path)
    args = parser.parse_args()
    try:
        validate_sources(args.sources)
        validate_packages(args.packages)
    except (OSError, UnicodeError, ValueError) as error:
        parser.error(str(error))
    print("Validated apt inputs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
