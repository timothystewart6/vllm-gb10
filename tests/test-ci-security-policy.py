#!/usr/bin/env python3
"""Prevent dangerous regressions in workflows that use persistent runners."""

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
ANY_ACTION = re.compile(r"^\s*uses:\s*([^#\s]+)@([^#\s]+)\s*$", re.MULTILINE)


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
            assert re.fullmatch(r"[0-9a-f]{40}", revision), (
                f"{path}: {action}@{revision} is not pinned by full SHA"
            )


def test_bump_workflow_preserves_approval_boundary():
    workflow = read(WORKFLOWS / "run-bump.yaml")
    required_controls = (
        "persist-credentials: false",
        '[[ "$(git rev-parse HEAD)" != "$APPROVED_SHA" ]]',
        "git diff --cached --name-only -z",
        "git ls-files --others --exclude-standard -z",
        "versions.env|locks/*",
        "find locks -type l",
        "core.hooksPath=/dev/null",
    )
    for control in required_controls:
        assert control in workflow, f"run-bump.yaml lost control: {control}"
    assert workflow.count("git ls-remote --exit-code origin") == 2


def test_trusted_builds_checkout_the_exact_event_sha():
    for name in ("build-image.yaml", "verify-reproducible.yaml"):
        workflow = read(WORKFLOWS / name)
        assert "ref: ${{ github.sha }}" in workflow, name


def test_security_policy_runs_for_every_workflow_change():
    workflow = read(WORKFLOWS / "test-release-notes.yaml")
    assert "- .github/workflows/**" in workflow
    assert "- tests/test-ci-security-policy.py" in workflow
    assert "python3 tests/test-ci-security-policy.py" in workflow


def main():
    tests = [
        test_self_hosted_workflows_have_trusted_entry_points,
        test_all_external_actions_are_pinned_by_full_sha,
        test_bump_workflow_preserves_approval_boundary,
        test_trusted_builds_checkout_the_exact_event_sha,
        test_security_policy_runs_for_every_workflow_change,
    ]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"All {len(tests)} CI security policy tests passed!")


if __name__ == "__main__":
    main()
