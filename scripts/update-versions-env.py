#!/usr/bin/env python3
"""Atomically update existing versions.env values as validated data."""

from __future__ import annotations

import argparse
import os
import stat
import tempfile
from pathlib import Path

from versions_env import VersionsEnvError, parse_versions_env


def parse_assignments(assignments: list[str]) -> dict[str, str]:
    updates = {}
    for assignment in assignments:
        if "=" not in assignment:
            raise VersionsEnvError(
                f"expected KEY=value assignment, got {assignment!r}"
            )
        key, value = assignment.split("=", 1)
        if key in updates:
            raise VersionsEnvError(f"duplicate update for {key}")
        updates[key] = value
    if not updates:
        raise VersionsEnvError("at least one KEY=value update is required")
    return updates


def update_versions_text(text: str, updates: dict[str, str]) -> str:
    current_values = parse_versions_env(text)
    unknown = updates.keys() - current_values.keys()
    if unknown:
        raise VersionsEnvError(
            "cannot update missing keys: " + ", ".join(sorted(unknown))
        )

    rendered = []
    replaced = set()
    for raw_line in text.splitlines(keepends=True):
        content = raw_line.rstrip("\r\n")
        line_ending = raw_line[len(content):]
        if "=" in content:
            key = content.split("=", 1)[0]
            if key in updates:
                raw_line = f"{key}={updates[key]}{line_ending}"
                replaced.add(key)
        rendered.append(raw_line)

    missing = updates.keys() - replaced
    if missing:
        raise VersionsEnvError(
            "could not replace keys: " + ", ".join(sorted(missing))
        )
    candidate = "".join(rendered)
    parse_versions_env(candidate)
    return candidate


def atomic_update(path: Path, assignments: list[str]) -> None:
    if path.is_symlink():
        raise VersionsEnvError("refusing to replace a symlink")
    text = path.read_text(encoding="utf-8")
    candidate = update_versions_text(text, parse_assignments(assignments))
    mode = stat.S_IMODE(path.stat().st_mode)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(candidate)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(mode)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    parser.add_argument("assignment", nargs="+")
    args = parser.parse_args()
    try:
        atomic_update(args.path, args.assignment)
    except (OSError, UnicodeError, VersionsEnvError) as error:
        parser.error(str(error))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
