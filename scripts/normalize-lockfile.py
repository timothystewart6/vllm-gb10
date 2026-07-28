#!/usr/bin/env python3
"""Replace one generated lockfile path with a stable label."""

from __future__ import annotations

import argparse
from pathlib import Path


def normalize_lockfile(
    lockfile: Path, generated_input: str, stable_input: str
) -> None:
    content = lockfile.read_text(encoding="utf-8")
    if generated_input not in content:
        raise ValueError(
            f"{lockfile} does not reference generated input {generated_input!r}"
        )
    lockfile.write_text(
        content.replace(generated_input, stable_input),
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("lockfile", type=Path)
    parser.add_argument("generated_input")
    parser.add_argument("stable_input")
    args = parser.parse_args()
    try:
        normalize_lockfile(
            args.lockfile,
            args.generated_input,
            args.stable_input,
        )
    except (OSError, UnicodeError, ValueError) as error:
        parser.error(str(error))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
