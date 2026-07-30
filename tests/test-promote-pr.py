#!/usr/bin/env python3
"""Behavior tests for the trusted fork pull request promotion script."""

import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import promote_pr  # noqa: E402


SHA = "a" * 40
OTHER_SHA = "b" * 40


def expect_error(function, text):
    try:
        function()
    except promote_pr.PromotionError as error:
        assert text in str(error), error
    else:
        raise AssertionError(f"Expected PromotionError containing {text!r}")


def review(login, state, commit_id=SHA):
    return {
        "user": {"login": login},
        "state": state,
        "commit_id": commit_id,
    }


def check_run(
    *,
    name=promote_pr.REQUIRED_CHECK_NAME,
    app_id=promote_pr.REQUIRED_CHECK_APP_ID,
    status="completed",
    conclusion="success",
):
    return {
        "name": name,
        "app": {"id": app_id},
        "status": status,
        "conclusion": conclusion,
    }


def test_positive_pr_number_validation():
    assert promote_pr.require_positive_pr_number("77") == 77
    for invalid in ("", "0", "-1", "1.0", " 7", "7 "):
        expect_error(
            lambda invalid=invalid: promote_pr.require_positive_pr_number(
                invalid
            ),
            "positive integer",
        )


def test_paginated_reviews_are_flattened_without_losing_later_pages():
    page_one = [review(f"user-{index}", "COMMENTED") for index in range(100)]
    decisive = review("maintainer", "APPROVED")
    combined = promote_pr.flatten_paginated_json([page_one, [decisive]])
    assert len(combined) == 101
    assert combined[-1] == decisive


def test_paginated_check_runs_are_flattened():
    pages = [
        {"check_runs": [check_run(name="other")]},
        {"check_runs": [check_run()]},
    ]
    combined = promote_pr.flatten_check_run_pages(pages)
    assert len(combined) == 2
    assert combined[-1]["name"] == promote_pr.REQUIRED_CHECK_NAME


def test_latest_review_per_reviewer_controls_effective_state():
    reviews = [
        review("maintainer", "CHANGES_REQUESTED"),
        review("maintainer", "APPROVED"),
    ]
    approver = promote_pr.validate_maintainer_approval(
        reviews,
        SHA,
        lambda login: "write",
    )
    assert approver == "maintainer"

    reversed_reviews = list(reversed(reviews))
    expect_error(
        lambda: promote_pr.validate_maintainer_approval(
            reversed_reviews,
            SHA,
            lambda login: "write",
        ),
        "CHANGES_REQUESTED",
    )


def test_approval_requires_current_maintainer_permission_and_exact_sha():
    permissions = {"outsider": "read", "maintainer": "admin"}
    reviews = [
        review("outsider", "APPROVED"),
        review("maintainer", "APPROVED", OTHER_SHA),
    ]
    expect_error(
        lambda: promote_pr.validate_maintainer_approval(
            reviews,
            SHA,
            permissions.__getitem__,
        ),
        "exact SHA",
    )
    reviews[-1]["commit_id"] = SHA
    assert (
        promote_pr.validate_maintainer_approval(
            reviews,
            SHA,
            permissions.__getitem__,
        )
        == "maintainer"
    )


def test_permission_lookup_errors_fail_closed():
    def unavailable(_login):
        raise promote_pr.PromotionError("permission API unavailable")

    expect_error(
        lambda: promote_pr.validate_maintainer_approval(
            [review("maintainer", "APPROVED")],
            SHA,
            unavailable,
        ),
        "permission API unavailable",
    )


def test_permission_api_distinguishes_outsiders_from_api_failures():
    class Runner:
        def __init__(self, result):
            self.result = result

        def run(self, _args, **_kwargs):
            return self.result

    outsider = promote_pr.GitHubClient(
        Runner(
            promote_pr.CommandResult(
                "gh: Not Found (HTTP 404)\n",
                returncode=1,
            )
        ),
        "owner/repo",
    )
    assert outsider.permission("outsider") == "none"

    unavailable = promote_pr.GitHubClient(
        Runner(
            promote_pr.CommandResult(
                "gh: service unavailable (HTTP 503)\n",
                returncode=1,
            )
        ),
        "owner/repo",
    )
    expect_error(
        lambda: unavailable.permission("maintainer"),
        "Could not determine current permission",
    )


def test_required_check_identity_and_result_are_enforced():
    promote_pr.validate_required_check([check_run()])
    promote_pr.validate_required_check([check_run(conclusion="neutral")])

    cases = (
        ([], "was not found"),
        ([check_run(name="other")], "was not found"),
        ([check_run(app_id=1)], "wrong app ID"),
        ([check_run(status="in_progress", conclusion=None)], "status"),
        ([check_run(conclusion="failure")], "conclusion"),
    )
    for checks, message in cases:
        expect_error(
            lambda checks=checks: promote_pr.validate_required_check(checks),
            message,
        )


def test_fetched_pull_ref_must_resolve_to_the_reviewed_sha():
    class Runner:
        def __init__(self, resolved):
            self.resolved = resolved
            self.calls = []

        def run(self, args, **_kwargs):
            self.calls.append(args)
            if args[:2] == ["git", "rev-parse"]:
                return promote_pr.CommandResult(f"{self.resolved}\n")
            return promote_pr.CommandResult("")

    matching = Runner(SHA)
    promote_pr.verify_fetched_pull_ref(matching, 77, SHA)
    assert matching.calls[0][-1].endswith(
        "refs/remotes/origin/pr-77-head"
    )

    expect_error(
        lambda: promote_pr.verify_fetched_pull_ref(Runner(OTHER_SHA), 77, SHA),
        "fetched head changed",
    )


def test_pr_url_parser_rejects_unrelated_output():
    parsed = promote_pr.parse_replacement_url(
        "notice\nhttps://github.com/owner/repo/pull/123\n",
        "owner/repo",
    )
    assert parsed == promote_pr.ReplacementPullRequest(
        123, "https://github.com/owner/repo/pull/123"
    )
    assert (
        promote_pr.parse_replacement_url(
            "https://github.com/attacker/repo/pull/123",
            "owner/repo",
        )
        is None
    )
    assert (
        promote_pr.parse_replacement_url("created something", "owner/repo")
        is None
    )


def test_replacement_body_documents_approval_required_runs():
    body = promote_pr.replacement_body(
        sample_context(),
        sample_pull_request(),
        "integration/pr-77-aaaaaaaaaaaa",
    )
    assert "approval-required" in body
    assert promote_pr.REQUIRED_CHECK_NAME in body
    assert str(promote_pr.REQUIRED_CHECK_APP_ID) in body


def test_replacement_failure_cleans_up_only_when_state_is_known():
    class FakeGitHub:
        def __init__(self, error):
            self.error = error
            self.deleted = []

        def create_replacement(self, _branch, _title, _body):
            raise self.error

        def delete_branch(self, branch):
            self.deleted.append(branch)

    branch = "integration/pr-77-aaaaaaaaaaaa"
    known_failure = FakeGitHub(promote_pr.PromotionError("not created"))
    expect_error(
        lambda: promote_pr.create_replacement_with_cleanup(
            known_failure, branch, "title", "body"
        ),
        "not created",
    )
    assert known_failure.deleted == [branch]

    ambiguous = FakeGitHub(
        promote_pr.ReplacementStateAmbiguousError("unknown state")
    )
    expect_error(
        lambda: promote_pr.create_replacement_with_cleanup(
            ambiguous, branch, "title", "body"
        ),
        "unknown state",
    )
    assert ambiguous.deleted == []


def sample_context():
    return promote_pr.PromotionContext(
        repository="owner/repo",
        actor="maintainer",
        workflow_ref="refs/heads/main",
        workflow_sha=OTHER_SHA,
        workflow_name="Promote fork PR",
        server_url="https://github.com",
        step_summary=None,
        pr_number=77,
    )


def sample_pull_request():
    return promote_pr.PullRequest(
        number=77,
        state="open",
        title="A change",
        head_ref="feature",
        head_sha=SHA,
        head_repo="contributor/repo",
        base_ref="main",
        base_repo="owner/repo",
        merged=False,
        draft=False,
        author="contributor",
        html_url="https://github.com/owner/repo/pull/77",
    )


def sample_pull_request_api():
    pr = sample_pull_request()
    return {
        "state": pr.state,
        "title": pr.title,
        "head": {
            "ref": pr.head_ref,
            "sha": pr.head_sha,
            "repo": {"full_name": pr.head_repo},
        },
        "base": {
            "ref": pr.base_ref,
            "repo": {"full_name": pr.base_repo},
        },
        "merged": pr.merged,
        "draft": pr.draft,
        "user": {"login": pr.author},
        "html_url": pr.html_url,
    }


class PromotionRunner:
    def __init__(self, *, pr_create_succeeds=True):
        self.calls = []
        self.branch_created = False
        self.branch_deleted = False
        self.comment_created = False
        self.pr_create_succeeds = pr_create_succeeds

    def run(self, args, *, check=True, **_kwargs):
        self.calls.append(args)
        result = self._result(args)
        if check and result.returncode:
            raise promote_pr.CommandError(
                args, result.returncode, result.stdout
            )
        return result

    def _result(self, args):
        if args[:2] == ["git", "fetch"]:
            return promote_pr.CommandResult("")
        if args[:2] == ["git", "rev-parse"]:
            return promote_pr.CommandResult(f"{SHA}\n")
        if args[:3] == ["gh", "pr", "create"]:
            if self.pr_create_succeeds:
                return promote_pr.CommandResult(
                    "https://github.com/owner/repo/pull/88\n"
                )
            return promote_pr.CommandResult(
                "pull request creation disabled\n",
                returncode=1,
            )
        if args[:2] != ["gh", "api"]:
            raise AssertionError(f"Unexpected command: {args}")

        path = args[2]
        if "/collaborators/" in path:
            return self._json({"permission": "write"})
        if path.endswith("/pulls/77"):
            return self._json(sample_pull_request_api())
        if "/pulls/77/reviews?" in path:
            return self._json([[review("maintainer", "APPROVED")]])
        if "/check-runs?" in path:
            return self._json([{"check_runs": [check_run()]}])
        if path.endswith("/git/refs") and "POST" in args:
            self.branch_created = True
            return promote_pr.CommandResult("")
        if "/git/ref/heads/" in path:
            if not self.branch_created:
                return promote_pr.CommandResult(
                    "gh: Not Found (HTTP 404)\n",
                    returncode=1,
                )
            return self._json({"object": {"sha": SHA}})
        if "/git/refs/heads/" in path and "DELETE" in args:
            self.branch_deleted = True
            return promote_pr.CommandResult("")
        if "/pulls?state=open" in path:
            return self._json([])
        if path.endswith("/issues/77/comments"):
            self.comment_created = True
            return promote_pr.CommandResult("")
        raise AssertionError(f"Unexpected API call: {args}")

    @staticmethod
    def _json(value):
        return promote_pr.CommandResult(json.dumps(value))


def test_complete_promotion_uses_only_trusted_script_commands():
    runner = PromotionRunner()
    promote_pr.promote(sample_context(), runner)
    assert runner.branch_created
    assert runner.comment_created
    assert not runner.branch_deleted
    commands = [" ".join(call) for call in runner.calls]
    assert not any("checkout" in command for command in commands)
    assert any("refs/pull/77/head:" in command for command in commands)


def test_complete_promotion_cleans_branch_after_definitive_pr_failure():
    runner = PromotionRunner(pr_create_succeeds=False)
    expect_error(
        lambda: promote_pr.promote(sample_context(), runner),
        "creation failed",
    )
    assert runner.branch_created
    assert runner.branch_deleted
    assert not runner.comment_created


def test_reverification_detects_any_pr_metadata_change():
    original = sample_pull_request()
    promote_pr.reverify_pull_request(original, original)
    changed = promote_pr.PullRequest(
        **{**original.__dict__, "head_sha": OTHER_SHA}
    )
    expect_error(
        lambda: promote_pr.reverify_pull_request(original, changed),
        "metadata changed",
    )


def main():
    tests = [
        test_positive_pr_number_validation,
        test_paginated_reviews_are_flattened_without_losing_later_pages,
        test_paginated_check_runs_are_flattened,
        test_latest_review_per_reviewer_controls_effective_state,
        test_approval_requires_current_maintainer_permission_and_exact_sha,
        test_permission_lookup_errors_fail_closed,
        test_permission_api_distinguishes_outsiders_from_api_failures,
        test_required_check_identity_and_result_are_enforced,
        test_fetched_pull_ref_must_resolve_to_the_reviewed_sha,
        test_pr_url_parser_rejects_unrelated_output,
        test_replacement_body_documents_approval_required_runs,
        test_replacement_failure_cleans_up_only_when_state_is_known,
        test_complete_promotion_uses_only_trusted_script_commands,
        test_complete_promotion_cleans_branch_after_definitive_pr_failure,
        test_reverification_detects_any_pr_metadata_change,
    ]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"All {len(tests)} promotion behavior tests passed!")


if __name__ == "__main__":
    main()
