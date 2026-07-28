# Repository Agent Instructions

Before adding, removing, or changing a `versions.env` variable, read and follow
[Adding or changing a versions.env input](CONTRIBUTING.md#adding-or-changing-a-versionsenv-input).

Do not treat a passing schema validation as complete integration. The variable
must participate in build revision accounting, build arguments, its consuming
script or lock seed, release metadata, documentation, and contract tests.

Preserve the trusted-runner boundary in
[Contributor CI security workflow](docs/contributor-ci-security.md). Pull
request code must never execute on the persistent Spark runner. `run-bump.yaml`
uses executable code only from trusted `main` and imports reviewed build inputs
as validated declarative data.
