#!/usr/bin/env python3
"""Extract approved vLLM optional-extra pins without executing upstream code."""

from __future__ import annotations

import argparse
import ast
import re
from pathlib import Path


VERSION_RE = re.compile(r"^[0-9]+(?:\.[0-9]+)+(?:[.-][A-Za-z0-9]+)*$")


class OptionalExtraError(ValueError):
    """Raised when upstream optional-extra metadata is not policy-compatible."""


def _setup_extras_require(source: str) -> dict[str, list[str]]:
    try:
        tree = ast.parse(source)
    except SyntaxError as error:
        raise OptionalExtraError(f"invalid vLLM packaging source: {error.msg}") from error

    matches = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Name) and node.func.id == "setup"):
            continue
        for keyword in node.keywords:
            if keyword.arg == "extras_require":
                matches.append(keyword.value)

    if len(matches) != 1 or not isinstance(matches[0], ast.Dict):
        raise OptionalExtraError(
            "vLLM setup.py must contain one literal extras_require mapping"
        )

    extras: dict[str, list[str]] = {}
    for key, value in zip(matches[0].keys, matches[0].values, strict=True):
        if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
            raise OptionalExtraError("vLLM extras_require keys must be literal strings")
        if key.value in extras:
            raise OptionalExtraError(
                f"vLLM extras_require contains duplicate extra {key.value!r}"
            )
        if not isinstance(value, ast.List) or not all(
            isinstance(item, ast.Constant) and isinstance(item.value, str)
            for item in value.elts
        ):
            raise OptionalExtraError(
                f"vLLM extra {key.value!r} must be a literal requirement list"
            )
        extras[key.value] = [item.value for item in value.elts]
    return extras


def b12x_version(source: str) -> str:
    """Return the exact B12X package pin declared by vLLM's b12x extra."""
    requirements = _setup_extras_require(source).get("b12x")
    if requirements is None:
        raise OptionalExtraError("vLLM does not declare a b12x optional extra")
    if len(requirements) != 1:
        raise OptionalExtraError(
            "vLLM b12x extra must contain exactly one direct requirement"
        )
    match = re.fullmatch(r"b12x==([^\s;@\[\],]+)", requirements[0])
    if not match or not VERSION_RE.fullmatch(match.group(1)):
        raise OptionalExtraError(
            "vLLM b12x extra must pin b12x to one exact numeric version"
        )
    return match.group(1)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    try:
        print(b12x_version(args.path.read_text(encoding="utf-8")))
    except (OSError, UnicodeError, OptionalExtraError) as error:
        parser.error(str(error))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
