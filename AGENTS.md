# Repository Agent Instructions

`AGENTS.md` is the canonical repository instruction source. `CLAUDE.md` is a
compatibility shim that imports this file. Keep shared instructions here and
do not duplicate them in tool-specific files.

## Required repository onboarding

Before opening an issue, editing code, or opening a pull request:

1. Read [Repository guide and change reasoning](docs/repository-guide.md),
   `README.md`, and the relevant section of `CONTRIBUTING.md`.
2. Read [Contributor CI security workflow](docs/contributor-ci-security.md)
   before changing workflows, scripts, `Dockerfile`, build inputs, locks,
   checksums, or release automation.
3. Inspect the working tree, the relevant implementation, its callers and
   consumers, nearby tests, and recent history. Do not reason from one failing
   line or workflow step in isolation.
4. Search open and closed issues and pull requests, plus relevant Actions runs
   when available. Link related work instead of creating an undocumented
   duplicate.
5. Classify the problem as repository build logic, automation, generated data,
   runner or environment behavior, or upstream component behavior. Confirm
   that it is in this repository's scope.
6. Record the invariant, evidence, root cause or bounded hypothesis, affected
   lifecycle stages, security impact, and validation plan before proposing a
   change.

Do not open an issue that lacks an exact affected version or commit, expected
and actual behavior, reproduction evidence, and related logs or workflow URLs
when they exist. Do not open a pull request until the failure is reproduced by
a test or a documented manual check. A regression test must fail for the
original reason and pass after the fix.

Before finalizing a pull request, inspect the complete diff and changed-file
list, run every applicable validation command, test the state produced by
automation when generated files are involved, and update documentation and
templates when the process or invariant changed. State any hardware-only or
post-merge validation that could not run in the pull request.

Before adding, removing, or changing a `versions.env` variable, read and follow
[Adding or changing a versions.env input](CONTRIBUTING.md#adding-or-changing-a-versionsenv-input).

Before changing release-monitor detection, fixtures, or PR creation, read and
follow [Automated release monitor lifecycle](CONTRIBUTING.md#automated-release-monitor-lifecycle).
Validation must apply a representative generated update and run the hosted
suite against that resulting repository state. Passing tests only against the
pre-update branch is not sufficient.

Do not treat a passing schema validation as complete integration. The variable
must participate in build revision accounting, build arguments, its consuming
script or lock seed, release metadata, documentation, and contract tests.

Preserve the trusted-runner boundary in
[Contributor CI security workflow](docs/contributor-ci-security.md). Pull
request code must never execute on the persistent Spark runner. `run-bump.yaml`
uses executable code only from trusted `main` and imports reviewed build inputs
as validated declarative data.
