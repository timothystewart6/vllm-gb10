# Repository Agent Instructions

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
