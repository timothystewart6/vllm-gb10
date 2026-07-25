#!/usr/bin/env python3
"""
Check if an open deps/bump PR already exists with identical versions.env changes.

Returns exit code 0 (skip), 1 (proceed), or 2 (error) and prints a
user-facing message.

Usage:
  scripts/check-duplicate-pr.py

Environment:
  GH_TOKEN: GitHub token for gh CLI authentication

Exit codes:
  0 - Skip: an existing open PR already has the same versions.env changes
      (or no changes to versions.env in working tree)
  1 - Proceed: no matching PR found, caller should create a new one
  2 - Error: the duplicate check could not be completed reliably
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


def get_versions_diff(ref=None, base=None):
    """Get the diff for versions.env, optionally against a ref and/or base.

    If ref is given, compares ref vs base (defaults to HEAD if no base).
    If no ref, compares working tree vs HEAD.
    Returns the diff text, or None if there's no diff.
    """
    if ref:
        if base:
            args = ["diff", "--exit-code", base, f"origin/{ref}", "--", "versions.env"]
        else:
            args = ["diff", "--exit-code", "HEAD", f"origin/{ref}", "--", "versions.env"]
    else:
        # Check both staged and unstaged changes vs HEAD
        args = ["diff", "--exit-code", "HEAD", "--", "versions.env"]
    result = run_git(args, check=False)
    if result.returncode == 0:
        return None
    return result.stdout


def diff_has_substantive_changes(diff_text):
    """Check if a versions.env diff has changes beyond GB10_BUILD.

    Returns True if there are any changed variables other than GB10_BUILD.
    """
    if diff_text is None:
        return False
    for line in diff_text.splitlines():
        # Look for +/- lines that define variables (not diff headers, not comments)
        if line.startswith(("+", "-")) and "=" in line and not line.startswith("---") and not line.startswith("+++"):
            var_name = line.split("=", 1)[0][1:].strip()  # strip +/- prefix
            if var_name != "GB10_BUILD":
                return True
    return False


def check_for_duplicate():
    """Check for existing PRs with matching versions.env, return True if duplicate."""
    # First check if versions.env actually has uncommitted changes
    working_diff = get_versions_diff()
    if not diff_has_substantive_changes(working_diff):
        print("No substantive changes to versions.env in working tree")
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
    if result.returncode != 0:
        detail = result.stderr.strip() or f"gh exited with code {result.returncode}"
        raise RuntimeError(f"Could not list open dependency PRs: {detail}")

    prs_text = result.stdout.strip()
    if not prs_text:
        print("No open deps/bump PRs by this actor")
        return False

    # gh --jq '.[]' outputs one JSON object per line (NDJSON) when there are
    # multiple results, or a single JSON object when there's exactly one.
    # Handle both cases.
    lines = [l.strip() for l in prs_text.splitlines() if l.strip()]
    if len(lines) == 1:
        prs = json.loads(lines[0])
        if not isinstance(prs, list):
            prs = [prs]
    else:
        prs = [json.loads(line) for line in lines]

    for pr in prs:
        number = pr["number"]
        branch = pr["headRefName"]
        print(f"Checking PR #{number} ({branch})...")

        # Fetch the branch
        run_git(["fetch", "origin", branch])

        # Compare the substantive changes between the PR branch and origin/main
        # vs the working tree and origin/main. This way we compare what each
        # changes relative to the common base, ignoring GB10_BUILD.
        branch_diff_vs_main = get_versions_diff(branch, "origin/main")
        if diff_has_substantive_changes(branch_diff_vs_main) != diff_has_substantive_changes(working_diff):
            print(f"PR #{number} has different versions.env -- not a match")
            continue

        def substantive_lines(diff):
            lines = set()
            for line in diff.splitlines():
                if line.startswith(("+", "-")) and "=" in line and not line.startswith("---") and not line.startswith("+++"):
                    var_name = line.split("=", 1)[0][1:].strip()
                    if var_name != "GB10_BUILD":
                        lines.add(line)
            return lines

        if substantive_lines(branch_diff_vs_main) == substantive_lines(working_diff):
            print(f"PR #{number} has matching versions.env -- skipping duplicate")
            return True
        else:
            print(f"PR #{number} has different versions.env -- not a match")

    print("No matching PR found -- will create new one")
    return False


if __name__ == "__main__":
    try:
        should_skip = check_for_duplicate()
    except (json.JSONDecodeError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"Duplicate PR check failed: {error}", file=sys.stderr)
        sys.exit(2)
    sys.exit(0 if should_skip else 1)
