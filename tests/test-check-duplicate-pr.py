#!/usr/bin/env python3
"""
Tests for scripts/check-duplicate-pr.py logic.

Tests the core check function in isolation (without calling gh/git).

Scenario coverage:
  - No changes in working tree (skip)
  - Only GB10_BUILD changes (skip - not substantive)
  - Substantive changes, no open PRs (proceed)
  - Single PR with matching substantive changes (skip)
  - Single PR with only GB10_BUILD diff, working tree has UV (proceed - different substantive)
  - Single PR with different substantive changes (proceed)
  - Single PR with same UV but different GB10_BUILD (skip - same substantive, GB10 ignored)
  - Multiple PRs, none matching (proceed)
  - Multiple PRs, second one matches (skip)
  - Multiple PRs, all match (skip - first match short-circuits)
  - gh returns single object not array (skip)
  - gh pr list errors/network issue (error)
  - gh returns empty array (proceed)
  - git fetch fails (error)
  - Workflow invokes the script through Python and maps all exit codes correctly
  - Branch has only GB10_BUILD change vs main, working tree has UV (proceed)
  - Working tree has only GB10_BUILD, branch has substantive vs main (skip)
"""

import sys

sys.path.insert(0, "scripts")


# ---------------------------------------------------------------------------
# Helpers to generate realistic unified diff output for mock git
# ---------------------------------------------------------------------------

SIMPLE_UV_DIFF = """\
diff --git a/versions.env b/versions.env
index abc1234..def5678 100644
--- a/versions.env
+++ b/versions.env
@@ -20,6 +20,6 @@ GB10_BUILD=2
-UV_VERSION=0.11.29
+UV_VERSION=0.11.30
"""

# Diff with only GB10_BUILD change (should be treated as no substantive change)
GB10_ONLY_DIFF = """\
diff --git a/versions.env b/versions.env
index abc1234..def5678 100644
--- a/versions.env
+++ b/versions.env
@@ -14,6 +14,6 @@ CUDA_BASE_DIGEST=sha256:a5b6256e470196fc1d5f8f62139d57d3662867746dfe1cb352d76
-GB10_BUILD=2
+GB10_BUILD=3
"""

# Diff with BOTH UV_VERSION and GB10_BUILD change (UV is substantive)
UV_AND_GB10_DIFF = """\
diff --git a/versions.env b/versions.env
index abc1234..def5678 100644
--- a/versions.env
+++ b/versions.env
@@ -14,6 +14,6 @@ CUDA_BASE_DIGEST=sha256:a5b6256e470196fc1d5f8f62139d57d3662867746dfe1cb352d76
-GB10_BUILD=2
+GB10_BUILD=3
@@ -20,6 +20,6 @@ GB10_BUILD=3
-UV_VERSION=0.11.29
+UV_VERSION=0.11.30
"""

# Diff with a completely different variable (VLLM_REF instead of UV_VERSION)
VLLM_REF_DIFF = """\
diff --git a/versions.env b/versions.env
index abc1234..def5678 100644
--- a/versions.env
+++ b/versions.env
@@ -28,6 +28,6 @@ GB10_BUILD=2
-VLLM_REF=v0.24.0
+VLLM_REF=v0.25.1
"""

# Diff with BOTH VLLM_REF and GB10_BUILD change
VLLM_AND_GB10_DIFF = """\
diff --git a/versions.env b/versions.env
index abc1234..def5678 100644
--- a/versions.env
+++ b/versions.env
@@ -28,6 +28,6 @@ GB10_BUILD=2
-VLLM_REF=v0.24.0
+VLLM_REF=v0.25.1
@@ -14,6 +14,6 @@ CUDA_BASE_DIGEST=sha256:a5b6256e470196fc1d5f8f62139d57d3662867746dfe1cb352d76
-GB10_BUILD=2
+GB10_BUILD=3
"""

if __name__ == "__main__":
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

    # Build test scenarios
    # Each scenario is a tuple:
    #   (name, fake_gh_stdout, fake_git_behaviors, expected_exit_code)
    #
    # fake_gh_stdout: what gh pr list returns (empty string = no PRs, or error)
    # fake_git_behaviors dict:
    #   working_diff: the diff text to return for working tree diff vs HEAD, or None for exit 0
    #   branch_diffs_vs_main: dict mapping branch name -> diff text vs origin/main, or None for exit 0
    #   fetch_exit: exit code for git fetch (0 = success, 1 = failure)

    EMPTY = None  # No diff (exit 0)

    scenarios = [
        (
            "No changes in working tree",
            "",
            {"working_diff": EMPTY, "branch_diffs_vs_main": {}, "fetch_exit": 0},
            0,  # skip (no changes)
        ),
        (
            "Only GB10_BUILD changes in working tree (not substantive)",
            "",
            {"working_diff": GB10_ONLY_DIFF, "branch_diffs_vs_main": {}, "fetch_exit": 0},
            0,  # skip (no substantive changes)
        ),
        (
            "Substantive changes (UV_VERSION + GB10_BUILD), no open PRs",
            "",
            {"working_diff": UV_AND_GB10_DIFF, "branch_diffs_vs_main": {}, "fetch_exit": 0},
            1,  # proceed
        ),
        (
            "Single PR with matching substantive changes (UV_VERSION + GB10_BUILD)",
            '{"number": 42, "headRefName": "deps/bump-42"}',
            {"working_diff": UV_AND_GB10_DIFF,
             "branch_diffs_vs_main": {"deps/bump-42": UV_AND_GB10_DIFF},
             "fetch_exit": 0},
            0,  # skip
        ),
        (
            "Single PR with only GB10_BUILD diff vs main, working tree has UV (different substantive)",
            '{"number": 42, "headRefName": "deps/bump-42"}',
            {"working_diff": UV_AND_GB10_DIFF,
             "branch_diffs_vs_main": {"deps/bump-42": GB10_ONLY_DIFF},
             "fetch_exit": 0},
            1,  # proceed (branch has no substantive change vs main)
        ),
        (
            "Single PR with different substantive changes (VLLM_REF vs UV_VERSION)",
            '{"number": 42, "headRefName": "deps/bump-42"}',
            {"working_diff": UV_AND_GB10_DIFF,
             "branch_diffs_vs_main": {"deps/bump-42": VLLM_AND_GB10_DIFF},
             "fetch_exit": 0},
            1,  # proceed (different variables changed)
        ),
        (
            "Single PR with same UV but different GB10_BUILD (same substantive)",
            '{"number": 42, "headRefName": "deps/bump-42"}',
            {"working_diff": UV_AND_GB10_DIFF,
             "branch_diffs_vs_main": {"deps/bump-42": SIMPLE_UV_DIFF},
             "fetch_exit": 0},
            0,  # skip (same UV change, GB10_BUILD differs but ignored)
        ),
        (
            "Multiple PRs, none matching",
            '{"number": 41, "headRefName": "deps/bump-41"}\n{"number": 42, "headRefName": "deps/bump-42"}',
            {"working_diff": UV_AND_GB10_DIFF,
             "branch_diffs_vs_main": {"deps/bump-41": VLLM_AND_GB10_DIFF, "deps/bump-42": VLLM_AND_GB10_DIFF},
             "fetch_exit": 0},
            1,  # proceed
        ),
        (
            "Multiple PRs, second one matches",
            '{"number": 41, "headRefName": "deps/bump-41"}\n{"number": 42, "headRefName": "deps/bump-42"}',
            {"working_diff": UV_AND_GB10_DIFF,
             "branch_diffs_vs_main": {"deps/bump-41": VLLM_AND_GB10_DIFF, "deps/bump-42": UV_AND_GB10_DIFF},
             "fetch_exit": 0},
            0,  # skip (second one matches)
        ),
        (
            "Multiple PRs, all match",
            '{"number": 41, "headRefName": "deps/bump-41"}\n{"number": 42, "headRefName": "deps/bump-42"}',
            {"working_diff": UV_AND_GB10_DIFF,
             "branch_diffs_vs_main": {"deps/bump-41": UV_AND_GB10_DIFF, "deps/bump-42": UV_AND_GB10_DIFF},
             "fetch_exit": 0},
            0,  # skip (first match short-circuits)
        ),
        (
            "gh returns single object not array",
            '{"number": 42, "headRefName": "deps/bump-42"}',
            {"working_diff": UV_AND_GB10_DIFF,
             "branch_diffs_vs_main": {"deps/bump-42": UV_AND_GB10_DIFF},
             "fetch_exit": 0},
            0,  # skip
        ),
        (
            "gh pr list errors (network issue)",
            "",
            {"working_diff": UV_AND_GB10_DIFF,
             "branch_diffs_vs_main": {},
             "fetch_exit": 0,
             "gh_exit": 1},
            2,  # error (the duplicate check was inconclusive)
        ),
        (
            "gh returns empty array (no matching PRs)",
            "[]",
            {"working_diff": UV_AND_GB10_DIFF,
             "branch_diffs_vs_main": {},
             "fetch_exit": 0},
            1,  # proceed
        ),
        (
            "git fetch fails for the PR branch (rate limited)",
            '{"number": 42, "headRefName": "deps/bump-42"}',
            {"working_diff": UV_AND_GB10_DIFF,
             "branch_diffs_vs_main": {},
             "fetch_exit": 1},  # fetch fails
            2,  # error (the duplicate check was inconclusive)
        ),
        (
            "Branch has only GB10_BUILD change vs main, working tree has UV",
            '{"number": 42, "headRefName": "deps/bump-42"}',
            {"working_diff": UV_AND_GB10_DIFF,
             "branch_diffs_vs_main": {"deps/bump-42": GB10_ONLY_DIFF},
             "fetch_exit": 0},
            1,  # proceed (different substantive content)
        ),
        (
            "Working tree has only GB10_BUILD, branch has substantive vs main",
            '{"number": 42, "headRefName": "deps/bump-42"}',
            {"working_diff": GB10_ONLY_DIFF,
             "branch_diffs_vs_main": {"deps/bump-42": UV_AND_GB10_DIFF},
             "fetch_exit": 0},
            0,  # skip (no substantive changes in working tree)
        ),
    ]

    script_path = os.path.join(
        os.path.dirname(__file__), "..", "scripts", "check-duplicate-pr.py"
    )
    script_path = os.path.abspath(script_path)

    for name, fake_gh_stdout, fake_git_behaviors, expected_exit in scenarios:
        print(f"Scenario: {name}")
        with tempfile.TemporaryDirectory() as tmpdir:
            working_diff = fake_git_behaviors["working_diff"]
            branch_diffs_vs_main = fake_git_behaviors.get("branch_diffs_vs_main", {})
            fetch_exit = fake_git_behaviors.get("fetch_exit", 0)
            gh_exit = fake_git_behaviors.get("gh_exit", 0)

            # Create mock gh script
            mock_gh = os.path.join(tmpdir, "gh")
            with open(mock_gh, "w") as f:
                f.write("#!/usr/bin/env bash\n")
                if fake_gh_stdout:
                    f.write(f'cat << \'ENDJSON\'\n{fake_gh_stdout}\nENDJSON\n')
                f.write(f"exit {gh_exit}\n")
            os.chmod(mock_gh, 0o755)

            # Create mock git script
            mock_git = os.path.join(tmpdir, "git")
            with open(mock_git, "w") as f:
                f.write("#!/usr/bin/env bash\n")
                f.write('set -o pipefail\n')

                # Handle "git diff --exit-code HEAD -- versions.env" (working tree vs HEAD)
                f.write('if [ "$1" = "diff" ] && [ "$2" = "--exit-code" ] && [ "$3" = "HEAD" ] && [ "$4" = "--" ] && [ "$5" = "versions.env" ] && [ $# -eq 5 ]; then\n')
                if working_diff is not None:
                    f.write(f'  cat << \'ENDDIFF\'\n{working_diff}\nENDDIFF\n')
                    f.write("  exit 1\n")
                else:
                    f.write("  exit 0\n")
                f.write("fi\n")

                # Handle "git fetch origin <branch>"
                f.write('if [ "$1" = "fetch" ] && [ "$2" = "origin" ] && [ $# -eq 3 ]; then\n')
                f.write(f"  exit {fetch_exit}\n")
                f.write("fi\n")

                # Handle "git diff --exit-code origin/main origin/<branch> -- versions.env"
                f.write('if [ "$1" = "diff" ] && [ "$2" = "--exit-code" ] && [ "$3" = "origin/main" ] && [[ "$4" =~ ^origin/ ]] && [ "$5" = "--" ] && [ "$6" = "versions.env" ] && [ $# -eq 6 ]; then\n')
                if branch_diffs_vs_main:
                    i=0
                    for br, diff_content in branch_diffs_vs_main.items():
                        prefix = "elif" if i > 0 else "if"
                        i+=1
                        if diff_content is not None:
                            f.write(f'  {prefix} [ "$4" = "origin/{br}" ]; then\n')
                            f.write(f'    cat << \'ENDDIFF\'\n{diff_content}\nENDDIFF\n')
                            f.write("    exit 1\n")
                        else:
                            f.write(f'  {prefix} [ "$4" = "origin/{br}" ]; then\n')
                            f.write("    exit 0\n")
                    f.write("  else\n")
                    f.write("    exit 1\n")
                    f.write("  fi\n")
                else:
                    f.write("  exit 1\n")
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

    workflow_path = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", ".github", "workflows",
                     "monitor-upstream-releases.yaml")
    )
    with open(workflow_path) as workflow_file:
        workflow = workflow_file.read()

    check(
        "python3 scripts/check-duplicate-pr.py" in workflow,
        "Workflow must invoke the Python script without relying on its executable bit",
    )
    check(
        '0) echo "skip-pr=true"' in workflow,
        "Workflow must skip PR creation when the script reports a duplicate",
    )
    check(
        '1) echo "skip-pr=false"' in workflow,
        "Workflow must proceed when the script reports no duplicate",
    )
    check(
        'exit "$result"' in workflow,
        "Workflow must fail instead of skipping when the duplicate check errors",
    )

    print(f"FAIL: {failed}")
    if failed:
        print(f"FAILED: {failed} of {passed + failed} checks failed")
        sys.exit(1)
    else:
        print(f"All {passed} checks passed!")
        sys.exit(0)
