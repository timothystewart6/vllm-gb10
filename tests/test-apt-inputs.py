#!/usr/bin/env python3
"""Security tests for declarative apt inputs."""

import importlib.util
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "validate_apt_inputs", ROOT / "scripts" / "validate-apt-inputs.py"
)
assert SPEC and SPEC.loader
VALIDATOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VALIDATOR)


def expect_rejected(function, path, reason):
    try:
        function(path)
    except ValueError:
        return
    raise AssertionError(f"accepted unsafe apt input: {reason}")


def main():
    sources = ROOT / "locks" / "apt-sources.list"
    packages = ROOT / "locks" / "apt-packages.txt"
    VALIDATOR.validate_sources(sources)
    VALIDATOR.validate_packages(packages)

    with tempfile.TemporaryDirectory() as directory:
        temporary = Path(directory) / "input.txt"
        temporary.write_text(
            sources.read_text().replace(
                "https://snapshot.ubuntu.com/ubuntu/",
                "https://attacker.example/ubuntu/",
            )
        )
        expect_rejected(VALIDATOR.validate_sources, temporary, "unapproved host")

        temporary.write_text(
            sources.read_text().replace(
                "20260714T000000Z", "20260715T123456Z"
            )
        )
        expect_rejected(VALIDATOR.validate_sources, temporary, "mutable timestamp")

        temporary.write_text("bash\n--option\n")
        expect_rejected(VALIDATOR.validate_packages, temporary, "apt option")

        temporary.write_text("bash\npkg;touch-pwned\n")
        expect_rejected(VALIDATOR.validate_packages, temporary, "shell syntax")

    print("All apt input security tests passed!")


if __name__ == "__main__":
    main()
