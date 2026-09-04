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
  - Dependabot PR with matching versions.env change (skip)
  - Dependabot PR with no versions.env change (proceed - action-only PR does not cover the update)
  - Larger PR includes the candidate update as a subset (skip)
  - PR changes the same variable to a different value (proceed)
  - Candidate has more changes than the PR (partial coverage, proceed)
  - Multiple PRs, each partially covers, none fully (proceed)
  - PR superset but shared variable has a different value (proceed)
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

# Diff with BOTH UV_VERSION and VLLM_REF change (a larger PR that includes the
# UV bump as a subset of its changes)
UV_AND_VLLM_DIFF = """\
diff --git a/versions.env b/versions.env
index abc1234..def5678 100644
--- a/versions.env
+++ b/versions.env
@@ -20,6 +20,6 @@ GB10_BUILD=2
-UV_VERSION=0.11.29
+UV_VERSION=0.11.30
@@ -28,6 +28,6 @@ GB10_BUILD=2
-VLLM_REF=v0.24.0
+VLLM_REF=v0.25.1
"""

# Diff that bumps UV_VERSION to a different value than the candidate
UV_DIFFERENT_DIFF = """\
diff --git a/versions.env b/versions.env
index abc1234..def5678 100644
--- a/versions.env
+++ b/versions.env
@@ -20,6 +20,6 @@ GB10_BUILD=2
-UV_VERSION=0.11.29
+UV_VERSION=0.12.0
"""

# Diff that bumps UV_VERSION to a different value AND changes VLLM_REF. Used to
# prove a superset PR does not suppress the candidate when the shared variable
# is changed to a different target version.
UV_DIFFERENT_AND_VLLM_DIFF = """\
diff --git a/versions.env b/versions.env
index abc1234..def5678 100644
--- a/versions.env
+++ b/versions.env
@@ -20,6 +20,6 @@ GB10_BUILD=2
-UV_VERSION=0.11.29
+UV_VERSION=0.12.0
@@ -28,6 +28,6 @@ GB10_BUILD=2
-VLLM_REF=v0.24.0
+VLLM_REF=v0.25.1
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
    #   branch_diffs_vs_base: dict mapping branch name -> diff text vs base ref, or None for exit 0
    #   fetch_exit: exit code for git fetch (0 = success, 1 = failure)
    #   origin_main_available: whether the origin/main ref resolves locally (default True)
    #   gh_exit: exit code for gh pr list (default 0)

    EMPTY = None  # No diff (exit 0)

    scenarios = [
        (
            "No changes in working tree",
            "",
            {"working_diff": EMPTY, "branch_diffs_vs_base": {}, "fetch_exit": 0},
            0,  # skip (no changes)
        ),
        (
            "Only GB10_BUILD changes in working tree (not substantive)",
            "",
            {"working_diff": GB10_ONLY_DIFF, "branch_diffs_vs_base": {}, "fetch_exit": 0},
            0,  # skip (no substantive changes)
        ),
        (
            "Substantive changes (UV_VERSION + GB10_BUILD), no open PRs",
            "",
            {"working_diff": UV_AND_GB10_DIFF, "branch_diffs_vs_base": {}, "fetch_exit": 0},
            10,  # proceed
        ),
        (
            "Single PR with matching substantive changes (UV_VERSION + GB10_BUILD)",
            '{"number": 42, "headRefName": "deps/bump-42"}',
            {"working_diff": UV_AND_GB10_DIFF,
             "branch_diffs_vs_base": {"deps/bump-42": UV_AND_GB10_DIFF},
             "fetch_exit": 0},
            0,  # skip
        ),
        (
            "Single PR with only GB10_BUILD diff vs main, working tree has UV (different substantive)",
            '{"number": 42, "headRefName": "deps/bump-42"}',
            {"working_diff": UV_AND_GB10_DIFF,
             "branch_diffs_vs_base": {"deps/bump-42": GB10_ONLY_DIFF},
             "fetch_exit": 0},
            10,  # proceed (branch has no substantive change vs main)
        ),
        (
            "Single PR with different substantive changes (VLLM_REF vs UV_VERSION)",
            '{"number": 42, "headRefName": "deps/bump-42"}',
            {"working_diff": UV_AND_GB10_DIFF,
             "branch_diffs_vs_base": {"deps/bump-42": VLLM_AND_GB10_DIFF},
             "fetch_exit": 0},
            10,  # proceed (different variables changed)
        ),
        (
            "Single PR with same UV but different GB10_BUILD (same substantive)",
            '{"number": 42, "headRefName": "deps/bump-42"}',
            {"working_diff": UV_AND_GB10_DIFF,
             "branch_diffs_vs_base": {"deps/bump-42": SIMPLE_UV_DIFF},
             "fetch_exit": 0},
            0,  # skip (same UV change, GB10_BUILD differs but ignored)
        ),
        (
            "Dependabot PR with matching versions.env change (skip)",
            '{"number": 118, "headRefName": "dependabot/github_actions/softprops/action-gh-release-3.0.3"}',
            {"working_diff": UV_AND_GB10_DIFF,
             "branch_diffs_vs_base": {"dependabot/github_actions/softprops/action-gh-release-3.0.3": UV_AND_GB10_DIFF},
             "fetch_exit": 0},
            0,  # skip (update already in a dependabot PR)
        ),
        (
            "Dependabot PR with no versions.env change (does not suppress, proceed)",
            '{"number": 118, "headRefName": "dependabot/github_actions/softprops/action-gh-release-3.0.3"}',
            {"working_diff": UV_AND_GB10_DIFF,
             "branch_diffs_vs_base": {"dependabot/github_actions/softprops/action-gh-release-3.0.3": None},
             "fetch_exit": 0},
            10,  # proceed (an action-only dependabot PR does not cover the uv update)
        ),
        (
            "Larger PR includes the candidate update as a subset (skip)",
            '{"number": 42, "headRefName": "deps/bump-42"}',
            {"working_diff": UV_AND_GB10_DIFF,
             "branch_diffs_vs_base": {"deps/bump-42": UV_AND_VLLM_DIFF},
             "fetch_exit": 0},
            0,  # skip (candidate UV change is a subset of the PR's changes)
        ),
        (
            "PR changes the same variable to a different value (proceed)",
            '{"number": 42, "headRefName": "deps/bump-42"}',
            {"working_diff": UV_AND_GB10_DIFF,
             "branch_diffs_vs_base": {"deps/bump-42": UV_DIFFERENT_DIFF},
             "fetch_exit": 0},
            10,  # proceed (same variable but a different target version)
        ),
        (
            "Candidate has more changes than the PR (partial coverage, proceed)",
            '{"number": 42, "headRefName": "deps/bump-42"}',
            {"working_diff": UV_AND_VLLM_DIFF,
             "branch_diffs_vs_base": {"deps/bump-42": SIMPLE_UV_DIFF},
             "fetch_exit": 0},
            10,  # proceed (the PR only covers UV, not the VLLM change)
        ),
        (
            "Multiple PRs, each partially covers, none fully (proceed)",
            '{"number": 41, "headRefName": "deps/bump-41"}\n{"number": 42, "headRefName": "deps/bump-42"}',
            {"working_diff": UV_AND_VLLM_DIFF,
             "branch_diffs_vs_base": {"deps/bump-41": SIMPLE_UV_DIFF, "deps/bump-42": VLLM_AND_GB10_DIFF},
             "fetch_exit": 0},
            10,  # proceed (no single PR covers both UV and VLLM)
        ),
        (
            "PR superset but shared variable has a different value (proceed)",
            '{"number": 42, "headRefName": "deps/bump-42"}',
            {"working_diff": UV_AND_GB10_DIFF,
             "branch_diffs_vs_base": {"deps/bump-42": UV_DIFFERENT_AND_VLLM_DIFF},
             "fetch_exit": 0},
            10,  # proceed (PR changes UV to a different version, so it does not cover the candidate)
        ),
        (
            "Shallow checkout: origin/main not a local ref, matching change (skip duplicate)",
            '{"number": 42, "headRefName": "deps/bump-42"}',
            {"working_diff": UV_AND_GB10_DIFF,
             "branch_diffs_vs_base": {"deps/bump-42": UV_AND_GB10_DIFF},
             "fetch_exit": 0,
             "origin_main_available": False},
            0,  # skip (falls back to HEAD base and detects the match)
        ),
        (
            "Shallow checkout: origin/main not a local ref, different change (proceed)",
            '{"number": 42, "headRefName": "deps/bump-42"}',
            {"working_diff": UV_AND_GB10_DIFF,
             "branch_diffs_vs_base": {"deps/bump-42": VLLM_AND_GB10_DIFF},
             "fetch_exit": 0,
             "origin_main_available": False},
            10,  # proceed (different substantive content under HEAD base)
        ),
        (
            "git diff fails for a PR branch (fail closed, not a silent duplicate)",
            '{"number": 42, "headRefName": "deps/bump-42"}',
            {"working_diff": UV_AND_GB10_DIFF,
             "branch_diffs_vs_base": {"deps/bump-42": UV_AND_GB10_DIFF},
             "branch_diff_errors": ["deps/bump-42"],
             "fetch_exit": 0},
            2,  # error (duplicate check could not be completed)
        ),
        (
            "git diff fails for the working tree (fail closed, not a silent skip)",
            '{"number": 42, "headRefName": "deps/bump-42"}',
            {"working_diff": UV_AND_GB10_DIFF,
             "branch_diffs_vs_base": {"deps/bump-42": UV_AND_GB10_DIFF},
             "working_diff_error": True,
             "fetch_exit": 0},
            2,  # error (duplicate check could not be completed)
        ),
        (
            "Two open PRs, first (identical change) diff errors, second differs (fail closed)",
            '{"number": 41, "headRefName": "deps/bump-41"}\n{"number": 42, "headRefName": "deps/bump-42"}',
            {"working_diff": UV_AND_GB10_DIFF,
             "branch_diffs_vs_base": {"deps/bump-41": UV_AND_GB10_DIFF, "deps/bump-42": VLLM_AND_GB10_DIFF},
             "branch_diff_errors": ["deps/bump-41"],
             "fetch_exit": 0},
            2,  # error (one branch could not be compared, so no decision)
        ),
        (
            "Multiple PRs, none matching",
            '{"number": 41, "headRefName": "deps/bump-41"}\n{"number": 42, "headRefName": "deps/bump-42"}',
            {"working_diff": UV_AND_GB10_DIFF,
             "branch_diffs_vs_base": {"deps/bump-41": VLLM_AND_GB10_DIFF, "deps/bump-42": VLLM_AND_GB10_DIFF},
             "fetch_exit": 0},
            10,  # proceed
        ),
        (
            "Multiple PRs, second one matches",
            '{"number": 41, "headRefName": "deps/bump-41"}\n{"number": 42, "headRefName": "deps/bump-42"}',
            {"working_diff": UV_AND_GB10_DIFF,
             "branch_diffs_vs_base": {"deps/bump-41": VLLM_AND_GB10_DIFF, "deps/bump-42": UV_AND_GB10_DIFF},
             "fetch_exit": 0},
            0,  # skip (second one matches)
        ),
        (
            "Multiple PRs, all match",
            '{"number": 41, "headRefName": "deps/bump-41"}\n{"number": 42, "headRefName": "deps/bump-42"}',
            {"working_diff": UV_AND_GB10_DIFF,
             "branch_diffs_vs_base": {"deps/bump-41": UV_AND_GB10_DIFF, "deps/bump-42": UV_AND_GB10_DIFF},
             "fetch_exit": 0},
            0,  # skip (first match short-circuits)
        ),
        (
            "gh returns single object not array",
            '{"number": 42, "headRefName": "deps/bump-42"}',
            {"working_diff": UV_AND_GB10_DIFF,
             "branch_diffs_vs_base": {"deps/bump-42": UV_AND_GB10_DIFF},
             "fetch_exit": 0},
            0,  # skip
        ),
        (
            "gh pr list errors (network issue)",
            "",
            {"working_diff": UV_AND_GB10_DIFF,
             "branch_diffs_vs_base": {},
             "fetch_exit": 0,
             "gh_exit": 1},
            2,  # error (the duplicate check was inconclusive)
        ),
        (
            "gh returns empty array (no matching PRs)",
            "[]",
            {"working_diff": UV_AND_GB10_DIFF,
             "branch_diffs_vs_base": {},
             "fetch_exit": 0},
            10,  # proceed
        ),
        (
            "git fetch fails for the PR branch (rate limited)",
            '{"number": 42, "headRefName": "deps/bump-42"}',
            {"working_diff": UV_AND_GB10_DIFF,
             "branch_diffs_vs_base": {},
             "fetch_exit": 1},  # fetch fails
            2,  # error (the duplicate check was inconclusive)
        ),
        (
            "Branch has only GB10_BUILD change vs main, working tree has UV",
            '{"number": 42, "headRefName": "deps/bump-42"}',
            {"working_diff": UV_AND_GB10_DIFF,
             "branch_diffs_vs_base": {"deps/bump-42": GB10_ONLY_DIFF},
             "fetch_exit": 0},
            10,  # proceed (different substantive content)
        ),
        (
            "Working tree has only GB10_BUILD, branch has substantive vs main",
            '{"number": 42, "headRefName": "deps/bump-42"}',
            {"working_diff": GB10_ONLY_DIFF,
             "branch_diffs_vs_base": {"deps/bump-42": UV_AND_GB10_DIFF},
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
            branch_diffs_vs_base = fake_git_behaviors.get("branch_diffs_vs_base", {})
            fetch_exit = fake_git_behaviors.get("fetch_exit", 0)
            gh_exit = fake_git_behaviors.get("gh_exit", 0)
            origin_main_available = fake_git_behaviors.get("origin_main_available", True)
            error_branches = set(fake_git_behaviors.get("branch_diff_errors", []))
            working_diff_error = fake_git_behaviors.get("working_diff_error", False)
            # The script prefers origin/main when the ref resolves, otherwise
            # falls back to HEAD (the shallow create-pr checkout case).
            base_ref = "origin/main" if origin_main_available else "HEAD"

            # Create mock gh script
            mock_gh = os.path.join(tmpdir, "gh")
            with open(mock_gh, "w") as f:
                f.write("#!/usr/bin/env bash\n")
                # Require the query to scan all open PRs. A regression back to
                # an author or branch-namespace filter (which would miss
                # dependabot PRs and PRs from other authors) is caught (exit 3
                # becomes a script error / workflow failure). Also require the
                # query to be scoped to open PRs only.
                f.write('state_ok=0; bad=0; for a in "$@"; do [ "$a" = "--state" ] && state_ok=1; [ "$a" = "--author" ] && bad=1; [ "$a" = "--search" ] && bad=1; done\n')
                f.write("[ $state_ok -eq 1 ] && [ $bad -eq 0 ] || exit 3\n")
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
                if working_diff_error:
                    f.write('  echo "fatal: not a git repository" >&2\n')
                    f.write("  exit 128\n")
                elif working_diff is not None:
                    f.write(f'  cat << \'ENDDIFF\'\n{working_diff}\nENDDIFF\n')
                    f.write("  exit 1\n")
                else:
                    f.write("  exit 0\n")
                f.write("fi\n")

                # Handle "git fetch origin <branch>"
                f.write('if [ "$1" = "fetch" ] && [ "$2" = "origin" ] && [ $# -eq 3 ]; then\n')
                f.write(f"  exit {fetch_exit}\n")
                f.write("fi\n")

                # Handle "git rev-parse --verify --quiet origin/main" (base resolution)
                f.write('if [ "$1" = "rev-parse" ] && [ "$2" = "--verify" ] && [ "$3" = "--quiet" ] && [ "$4" = "origin/main" ]; then\n')
                f.write(f"  exit {0 if origin_main_available else 1}\n")
                f.write("fi\n")
                # Handle "git rev-parse --verify --quiet HEAD" (base fallback)
                f.write('if [ "$1" = "rev-parse" ] && [ "$2" = "--verify" ] && [ "$3" = "--quiet" ] && [ "$4" = "HEAD" ]; then\n')
                f.write("  exit 0\n")
                f.write("fi\n")

                # Handle "git diff --exit-code <base> origin/<branch> -- versions.env"
                f.write('if [ "$1" = "diff" ] && [ "$2" = "--exit-code" ] && [ "$3" = "' + base_ref + '" ] && [[ "$4" =~ ^origin/ ]] && [ "$5" = "--" ] && [ "$6" = "versions.env" ] && [ $# -eq 6 ]; then\n')
                if branch_diffs_vs_base:
                    i=0
                    for br, diff_content in branch_diffs_vs_base.items():
                        prefix = "elif" if i > 0 else "if"
                        i+=1
                        if br in error_branches:
                            f.write(f'  {prefix} [ "$4" = "origin/{br}" ]; then\n')
                            f.write('    echo "fatal: ambiguous argument" >&2\n')
                            f.write("    exit 128\n")
                        elif diff_content is not None:
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
        '10) echo "skip-pr=false"' in workflow,
        "Workflow must proceed when the script reports no duplicate",
    )
    check(
        '1) echo "skip-pr=false"' not in workflow,
        "Workflow must not treat a generic script failure as permission to proceed",
    )
    check(
        'exit "$result"' in workflow,
        "Workflow must fail instead of skipping when the duplicate check errors",
    )
    check(
        "group: monitor-upstream-releases" in workflow,
        "Workflow must serialize monitor runs to prevent duplicate PR races",
    )
    check(
        "cancel-in-progress: false" in workflow,
        "Workflow must let the active monitor run finish before starting another",
    )

    # Static regression guards on the script source. These protect the core
    # invariant that the duplicate check scans all open PRs regardless of
    # author or branch namespace, and that it only considers open PRs.
    with open(script_path) as script_file:
        source = script_file.read()
    check(
        '"--state", "OPEN"' in source,
        "Script must scope the PR query to open PRs only",
    )
    check(
        '"--author"' not in source,
        "Script must not filter PRs by author (would miss dependabot and other authors)",
    )
    check(
        '"--search"' not in source,
        "Script must not filter PRs by branch namespace (would miss dependabot PRs)",
    )
    check(
        "<= substantive_lines(branch_diff_vs_main)" in source,
        "Script must use subset matching so an update already included in a larger PR is recognized",
    )

    print(f"FAIL: {failed}")
    if failed:
        print(f"FAILED: {failed} of {passed + failed} checks failed")
        sys.exit(1)
    else:
        print(f"All {passed} checks passed!")
        sys.exit(0)
