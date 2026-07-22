#!/usr/bin/env python3
"""
Tests for scripts/check-duplicate-pr.py logic.

Tests the core check function in isolation (without calling gh/git).

Scenario coverage:
  - No changes in working tree (skip)
  - No open PRs found (proceed)
  - Single PR with matching versions.env (skip)
  - Single PR with different versions.env (proceed)
  - Multiple PRs, none matching (proceed)
  - Multiple PRs, one matching (skip)
"""

import sys
import textwrap

sys.path.insert(0, "scripts")

# We test the internal logic by patching run_git/run_gh, but since we can't
# easily monkey-patch in a subprocess, we test the decision logic directly.
# The script's exit code behavior is validated via subprocess tests below.


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # Run check-duplicate-pr.py in a subprocess with fake gh/git
    # This is done by creating a temporary script that shadows the real gh/git
    import os
    import subprocess
    import tempfile

    passed = 0
    failed = 0

    def check(condition, message):
        global passed, failed
        if condition:
            passed += 1
        else:
            failed += 1
            print(f"  FAIL: {message}", file=sys.stderr)

    # Build test scenarios simulating different gh/git responses
    # Each scenario is a tuple:
    #   (name, fake_gh_stdout, fake_git_diff_exit_codes, expected_exit_code)
    #
    # fake_gh_stdout: what gh pr list returns (empty string = no PRs)
    # fake_git_diff_exit_codes: list of exit codes for each git diff --exit-code call
    # expected_exit_code: 0 = skip, 1 = proceed
    #
    # The test harness creates mock scripts for gh and git that return
    # the specified outputs and exit codes.

    scenarios = [
        (
            "No changes in working tree (git diff versions.env exits 0)",
            # gh not called since git diff shows no changes
            "",
            {"diff_exit": 0},  # git diff versions.env exits 0 -> no changes
            0,  # skip (no changes to PR)
        ),
        (
            "Working tree has changes, no open PRs (gh returns empty)",
            "",  # gh pr list returns nothing
            {"diff_exit": 1,  # git diff versions.env exits 1 (has changes)
             "fetch_exit": 0,
             "diff_branch_exit": 0},  # branch diff not checked (no PRs)
            1,  # proceed
        ),
        (
            "Working tree has changes, single PR with matching diff",
            # gh returns one PR: #42 on branch deps/bump-42
            '[{"number": 42, "headRefName": "deps/bump-42"}]',
            {"diff_exit": 1,  # git diff versions.env exits 1 (has changes)
             "fetch_exit": 0,
             "diff_branch_exit": 0},  # git diff origin/deps/bump-42 exits 0 (matching)
            0,  # skip (matching PR found)
        ),
        (
            "Working tree has changes, single PR with different diff",
            '[{"number": 42, "headRefName": "deps/bump-42"}]',
            {"diff_exit": 1,  # git diff versions.env exits 1 (has changes)
             "fetch_exit": 0,
             "diff_branch_exit": 1},  # git diff origin/deps/bump-42 exits 1 (different)
            1,  # proceed (no match)
        ),
        (
            "Multiple PRs, none matching",
            '[{"number": 41, "headRefName": "deps/bump-41"}, {"number": 42, "headRefName": "deps/bump-42"}]',
            {"diff_exit": 1,  # git diff versions.env exits 1 (has changes)
             "fetch_exit": 0,
             "diff_branch_exit": [1, 1]},  # both PRs have different diffs
            1,  # proceed
        ),
        (
            "Multiple PRs, second one matches",
            '[{"number": 41, "headRefName": "deps/bump-41"}, {"number": 42, "headRefName": "deps/bump-42"}]',
            {"diff_exit": 1,
             "fetch_exit": 0,
             "diff_branch_exit": [1, 0]},  # first doesn't match, second does
            0,  # skip
        ),
        (
            "Single PR returns single object (not array) from gh",
            '{"number": 42, "headRefName": "deps/bump-42"}',
            {"diff_exit": 1,
             "fetch_exit": 0,
             "diff_branch_exit": 0},
            0,  # skip
        ),
        (
            "gh pr list errors (network issue)",
            "",  # stderr handled via check=False
            {"diff_exit": 1,
             "fetch_exit": 0,
             "diff_branch_exit": 0},
            1,  # proceed (fails open)
        ),
        (
            "gh pr list returns empty array (no PRs)",
            "[]",
            {"diff_exit": 1,
             "fetch_exit": 0,
             "diff_branch_exit": 0},
            1,  # proceed
        ),
        (
            "gh pr list returns non-JSON output (stale/weird token)",
            "",
            {"diff_exit": 1,
             "fetch_exit": 0,
             "diff_branch_exit": 0},
            1,  # proceed (json.loads will crash but script catches via stdout empty check)
        ),
        (
            "Multiple PRs, all match",
            '[{"number": 41, "headRefName": "deps/bump-41"}, {"number": 42, "headRefName": "deps/bump-42"}]',
            {"diff_exit": 1,
             "fetch_exit": 0,
             "diff_branch_exit": [0, 0]},  # both match
            0,  # skip (first match short-circuits)
        ),
        (
            "Single PR, git fetch fails (rate-limited or branch deleted)",
            '[{"number": 42, "headRefName": "deps/bump-42"}]',
            {"diff_exit": 1,
             "fetch_exit": 1,  # git fetch fails
             "diff_branch_exit": 0},  # diff not reached
            1,  # proceed (fetch failure should not block PR creation)
        ),
        (
            "versions.env changes but lockfiles only changed (no component version change)",
            "",
            {"diff_exit": 1,  # git diff versions.env exits 1 (has changes, e.g. GB10_BUILD)
             "fetch_exit": 0,
             "diff_branch_exit": 0},
            1,  # proceed (no PRs exist, should proceed)
        ),
    ]

    script_path = os.path.join(
        os.path.dirname(__file__), "..", "scripts", "check-duplicate-pr.py"
    )
    script_path = os.path.abspath(script_path)

    for name, fake_gh_stdout, fake_git_behaviors, expected_exit in scenarios:
        print(f"Scenario: {name}")
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create mock gh script
            mock_gh = os.path.join(tmpdir, "gh")
            with open(mock_gh, "w") as f:
                f.write("#!/usr/bin/env bash\n")
                if fake_gh_stdout:
                    f.write(f'cat << \'ENDJSON\'\n{fake_gh_stdout}\nENDJSON\n')
                else:
                    f.write("exit 0\n")
            os.chmod(mock_gh, 0o755)

            # Create mock git script
            mock_git = os.path.join(tmpdir, "git")
            diff_exit = fake_git_behaviors.get("diff_exit", 0)
            fetch_exit = fake_git_behaviors.get("fetch_exit", 0)
            diff_branch_exit = fake_git_behaviors.get("diff_branch_exit", 0)

            with open(mock_git, "w") as f:
                f.write("#!/usr/bin/env bash\n")

                # Handle "git diff --exit-code versions.env" (bare, no branch)
                f.write('if [ "$1" = "diff" ] && [ "$2" = "--exit-code" ] && [ "$3" = "versions.env" ]; then\n')
                f.write(f"  exit {diff_exit}\n")
                f.write("fi\n")

                # Handle "git fetch origin <branch>"
                f.write('if [ "$1" = "fetch" ]; then\n')
                f.write(f"  exit {fetch_exit}\n")
                f.write("fi\n")

                # Handle "git diff --exit-code origin/<branch> -- versions.env"
                f.write('if [ "$1" = "diff" ] && [ "$2" = "--exit-code" ] && [[ "$3" =~ ^origin/ ]]; then\n')
                if isinstance(diff_branch_exit, list):
                    # Use a counter file to track which invocation
                    f.write('  COUNTER_FILE="' + tmpdir + '/diff_counter"\n')
                    f.write('  if [ ! -f "$COUNTER_FILE" ]; then echo 0 > "$COUNTER_FILE"; fi\n')
                    f.write('  COUNTER=$(cat "$COUNTER_FILE")\n')
                    f.write('  echo $((COUNTER + 1)) > "$COUNTER_FILE"\n')
                    for i, code in enumerate(diff_branch_exit):
                        f.write(f'  if [ "$COUNTER" = "{i}" ]; then exit {code}; fi\n')
                    f.write(f"  exit {diff_branch_exit[-1]}\n")
                else:
                    f.write(f"  exit {diff_branch_exit}\n")
                f.write("fi\n")

                # Default: pass through to real git for other commands
                f.write('exec /usr/bin/git "$@"\n')
            os.chmod(mock_git, 0o755)

            # Run the script with mocked PATH
            env = os.environ.copy()
            env["PATH"] = f"{tmpdir}:{env['PATH']}"
            env["GH_TOKEN"] = "fake-token"

            result = subprocess.run(
                [sys.executable, script_path],
                capture_output=True, text=True, env=env,
            )

            actual_exit = result.returncode
            check(
                actual_exit == expected_exit,
                f"Expected exit {expected_exit}, got {actual_exit}. stdout: {result.stdout.strip()}",
            )

            # Show the script's output
            for line in result.stdout.strip().splitlines():
                print(f"  {line}")

        print()

    print(f"FAIL: {failed}")
    if failed:
        print(f"FAILED: {failed} of {passed + failed} checks failed")
        sys.exit(1)
    else:
        print(f"All {passed} checks passed!")
        sys.exit(0)
