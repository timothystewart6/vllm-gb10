#!/usr/bin/env python3
"""Enforce complete downstream integration for every versions.env key."""

import importlib.util
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "versions_env", ROOT / "scripts" / "versions_env.py"
)
assert SPEC and SPEC.loader
VERSIONS_ENV = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VERSIONS_ENV)

sys.path.insert(0, str(ROOT / "scripts"))
from versions_diff import COMPONENT_LABELS


def read(path):
    return (ROOT / path).read_text(encoding="utf-8")


def test_build_revision_roles_are_exhaustive():
    roles = (
        VERSIONS_ENV.BUILD_RESET_INPUT_KEYS,
        VERSIONS_ENV.REVIEWED_RESOLUTION_INPUT_KEYS,
        {VERSIONS_ENV.BUILD_COUNTER_KEY},
        VERSIONS_ENV.BUILD_INCREMENT_INPUT_KEYS,
    )
    assert set().union(*roles) == VERSIONS_ENV.EXPECTED_KEYS
    for index, role in enumerate(roles):
        for other in roles[index + 1:]:
            assert role.isdisjoint(other)

    assert VERSIONS_ENV.BUILD_RESET_INPUT_KEYS == {"VLLM_REF"}
    assert VERSIONS_ENV.REVIEWED_RESOLUTION_INPUT_KEYS == {"VLLM_COMMIT"}

    bump = read("scripts/bump.sh")
    assert "--list-build-inputs increment" in bump
    assert '--new-vllm-ref "${VLLM_REF}"' in bump
    assert '--reviewed-vllm-commit "${REVIEWED_VLLM_COMMIT}"' in bump
    assert '--resolved-vllm-commit "${VLLM_COMMIT}"' in bump


def test_every_schema_key_is_emitted_as_a_build_argument():
    build_args = read("scripts/build-args.sh")
    emitted = set(re.findall(r"^_arg ([A-Z][A-Z0-9_]*)$", build_args, re.MULTILINE))
    assert emitted == VERSIONS_ENV.EXPECTED_KEYS, (
        f"missing={sorted(VERSIONS_ENV.EXPECTED_KEYS - emitted)}, "
        f"unexpected={sorted(emitted - VERSIONS_ENV.EXPECTED_KEYS)}"
    )


def test_every_version_has_a_label_and_release_metadata():
    missing_labels = VERSIONS_ENV.VERSION_KEYS - COMPONENT_LABELS.keys()
    assert not missing_labels, f"missing display labels: {sorted(missing_labels)}"

    metadata = read("scripts/render-metadata.sh")
    missing_metadata = {
        key for key in VERSIONS_ENV.VERSION_KEYS if f"${{{key}}}" not in metadata
    }
    assert not missing_metadata, (
        f"missing build metadata: {sorted(missing_metadata)}"
    )


def test_every_version_has_a_trusted_build_consumer():
    consumers = "\n".join(
        read(path)
        for path in (
            "scripts/bump.sh",
            "scripts/check-updates.sh",
            "Dockerfile",
        )
    )
    missing_consumers = {
        key for key in VERSIONS_ENV.VERSION_KEYS if key not in consumers
    }
    assert not missing_consumers, (
        f"missing trusted build consumers: {sorted(missing_consumers)}"
    )


def test_guidance_is_visible_to_humans_agents_and_reviewers():
    anchor = "adding-or-changing-a-versionsenv-input"
    assert "### Adding or changing a versions.env input" in read("CONTRIBUTING.md")
    assert anchor in read("AGENTS.md")
    assert anchor in read("versions.env")
    assert anchor in read(".github/PULL_REQUEST_TEMPLATE.md")


def test_repository_onboarding_is_visible_and_enforced():
    guide = "docs/repository-guide.md"
    agents = read("AGENTS.md")
    contributing = read("CONTRIBUTING.md")
    pull_request = read(".github/PULL_REQUEST_TEMPLATE.md")
    bug_report = read(".github/ISSUE_TEMPLATE/bug_report.yml")
    version_bump = read(".github/ISSUE_TEMPLATE/version_bump.yml")
    codeowners = read(".github/CODEOWNERS")

    assert guide in agents
    assert guide in contributing
    assert guide in read("README.md")
    assert "/.github/ISSUE_TEMPLATE/ @timothystewart6" in codeowners
    assert "/docs/repository-guide.md @timothystewart6" in codeowners
    for section in (
        "## Reasoning and evidence",
        "## Lifecycle impact",
        "## Security impact",
        "## Validation",
    ):
        assert section in pull_request
    for template in (bug_report, version_bump):
        assert "open and closed issues and pull requests" in template
        assert "id: related-work" in template
        assert "id: preflight" in template


def test_agent_instructions_have_one_canonical_source():
    agents = read("AGENTS.md")
    claude = read("CLAUDE.md")
    codeowners = read(".github/CODEOWNERS")

    assert "canonical repository instruction source" in agents
    assert claude.startswith("@AGENTS.md\n")
    assert len(claude.encode("utf-8")) < 512
    assert "/CLAUDE.md @timothystewart6" in codeowners


def test_hosted_ci_discovers_every_python_test():
    workflow = read(".github/workflows/test-release-notes.yaml")
    assert "for test_file in tests/test-*.py" in workflow
    assert 'python3 "${test_file}"' in workflow


def main():
    tests = [
        test_build_revision_roles_are_exhaustive,
        test_every_schema_key_is_emitted_as_a_build_argument,
        test_every_version_has_a_label_and_release_metadata,
        test_every_version_has_a_trusted_build_consumer,
        test_guidance_is_visible_to_humans_agents_and_reviewers,
        test_repository_onboarding_is_visible_and_enforced,
        test_agent_instructions_have_one_canonical_source,
        test_hosted_ci_discovers_every_python_test,
    ]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"All {len(tests)} versions contract tests passed!")


if __name__ == "__main__":
    main()
