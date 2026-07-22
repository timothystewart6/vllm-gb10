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

export REPO_ROOT
python3 - <<'PYEOF'
import os, subprocess, sys, hashlib, re, textwrap

repo_root = os.environ["REPO_ROOT"]
sys.path.insert(0, os.path.join(repo_root, "scripts"))
from versions_diff import COMPONENT_LABELS, diff_from_git_diff, format_changes_with_lockfiles

baseref = os.environ.get("BASEREF", "origin/main")

# 1. Parse versions.env diff
diff = subprocess.run(
    ["git", "diff", baseref, "--", "versions.env"],
    capture_output=True, text=True, check=True,
).stdout
changes = diff_from_git_diff(diff)

# 2. Check lockfile changes
lockfiles = [
    ("locks/apt-packages.txt", "apt packages"),
    ("locks/apt-sources.list", "apt snapshot"),
    ("locks/python-bootstrap.txt", "python bootstrap lock"),
    ("locks/python-build.txt", "python build lock"),
    ("locks/python-runtime.txt", "python runtime lock"),
]

for lock_path, lock_label in lockfiles:
    old_result = subprocess.run(
        ["git", "show", f"{baseref}:{lock_path}"],
        capture_output=True, text=True,
    )
    new_result = subprocess.run(
        ["git", "show", f"HEAD:{lock_path}"],
        capture_output=True, text=True,
    )
    if old_result.returncode == 0 and new_result.returncode == 0 and old_result.stdout and new_result.stdout:
        old_hash = hashlib.sha256(old_result.stdout.encode()).hexdigest()[:12]
        new_hash = hashlib.sha256(new_result.stdout.encode()).hexdigest()[:12]
        if old_hash != new_hash:
            # For apt-sources.list, show the snapshot date when it differs
            if lock_path == "locks/apt-sources.list":
                m_old = re.search(r"snapshot\.ubuntu\.com/ubuntu/(\d{8}T\d{6}Z)", old_result.stdout)
                m_new = re.search(r"snapshot\.ubuntu\.com/ubuntu/(\d{8}T\d{6}Z)", new_result.stdout)
                if m_old and m_new and m_old.group(1) != m_new.group(1):
                    changes[f"LOCK:{lock_path}"] = (m_old.group(1), m_new.group(1))
                else:
                    changes[f"LOCK:{lock_path}"] = (f"sha256:{old_hash}", f"sha256:{new_hash}")
            else:
                changes[f"LOCK:{lock_path}"] = (f"sha256:{old_hash}", f"sha256:{new_hash}")

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
