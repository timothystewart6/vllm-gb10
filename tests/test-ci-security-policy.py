#!/usr/bin/env python3
"""Prevent dangerous regressions in workflows that use persistent runners."""

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
ANY_ACTION = re.compile(r"^\s*uses:\s*([^#\s]+)@([^#\s]+)\s*$", re.MULTILINE)
APPROVED_ACTIONS = {
    "actions/checkout": "3d3c42e5aac5ba805825da76410c181273ba90b1",
    "actions/download-artifact": "3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c",
    "actions/upload-artifact": "043fb46d1a93c77aae656e7c1c64a875d1fc6a0a",
    "docker/build-push-action": "53b7df96c91f9c12dcc8a07bcb9ccacbed38856a",
    "docker/login-action": "af1e73f918a031802d376d3c8bbc3fe56130a9b0",
    "docker/metadata-action": "dc802804100637a589fabce1cb79ff13a1411302",
    "docker/setup-buildx-action": "bb05f3f5519dd87d3ba754cc423b652a5edd6d2c",
    "peter-evans/create-pull-request": "5f6978faf089d4d20b00c7766989d076bb2fc7f1",
    "softprops/action-gh-release": "3d0d9888cb7fd7b750713d6e236d1fcb99157228",
}


def read(path):
    return path.read_text(encoding="utf-8")


def test_self_hosted_workflows_have_trusted_entry_points():
    for path in WORKFLOWS.glob("*.yaml"):
        workflow = read(path)
        if "self-hosted" not in workflow:
            continue
        assert "\n  pull_request:" not in workflow, path
        assert "\n  pull_request_target:" not in workflow, path
        assert "\n  workflow_run:" not in workflow, path
        assert 'WORKFLOW_REF: ${{ github.ref }}' in workflow, path
        assert '"refs/heads/main"' in workflow, path
        assert "Refusing to run" in workflow, path


def test_all_external_actions_are_pinned_by_full_sha():
    for path in WORKFLOWS.glob("*.yaml"):
        workflow = read(path)
        for action, revision in ANY_ACTION.findall(workflow):
            assert action in APPROVED_ACTIONS, (
                f"{path}: unreviewed external action {action}"
            )
            assert revision == APPROVED_ACTIONS[action], (
                f"{path}: {action}@{revision} is not the reviewed pin"
            )


def test_bump_workflow_preserves_approval_boundary():
    workflow = read(WORKFLOWS / "run-bump.yaml")
    required_controls = (
        "persist-credentials: false",
        'git worktree add --detach "$GENERATOR_DIR" "$GITHUB_SHA"',
        'git show "$APPROVED_SHA:versions.env"',
        "python3 scripts/validate-apt-inputs.py",
        "working-directory: ${{ env.GENERATOR_DIR }}",
        "git diff --cached --name-only -z",
        "git ls-files --others --exclude-standard -z",
        "versions.env|locks/*",
        "find locks -type l",
        "core.hooksPath=/dev/null",
        'GIT_ASKPASS="$ASKPASS" GIT_TERMINAL_PROMPT=0',
        'trap \'rm -f "$ASKPASS"\' EXIT',
    )
    for control in required_controls:
        assert control in workflow, f"run-bump.yaml lost control: {control}"
    assert workflow.count("git ls-remote --exit-code origin") == 2
    assert "git checkout --detach \"$APPROVED_SHA\"" in workflow
    assert workflow.count("run: bash scripts/bump.sh") == 1
    assert workflow.count("core.hooksPath=/dev/null") == 2
    assert (
        'git -c core.hooksPath=/dev/null \\\n'
        '              push origin "HEAD:refs/heads/$APPROVED_BRANCH"'
    ) in workflow
    assert "gh auth setup-git" not in workflow
    assert "x-access-token:${GH_TOKEN}" not in workflow


def test_trusted_builds_checkout_the_exact_event_sha():
    for name in ("build-image.yaml", "verify-reproducible.yaml"):
        workflow = read(WORKFLOWS / name)
        assert "ref: ${{ github.sha }}" in workflow, name


def test_privileged_workflows_reject_untrusted_refs():
    monitor = read(WORKFLOWS / "monitor-upstream-releases.yaml")
    assert 'WORKFLOW_REF: ${{ github.ref }}' in monitor
    assert '"refs/heads/main"' in monitor
    assert monitor.count("ref: ${{ github.sha }}") == 2
    assert monitor.count("persist-credentials: false") == 2

    release = read(WORKFLOWS / "create-release.yaml")
    assert "git merge-base --is-ancestor" in release
    assert "persist-credentials: false" in release


def test_monitor_pr_creation_is_scoped_and_handles_detached_checkout():
    monitor = read(WORKFLOWS / "monitor-upstream-releases.yaml")
    workflow_header = monitor.split("\njobs:", 1)[0]
    check_job = monitor.split("\n  check-deps:", 1)[1].split(
        "\n  create-pr:", 1
    )[0]
    create_pr_job = monitor.split("\n  create-pr:", 1)[1]
    create_pr_step = monitor.split("- name: Create Pull Request", 1)[1]

    assert "contents: read" in workflow_header
    assert "contents: write" not in workflow_header
    assert "pull-requests: write" not in workflow_header
    assert "id-token: write" not in workflow_header
    assert "contents: write" in create_pr_job
    assert "pull-requests: write" in create_pr_job
    assert "scripts/check-updates.sh --update" in check_job
    assert "secrets." not in check_job
    assert "actions/upload-artifact@" in check_job
    assert "path: versions.env" in check_job
    assert "scripts/check-updates.sh" not in create_pr_job
    assert "actions/download-artifact@" in create_pr_job
    assert (
        create_pr_job.index("scripts/validate-monitor-update.py")
        < create_pr_job.index("secrets.RELEASE_MONITOR_PAT")
    )
    assert "versions.env /tmp/release-monitor/versions.env" in create_pr_job
    assert "base: main" in create_pr_step
    assert "add-paths: versions.env" in create_pr_step


def test_pr_workflows_are_read_only_and_secret_free():
    for path in WORKFLOWS.glob("*.yaml"):
        workflow = read(path)
        if "\n  pull_request:" not in workflow:
            continue
        assert "contents: read" in workflow, path
        assert "secrets." not in workflow, path
        assert "contents: write" not in workflow, path


def test_security_policy_runs_for_every_pull_request():
    workflow = read(WORKFLOWS / "test-release-notes.yaml")
    pull_request_trigger = workflow.split("workflow_dispatch:", 1)[0]
    assert "pull_request:" in pull_request_trigger
    assert "paths:" not in pull_request_trigger
    assert "for test_file in tests/test-*.py" in workflow
    assert 'python3 "${test_file}"' in workflow


def test_sensitive_paths_have_codeowners():
    codeowners = read(ROOT / ".github" / "CODEOWNERS")
    for path in (
        "/.github/CODEOWNERS",
        "/.github/PULL_REQUEST_TEMPLATE.md",
        "/.github/workflows/",
        "/AGENTS.md",
        "/CONTRIBUTING.md",
        "/Dockerfile",
        "/docs/contributor-ci-security.md",
        "/versions.env",
        "/locks/",
        "/scripts/",
        "/tests/test-monitor-lifecycle.py",
        "/tests/test-monitor-update-policy.py",
        "/tests/test-update-versions-env.py",
        "/tests/test-versions-contract.py",
    ):
        assert f"{path} @timothystewart6" in codeowners


def test_security_runbook_is_linked_from_entry_points():
    runbook = ROOT / "docs" / "contributor-ci-security.md"
    assert runbook.is_file()
    link = "docs/contributor-ci-security.md"
    for path in (
        ROOT / "README.md",
        ROOT / "CONTRIBUTING.md",
        WORKFLOWS / "run-bump.yaml",
    ):
        assert link in read(path), path


def main():
    tests = [
        test_self_hosted_workflows_have_trusted_entry_points,
        test_all_external_actions_are_pinned_by_full_sha,
        test_bump_workflow_preserves_approval_boundary,
        test_trusted_builds_checkout_the_exact_event_sha,
        test_privileged_workflows_reject_untrusted_refs,
        test_monitor_pr_creation_is_scoped_and_handles_detached_checkout,
        test_pr_workflows_are_read_only_and_secret_free,
        test_security_policy_runs_for_every_pull_request,
        test_sensitive_paths_have_codeowners,
        test_security_runbook_is_linked_from_entry_points,
    ]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"All {len(tests)} CI security policy tests passed!")


if __name__ == "__main__":
    main()
