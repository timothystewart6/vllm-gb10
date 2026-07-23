#!/usr/bin/env bash
# scripts/generate-pr-body.sh
#
# Generates a markdown PR body for the automated dependency bump PR.
# Compares the working tree against origin/main and lists all changes:
#   - versions.env variable changes (with human-readable labels)
#   - lockfile hash changes (apt, python-bootstrap, python-build, python-runtime)
#   - apt snapshot date changes (when applicable)
#
# Uses the shared versions_diff.py module (same labels and formatting as
# generate-release-notes.sh).
#
# Usage:
#   scripts/generate-pr-body.sh > /tmp/pr-body.md
#
# Exit code: 0 on success, 1 if no changes detected (body still produced).

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${REPO_ROOT}"

# Determine what to diff against (origin/main or HEAD~1 as fallback)
BASEREF="origin/main"
if ! git rev-parse --verify "${BASEREF}" > /dev/null 2>&1; then
  BASEREF="HEAD~1"
fi

# Check if there are any changes to versions.env or lockfiles
if git diff --exit-code "${BASEREF}" -- versions.env locks/ > /dev/null 2>&1; then
  printf 'Automated upstream release monitor detected no changes.\n'
  exit 1
fi

export BASEREF REPO_ROOT
python3 - <<'PYEOF'
import os
import subprocess
import sys
from pathlib import Path

repo_root = os.environ["REPO_ROOT"]
sys.path.insert(0, os.path.join(repo_root, "scripts"))
from versions_diff import (
    COMPONENT_LABELS,
    LOCKFILES,
    diff_env_dicts,
    extract_apt_snapshot_date,
    file_sha256,
    format_changes_with_lockfiles,
    parse_versions_env,
)

baseref = os.environ["BASEREF"]

# 1. Compare the base versions.env with the current working-tree file.
old_versions = subprocess.run(
    ["git", "show", f"{baseref}:versions.env"],
    capture_output=True, text=True, check=True,
).stdout
new_versions = Path(repo_root, "versions.env").read_text()
changes = diff_env_dicts(
    parse_versions_env(old_versions),
    parse_versions_env(new_versions),
)

# 2. Check lockfile changes
for lock_path, _lock_label in LOCKFILES:
    old_result = subprocess.run(
        ["git", "show", f"{baseref}:{lock_path}"],
        capture_output=True, text=True,
    )
    new_path = Path(repo_root, lock_path)
    old_content = old_result.stdout if old_result.returncode == 0 else None
    new_content = new_path.read_text() if new_path.is_file() else None

    if old_content is not None and new_content is not None:
        old_hash = file_sha256(old_content)
        new_hash = file_sha256(new_content)
        if old_hash != new_hash:
            if lock_path == "locks/apt-sources.list":
                old_date = extract_apt_snapshot_date(old_content)
                new_date = extract_apt_snapshot_date(new_content)
                if old_date and new_date and old_date != new_date:
                    changes[f"LOCK:{lock_path}"] = (old_date, new_date)
                else:
                    changes[f"LOCK:{lock_path}"] = (f"sha256:{old_hash}", f"sha256:{new_hash}")
            else:
                changes[f"LOCK:{lock_path}"] = (f"sha256:{old_hash}", f"sha256:{new_hash}")
    elif old_content != new_content:
        old_value = f"sha256:{file_sha256(old_content)}" if old_content is not None else "(missing)"
        new_value = f"sha256:{file_sha256(new_content)}" if new_content is not None else "(missing)"
        changes[f"LOCK:{lock_path}"] = (old_value, new_value)

# Separate component changes from lockfile changes
lock_keys = {k for k in changes if k.startswith("LOCK:")}
component_changes = {k: v for k, v in changes.items() if k not in lock_keys and k != "GB10_BUILD"}
lock_changes = {k: v for k, v in changes.items() if k in lock_keys}

# Format both via shared function
change_lines = format_changes_with_lockfiles(component_changes, lock_changes, COMPONENT_LABELS)

lines = ["### Changes in this PR", ""]
if change_lines:
    lines.extend(change_lines)
else:
    lines.append("- No component changes detected (unexpected)")

body = "\n".join(lines) + "\n"
print(body, end="")
PYEOF
