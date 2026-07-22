#!/usr/bin/env python3
"""
Check if an open deps/bump PR already exists with identical versions.env changes.

Returns exit code 0 (skip) or 1 (proceed) and prints a user-facing message.

Usage:
  scripts/check-duplicate-pr.py

Environment:
  GH_TOKEN: GitHub token for gh CLI authentication

Exit codes:
  0 - Skip: an existing open PR already has the same versions.env changes
      (or no changes to versions.env in working tree)
  1 - Proceed: no matching PR found, caller should create a new one
"""

import json
import subprocess
import sys


def run_git(args, check=True):
    """Run a git command and return the result."""
    return subprocess.run(
        ["git"] + args,
        capture_output=True,
        text=True,
        check=check,
    )


def run_gh(args, check=True):
    """Run a gh command and return the result."""
    return subprocess.run(
        ["gh"] + args,
        capture_output=True,
        text=True,
        check=check,
    )


def check_for_duplicate():
    """Check for existing PRs with matching versions.env, return True if duplicate."""
    # First check if versions.env actually has uncommitted changes
    diff_result = run_git(
        ["diff", "--exit-code", "versions.env"], check=False
    )
    if diff_result.returncode == 0:
        # No changes to versions.env in working tree -- nothing to PR
        print("No changes to versions.env in working tree")
        return True

    # Find all open deps/bump PRs by this actor
    result = run_gh(
        [
            "pr", "list",
            "--state", "OPEN",
            "--author", "@me",
            "--search", "deps/bump",
            "--json", "number,headRefName",
            "--jq", ".[]",
        ],
        check=False,
    )
    prs_text = result.stdout.strip()
    if not prs_text:
        print("No open deps/bump PRs by this actor")
        return False

    prs = json.loads(prs_text)
    if not isinstance(prs, list):
        prs = [prs]

    for pr in prs:
        number = pr["number"]
        branch = pr["headRefName"]
        print(f"Checking PR #{number} ({branch})...")

        # Fetch the branch
        run_git(["fetch", "origin", branch])

        # Compare versions.env between the PR branch and the working tree
        r = run_git(
            ["diff", "--exit-code", f"origin/{branch}", "--", "versions.env"],
            check=False,
        )
        if r.returncode == 0:
            print(f"PR #{number} has matching versions.env -- skipping duplicate")
            return True
        else:
            print(f"PR #{number} has different versions.env -- not a match")

    print("No matching PR found -- will create new one")
    return False


if __name__ == "__main__":
    should_skip = check_for_duplicate()
    sys.exit(0 if should_skip else 1)
