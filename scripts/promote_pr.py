#!/usr/bin/env python3
"""Promote an approved fork pull request without executing contributor code."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence
from urllib.parse import quote, urlparse


REQUIRED_CHECK_NAME = "test"
REQUIRED_CHECK_APP_ID = 15368
MAINTAINER_PERMISSIONS = {"admin", "write"}
SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
PR_URL_PATTERN = re.compile(r"/pull/([1-9][0-9]*)/?$")


class PromotionError(RuntimeError):
    """A fail-closed promotion error safe to display in workflow logs."""


class CommandError(PromotionError):
    """A subprocess failure."""

    def __init__(self, args: Sequence[str], returncode: int, output: str):
        self.args_list = list(args)
        self.returncode = returncode
        self.output = output
        command = " ".join(args)
        detail = output.strip() or "no command output"
        super().__init__(f"Command failed ({returncode}): {command}\n{detail}")


class ReplacementStateAmbiguousError(PromotionError):
    """PR creation may have succeeded, so deleting its branch is unsafe."""


@dataclass(frozen=True)
class CommandResult:
    stdout: str
    returncode: int = 0


class CommandRunner:
    """Small subprocess boundary that can be replaced by a fake in tests."""

    def run(
        self,
        args: Sequence[str],
        *,
        check: bool = True,
        input_text: str | None = None,
    ) -> CommandResult:
        completed = subprocess.run(
            list(args),
            check=False,
            input=input_text,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        result = CommandResult(completed.stdout, completed.returncode)
        if check and result.returncode != 0:
            raise CommandError(args, result.returncode, result.stdout)
        return result


@dataclass(frozen=True)
class PromotionContext:
    repository: str
    actor: str
    workflow_ref: str
    workflow_sha: str
    workflow_name: str
    server_url: str
    step_summary: Path | None
    pr_number: int


@dataclass(frozen=True)
class PullRequest:
    number: int
    state: str
    title: str
    head_ref: str
    head_sha: str
    head_repo: str
    base_ref: str
    base_repo: str
    merged: bool
    draft: bool
    author: str
    html_url: str

    @classmethod
    def from_api(cls, number: int, value: dict[str, Any]) -> "PullRequest":
        try:
            return cls(
                number=number,
                state=value["state"],
                title=value["title"],
                head_ref=value["head"]["ref"],
                head_sha=value["head"]["sha"],
                head_repo=value["head"]["repo"]["full_name"],
                base_ref=value["base"]["ref"],
                base_repo=value["base"]["repo"]["full_name"],
                merged=value["merged"],
                draft=value["draft"],
                author=value["user"]["login"],
                html_url=value["html_url"],
            )
        except (KeyError, TypeError) as error:
            raise PromotionError(
                f"Pull request #{number} returned incomplete metadata."
            ) from error


@dataclass(frozen=True)
class ReplacementPullRequest:
    number: int
    url: str


def require_positive_pr_number(value: str) -> int:
    if not re.fullmatch(r"[1-9][0-9]*", value):
        raise PromotionError("pr_number must be a positive integer.")
    return int(value)


def require_trusted_workflow(context: PromotionContext) -> None:
    if context.workflow_ref != "refs/heads/main":
        raise PromotionError("Dispatch this workflow from main.")
    if not SHA_PATTERN.fullmatch(context.workflow_sha):
        raise PromotionError("The trusted workflow SHA is not a full commit SHA.")


def validate_pull_request(pr: PullRequest, repository: str) -> None:
    if pr.state != "open":
        raise PromotionError(
            f"PR #{pr.number} is {pr.state!r}, not 'open'."
        )
    if pr.merged:
        raise PromotionError(f"PR #{pr.number} is already merged.")
    if pr.draft:
        raise PromotionError(f"PR #{pr.number} is a draft.")
    if pr.base_ref != "main":
        raise PromotionError(
            f"PR #{pr.number} targets {pr.base_ref!r}, not 'main'."
        )
    if pr.base_repo != repository:
        raise PromotionError(
            f"PR #{pr.number} targets a different repository "
            f"{pr.base_repo!r}."
        )
    if pr.head_repo == repository:
        raise PromotionError(
            f"PR #{pr.number} is not a fork pull request."
        )
    if not SHA_PATTERN.fullmatch(pr.head_sha):
        raise PromotionError(
            f"PR #{pr.number} head SHA is not a full commit SHA."
        )


def flatten_paginated_json(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise PromotionError("Expected a JSON array from the paginated API.")
    if not value:
        return []
    if all(isinstance(item, dict) for item in value):
        return value
    if not all(isinstance(page, list) for page in value):
        raise PromotionError("Paginated API response has an unexpected shape.")
    flattened: list[dict[str, Any]] = []
    for page in value:
        if not all(isinstance(item, dict) for item in page):
            raise PromotionError(
                "Paginated API response contains a non-object item."
            )
        flattened.extend(page)
    return flattened


def flatten_check_run_pages(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        pages = [value]
    elif isinstance(value, list):
        pages = value
    else:
        raise PromotionError("Check-runs API returned an invalid response.")

    check_runs: list[dict[str, Any]] = []
    for page in pages:
        if not isinstance(page, dict) or not isinstance(
            page.get("check_runs"), list
        ):
            raise PromotionError("Check-runs API returned an invalid response.")
        if not all(isinstance(item, dict) for item in page["check_runs"]):
            raise PromotionError("Check-runs API returned an invalid item.")
        check_runs.extend(page["check_runs"])
    return check_runs


def effective_reviews(
    reviews: list[dict[str, Any]],
) -> OrderedDict[str, dict[str, str]]:
    effective: OrderedDict[str, dict[str, str]] = OrderedDict()
    for review in reviews:
        try:
            login = review["user"]["login"]
            state = review["state"]
        except (KeyError, TypeError) as error:
            raise PromotionError("A review returned incomplete metadata.") from error
        if not isinstance(login, str) or not isinstance(state, str):
            raise PromotionError("A review returned invalid metadata.")
        effective[login] = {
            "state": state,
            "commit_id": review.get("commit_id") or "",
        }
    return effective


def validate_maintainer_approval(
    reviews: list[dict[str, Any]],
    head_sha: str,
    permission_lookup: Callable[[str], str],
) -> str:
    if not reviews:
        raise PromotionError("No reviews were found.")

    approvers: list[str] = []
    for reviewer, review in effective_reviews(reviews).items():
        permission = permission_lookup(reviewer)
        if permission not in MAINTAINER_PERMISSIONS:
            print(
                f"SKIP: {reviewer} has {permission!r} permission, "
                "not write or admin."
            )
            continue

        state = review["state"]
        if state == "CHANGES_REQUESTED":
            raise PromotionError(
                f"Maintainer {reviewer} has an effective "
                "CHANGES_REQUESTED review."
            )
        if state == "APPROVED" and review["commit_id"] == head_sha:
            approvers.append(reviewer)

    if not approvers:
        raise PromotionError(
            f"No current maintainer approval applies to exact SHA {head_sha}."
        )
    return approvers[-1]


def validate_required_check(check_runs: list[dict[str, Any]]) -> None:
    matching = [
        check for check in check_runs if check.get("name") == REQUIRED_CHECK_NAME
    ]
    if not matching:
        raise PromotionError(
            f"Required check {REQUIRED_CHECK_NAME!r} was not found."
        )

    failures: list[str] = []
    for check in matching:
        app_id = (check.get("app") or {}).get("id")
        if app_id != REQUIRED_CHECK_APP_ID:
            failures.append(f"wrong app ID {app_id!r}")
            continue
        if check.get("status") != "completed":
            failures.append(f"status {check.get('status')!r}")
            continue
        if check.get("conclusion") not in {"success", "neutral"}:
            failures.append(f"conclusion {check.get('conclusion')!r}")

    if failures:
        details = ", ".join(failures)
        raise PromotionError(
            f"Required check {REQUIRED_CHECK_NAME!r} did not pass: {details}."
        )


def parse_replacement_url(
    output: str,
    repository: str,
) -> ReplacementPullRequest | None:
    for line in reversed(output.splitlines()):
        candidate = line.strip()
        match = PR_URL_PATTERN.search(candidate)
        parsed = urlparse(candidate)
        expected_path = f"/{repository}/pull/{match.group(1)}" if match else ""
        if (
            match
            and parsed.scheme in {"http", "https"}
            and parsed.netloc
            and parsed.path.rstrip("/") == expected_path
        ):
            return ReplacementPullRequest(int(match.group(1)), candidate)
    return None


class GitHubClient:
    def __init__(self, runner: CommandRunner, repository: str):
        self.runner = runner
        self.repository = repository

    def api_json(
        self,
        path: str,
        *extra: str,
        check: bool = True,
    ) -> Any:
        result = self.runner.run(
            ["gh", "api", path, *extra],
            check=check,
        )
        if result.returncode != 0:
            return None
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise PromotionError(
                f"GitHub API returned invalid JSON for {path}."
            ) from error

    def pull_request(self, number: int) -> PullRequest:
        value = self.api_json(f"/repos/{self.repository}/pulls/{number}")
        if not isinstance(value, dict):
            raise PromotionError(
                f"Pull request #{number} API result is not an object."
            )
        return PullRequest.from_api(number, value)

    def permission(self, login: str) -> str:
        path = (
            f"/repos/{self.repository}/collaborators/"
            f"{quote(login)}/permission"
        )
        result = self.runner.run(["gh", "api", path], check=False)
        if result.returncode != 0:
            if re.search(r"\(HTTP 404\)\s*$", result.stdout.strip()):
                return "none"
            detail = result.stdout.strip() or "no command output"
            raise PromotionError(
                f"Could not determine current permission for {login}: {detail}"
            )
        try:
            value = json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise PromotionError(
                f"Permission API returned invalid JSON for {login}."
            ) from error
        if not isinstance(value, dict) or not isinstance(
            value.get("permission"), str
        ):
            raise PromotionError(
                f"Could not determine current permission for {login}."
            )
        return value["permission"]

    def reviews(self, number: int) -> list[dict[str, Any]]:
        value = self.api_json(
            f"/repos/{self.repository}/pulls/{number}/reviews?per_page=100",
            "--paginate",
            "--slurp",
        )
        return flatten_paginated_json(value)

    def check_runs(self, sha: str) -> list[dict[str, Any]]:
        value = self.api_json(
            f"/repos/{self.repository}/commits/{sha}/check-runs?per_page=100",
            "--paginate",
            "--slurp",
        )
        return flatten_check_run_pages(value)

    def create_branch(self, branch: str, sha: str) -> None:
        self.runner.run(
            [
                "gh",
                "api",
                f"/repos/{self.repository}/git/refs",
                "--method",
                "POST",
                "--field",
                f"ref=refs/heads/{branch}",
                "--field",
                f"sha={sha}",
                "--silent",
            ]
        )

    def branch_sha(self, branch: str, *, check: bool = True) -> str | None:
        value = self.api_json(
            f"/repos/{self.repository}/git/ref/heads/{quote(branch, safe='/')}",
            check=check,
        )
        if value is None:
            return None
        try:
            sha = value["object"]["sha"]
        except (KeyError, TypeError) as error:
            raise PromotionError(
                f"Branch {branch!r} returned incomplete metadata."
            ) from error
        if not isinstance(sha, str):
            raise PromotionError(f"Branch {branch!r} returned an invalid SHA.")
        return sha

    def delete_branch(self, branch: str) -> None:
        result = self.runner.run(
            [
                "gh",
                "api",
                f"/repos/{self.repository}/git/refs/heads/"
                f"{quote(branch, safe='/')}",
                "--method",
                "DELETE",
                "--silent",
            ],
            check=False,
        )
        if result.returncode != 0:
            raise PromotionError(
                f"Could not clean up integration branch {branch!r}: "
                f"{result.stdout.strip()}"
            )

    def find_open_replacement(
        self, branch: str
    ) -> ReplacementPullRequest | None:
        owner = self.repository.split("/", 1)[0]
        head = quote(f"{owner}:{branch}", safe="")
        value = self.api_json(
            f"/repos/{self.repository}/pulls"
            f"?state=open&base=main&head={head}&per_page=10"
        )
        if not isinstance(value, list):
            raise PromotionError(
                "Replacement pull request lookup returned an invalid response."
            )
        if not value:
            return None
        try:
            return ReplacementPullRequest(
                number=int(value[0]["number"]),
                url=value[0]["html_url"],
            )
        except (KeyError, TypeError, ValueError) as error:
            raise PromotionError(
                "Replacement pull request returned incomplete metadata."
            ) from error

    def create_replacement(
        self,
        branch: str,
        title: str,
        body: str,
    ) -> ReplacementPullRequest:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            prefix="promote-pr-body-",
            suffix=".md",
        ) as body_file:
            body_file.write(body)
            body_file.flush()
            result = self.runner.run(
                [
                    "gh",
                    "pr",
                    "create",
                    "--repo",
                    self.repository,
                    "--base",
                    "main",
                    "--head",
                    branch,
                    "--title",
                    title,
                    "--body-file",
                    body_file.name,
                    "--label",
                    "enhancement",
                ],
                check=False,
            )

        parsed = (
            parse_replacement_url(result.stdout, self.repository)
            if result.returncode == 0
            else None
        )
        if parsed is not None:
            return parsed

        try:
            recovered = self.find_open_replacement(branch)
        except PromotionError as error:
            raise ReplacementStateAmbiguousError(
                "Could not determine whether replacement pull request "
                "creation succeeded. The integration branch was retained "
                "for manual inspection."
            ) from error
        if recovered is not None:
            print(
                "Recovered the replacement PR after ambiguous "
                "gh pr create output."
            )
            return recovered

        detail = result.stdout.strip() or "no command output"
        raise PromotionError(
            "Replacement pull request creation failed and no matching open "
            f"pull request exists: {detail}"
        )

    def comment(self, number: int, body: str) -> None:
        self.runner.run(
            [
                "gh",
                "api",
                f"/repos/{self.repository}/issues/{number}/comments",
                "--method",
                "POST",
                "--field",
                f"body={body}",
                "--silent",
            ]
        )


def replacement_body(
    context: PromotionContext,
    pr: PullRequest,
    branch: str,
) -> str:
    return f"""## Source pull request

This is an integration pull request for fork PR #{pr.number}.

| Detail | Value |
|---|---|
| Source PR | {pr.html_url} |
| Author | @{pr.author} |
| Source branch | `{pr.head_ref}` |
| Approved SHA | `{pr.head_sha}` |
| Required check | `{REQUIRED_CHECK_NAME}` from app ID {REQUIRED_CHECK_APP_ID} |
| Promoted by | @{context.actor} |

## Security

- Promoted by the trusted `{context.workflow_name}` workflow from `main`.
- The integration branch was created at the exact reviewed commit SHA.
- No contributor code was executed during promotion.
- A new push to the source pull request requires a fresh review and promotion.

## Next steps

1. If GitHub marks this pull request's workflow run as approval-required,
   a maintainer must approve that hosted run.
2. Wait for required checks to complete on this pull request.
3. Review the diff, including any generated files.
4. Dispatch **Run bump.sh** from `main` with branch `{branch}` and SHA
   `{pr.head_sha}`.
5. Review the generated changes and merge this pull request.
"""


def source_comment(
    context: PromotionContext,
    pr: PullRequest,
    branch: str,
    replacement: ReplacementPullRequest,
) -> str:
    branch_url = (
        f"{context.server_url}/{context.repository}/tree/{quote(branch, safe='/')}"
    )
    return f""":rocket: Promoted to integration pull request \
[{replacement.number}]({replacement.url}).

Integration branch: [{branch}]({branch_url})

Approved SHA: `{pr.head_sha}`

Promoted by @{context.actor}. Any new push requires a fresh review and
promotion.
"""


def promotion_summary(
    context: PromotionContext,
    pr: PullRequest,
    branch: str,
    replacement: ReplacementPullRequest,
) -> str:
    return f"""## Promotion complete

| Item | Value |
|---|---|
| Source PR | [#{pr.number}]({pr.html_url}) |
| Author | @{pr.author} |
| Approved SHA | `{pr.head_sha}` |
| Integration branch | `{branch}` |
| Replacement PR | [#{replacement.number}]({replacement.url}) |
| Promoted by | @{context.actor} |
"""


def verify_fetched_pull_ref(
    runner: CommandRunner,
    pr_number: int,
    expected_sha: str,
) -> None:
    remote_ref = f"refs/remotes/origin/pr-{pr_number}-head"
    runner.run(
        [
            "git",
            "fetch",
            "--no-tags",
            "origin",
            f"refs/pull/{pr_number}/head:{remote_ref}",
        ]
    )
    fetched_sha = runner.run(
        ["git", "rev-parse", "--verify", remote_ref]
    ).stdout.strip()
    if fetched_sha != expected_sha:
        raise PromotionError(
            f"PR #{pr_number} fetched head changed from {expected_sha} "
            f"to {fetched_sha}. A new review is required."
        )


def reverify_pull_request(original: PullRequest, current: PullRequest) -> None:
    if current != original:
        raise PromotionError(
            f"PR #{original.number} metadata changed during promotion. "
            "A new review is required."
        )


def write_summary(path: Path | None, content: str) -> None:
    if path is None:
        return
    with path.open("a", encoding="utf-8") as summary:
        summary.write(content)


def create_replacement_with_cleanup(
    github: GitHubClient,
    branch: str,
    title: str,
    body: str,
) -> ReplacementPullRequest:
    """Create the PR and delete only a branch known to be orphaned."""
    try:
        return github.create_replacement(branch, title, body)
    except ReplacementStateAmbiguousError:
        raise
    except Exception:
        github.delete_branch(branch)
        raise


def promote(context: PromotionContext, runner: CommandRunner) -> None:
    require_trusted_workflow(context)
    github = GitHubClient(runner, context.repository)

    actor_permission = github.permission(context.actor)
    if actor_permission not in MAINTAINER_PERMISSIONS:
        raise PromotionError(
            f"Dispatch actor {context.actor} has {actor_permission!r} "
            "permission, not write or admin."
        )

    pr = github.pull_request(context.pr_number)
    validate_pull_request(pr, context.repository)
    approver = validate_maintainer_approval(
        github.reviews(pr.number),
        pr.head_sha,
        github.permission,
    )
    validate_required_check(github.check_runs(pr.head_sha))
    print(
        f"Verified PR #{pr.number} at {pr.head_sha}, "
        f"approved by maintainer {approver}."
    )

    current = github.pull_request(pr.number)
    validate_pull_request(current, context.repository)
    reverify_pull_request(pr, current)
    approver = validate_maintainer_approval(
        github.reviews(pr.number),
        pr.head_sha,
        github.permission,
    )
    print(f"Re-verified approval from {approver}.")

    verify_fetched_pull_ref(runner, pr.number, pr.head_sha)
    branch = f"integration/pr-{pr.number}-{pr.head_sha[:12]}"
    if github.branch_sha(branch, check=False) is not None:
        raise PromotionError(f"Integration branch {branch!r} already exists.")

    github.create_branch(branch, pr.head_sha)
    verified_sha = github.branch_sha(branch)
    if verified_sha != pr.head_sha:
        raise PromotionError(
            f"Integration branch {branch!r} points to {verified_sha}, "
            f"not approved SHA {pr.head_sha}."
        )

    replacement = create_replacement_with_cleanup(
        github,
        branch,
        pr.title,
        replacement_body(context, pr, branch),
    )

    write_summary(
        context.step_summary,
        promotion_summary(context, pr, branch, replacement),
    )
    try:
        github.comment(
            pr.number,
            source_comment(context, pr, branch, replacement),
        )
    except PromotionError as error:
        print(
            "WARNING: promotion succeeded, but the source PR comment "
            f"could not be created: {error}",
            file=sys.stderr,
        )
    print(
        f"Promoted PR #{pr.number} to replacement PR "
        f"#{replacement.number}: {replacement.url}"
    )


def context_from_environment(environment: dict[str, str]) -> PromotionContext:
    required = (
        "PROMOTE_PR_NUMBER",
        "GITHUB_REPOSITORY",
        "GITHUB_ACTOR",
        "GITHUB_REF",
        "GITHUB_SHA",
        "GITHUB_WORKFLOW",
        "GITHUB_SERVER_URL",
    )
    missing = [name for name in required if not environment.get(name)]
    if missing:
        raise PromotionError(
            f"Required environment variables are missing: {', '.join(missing)}."
        )
    summary_value = environment.get("GITHUB_STEP_SUMMARY")
    return PromotionContext(
        repository=environment["GITHUB_REPOSITORY"],
        actor=environment["GITHUB_ACTOR"],
        workflow_ref=environment["GITHUB_REF"],
        workflow_sha=environment["GITHUB_SHA"],
        workflow_name=environment["GITHUB_WORKFLOW"],
        server_url=environment["GITHUB_SERVER_URL"].rstrip("/"),
        step_summary=Path(summary_value) if summary_value else None,
        pr_number=require_positive_pr_number(environment["PROMOTE_PR_NUMBER"]),
    )


def main() -> int:
    try:
        promote(context_from_environment(dict(os.environ)), CommandRunner())
    except PromotionError as error:
        print(f"Refusing promotion: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
