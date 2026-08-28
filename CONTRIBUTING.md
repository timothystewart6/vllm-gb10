# Contributing

This repo builds and ships a reproducible vLLM Docker image for the NVIDIA DGX
Spark (GB10 / sm_121a). Contributions are welcome, but the scope is intentionally
narrow: keep the build working, keep the pins current, keep it reproducible.

Start with [Repository guide and change reasoning](docs/repository-guide.md).
It maps inputs, trusted automation, generated outputs, tests, publishing, and
the evidence required before opening an issue or pull request.

## How builds work

All version inputs live in [`versions.env`](versions.env). Every field that can
be pinned by commit SHA is pinned - there are no floating tags in the build.

The lockfiles under [`locks/`](locks/) and build checksums under
[`checksums/`](checksums/) must be regenerated whenever `versions.env` changes.
This is done by [`scripts/bump.sh`](scripts/bump.sh), which **must run on an
aarch64 DGX Spark** (it compiles and resolves platform-specific dependencies).

### Bumping a component

1. Edit the relevant `_REF` line in `versions.env` (e.g. `VLLM_REF=v0.21.0`) on a branch.
2. Open a PR. GitHub-hosted checks validate the change without executing
   contributor code on a persistent DGX Spark runner.
3. A maintainer reviews an exact commit SHA. For a fork PR, the maintainer
   dispatches **Promote fork PR** from `main`. The workflow verifies the review,
   required check, and fetched SHA before creating the upstream integration
   branch and replacement PR.
4. If GitHub marks the replacement PR's hosted run as approval-required, a
   maintainer approves it and waits for the required check to pass.
5. The maintainer dispatches `run-bump.yaml` from `main` with the upstream
   branch and reviewed 40-character SHA. The workflow refuses to run if the
   branch moved after review.
6. Review the generated `_COMMIT` SHAs, `GB10_BUILD`, and lockfile diff on the
   integration PR, then merge that PR. Do not merge the original fork PR after
   promotion.

You do not need to SSH into the Spark or run anything locally.

**Do not edit lockfiles by hand.** They are generated outputs.

## Automated release monitor lifecycle

`Monitor Upstream Releases` is complete only when the generated dependency PR
passes the same hosted checks required for merge. A green monitor workflow run
proves that detection, validation, artifact transfer, and PR creation worked.
It does not prove that the repository remains valid after the candidate values
are applied.

The PyTorch companion set is one compatibility unit: Torch, TorchVision,
TorchAudio, and the exact Triton dependency declared by the selected Torch
release must be updated together. The monitor must never select Triton's
independent PyPI latest release.

The full lifecycle is:

1. Run the monitor from `main`. Upstream data is processed in the read-only,
   secret-free job.
2. A fresh hosted runner validates the candidate `versions.env` against the
   exact trusted `main` SHA, then creates a PR containing only that file.
3. Run every hosted test against the generated PR state. This is the first
   validation of the repository after the candidate has been applied.
4. Review the generated PR's exact 40-character head SHA.
5. Dispatch `run-bump.yaml` from `main` with the dependency branch and reviewed
   SHA. Trusted `main` code resolves commits, increments or resets
   `GB10_BUILD`, regenerates locks, validates output paths, and pushes the
   generated commit back to the branch.
6. Run hosted tests again and review the resolved SHAs, build number, and
   lockfile changes.
7. Merge the dependency PR. The `versions.env` or lockfile change triggers the
   trusted `main` image build, verification, and release jobs.

Tests for monitor detection must use an explicit deterministic baseline for
every monitored key. They must not inherit mutable production values while
also asserting fixed mock outputs or update counts. The baseline key set must
equal the production monitor allowlist so a newly watched key fails closed
until its fixture and downstream expectations are added.

For a monitor or bump change, test both sides of the transition:

- run the behavior against the current trusted input;
- apply each allowed generated value change;
- validate the resulting complete `versions.env`;
- verify duplicate detection, PR-body labels, build-number behavior, build
  arguments, release metadata, and workflow handoff order;
- run the complete hosted suite against at least one representative generated
  repository state.

If a generated dependency PR exposes an automation-test defect, fix the test
or trusted automation in a separate PR. Close the generated dependency PR,
merge the automation fix, and rerun the monitor from the new `main`. Do not add
unrelated executable changes to the generated dependency PR.

### Adding or changing a versions.env input

`versions.env` is an integration contract, not only a list of values. Adding,
removing, or renaming a key affects validation, image revisioning, lock
generation, build arguments, metadata, release notes, and tests.

Complete this checklist in the same change:

1. Add the key to `versions.env` and to the strict schema in
   `scripts/versions_env.py`. Add an exact-value allowlist or specialized
   validation when a generic numeric version is not sufficient.
2. Confirm its `GB10_BUILD` behavior. The schema classifies every key as a
   release-ref reset input, a reviewed resolved-commit input, the build counter,
   or an ordinary increment input. New keys fail closed into increment
   behavior.
3. Emit the key from `scripts/build-args.sh`, even when it is metadata-only.
4. Add the key to the trusted consumer that uses it, such as a lock seed in
   `scripts/bump.sh`, a Docker build stage, or an update lookup in
   `scripts/check-updates.sh`.
5. Add a human-readable label in `scripts/versions_diff.py` and include numeric
   version inputs in `scripts/render-metadata.sh`. Update release-note output
   when the component belongs in the published component table.
6. Update test fixtures and add behavior coverage for the consumer. If the key
   is monitored, add it to the deterministic monitor fixture and generated-PR
   lifecycle coverage. Do not add a one-off test that only checks for a string
   when the behavior can be derived from the schema.
7. Run every hosted test with `for test_file in tests/test-*.py; do python3
   "$test_file"; done`, plus shell syntax, ShellCheck, actionlint, and
   `git diff --check`.
8. Review the generated `versions.env` and lockfile changes before merge.

`tests/test-versions-contract.py` enforces the schema partition, build-argument
coverage, trusted consumer coverage, display labels, release metadata,
documentation links, and automatic test discovery. Adding a schema key without
completing those integration points fails hosted CI.

The trusted-runner model creates one important bootstrap rule. A pull request's
modified `bump.sh` does not run on the persistent Spark. `run-bump.yaml` runs
the version from trusted `main` and imports only reviewed declarative inputs.
If one change needs to teach trusted automation about a new key, merge that
automation and schema change first. Change the new input in a follow-up pull
request after the updated `bump.sh` is available on trusted `main`. An explicit
branch `GB10_BUILD` value does not override trusted build-number policy.

Never weaken the trusted-main execution boundary to make a new variable easier
to bootstrap.

### Maintainer approval flow

Contributor code is not executed by `run-bump.yaml`. The workflow imports only
`versions.env`, `Dockerfile`, `locks/apt-sources.list`, and
`locks/apt-packages.txt` as declarative data, validates them, and runs scripts
from the exact trusted `main` workflow revision. Before manual dispatch:

1. Review every changed executable file at the exact SHA, especially
   workflows, shell scripts, Python scripts, the Dockerfile, and build inputs.
2. Dispatch **Promote fork PR** from `main` with the fork pull request number.
   The workflow creates the integration branch and replacement pull request
   only after re-verifying the reviewed SHA.
3. Approve the replacement pull request's hosted workflow run if GitHub
   requires it, then wait for the required check.
4. Confirm the integration branch head equals the reviewed SHA.
5. In Actions, select **Run bump.sh**, choose the `main` workflow ref, and
   enter the integration branch and exact SHA.

The workflow checks the SHA before execution and again before pushing. Any new
contributor commit invalidates the approval and requires another review.

See [Contributor CI security workflow](docs/contributor-ci-security.md) for the
complete trust model, fork-promotion commands, failure handling, and required
repository settings.

## What is and is not in scope

This repo builds and ships vLLM. It does not patch or modify upstream component
behavior. If something is broken in vLLM itself, NCCL, FlashInfer, or PyTorch,
the fix belongs in that project's repo - not here.

| In scope | Out of scope |
|---|---|
| Fixing build failures on GB10 | Porting to other GPU architectures |
| Bumping pinned component versions | Adding new Python packages to the image |
| CI workflow improvements | Changing the base OS |
| Reproducibility fixes | Runtime configuration / serving scripts |
| GB10-specific workarounds with no upstream path | Patching upstream component behavior |

If the issue is upstream, please file it there directly:

- vLLM bugs: [vllm-project/vllm](https://github.com/vllm-project/vllm/issues)
- NCCL bugs: [NVIDIA/nccl](https://github.com/NVIDIA/nccl/issues)
- FlashInfer bugs: [flashinfer-ai/flashinfer](https://github.com/flashinfer-ai/flashinfer/issues)

## Opening a PR

- Target `main`.
- Search open and closed issues and pull requests before starting.
- Explain the invariant that failed, the root cause or bounded hypothesis, and
  every upstream and downstream stage inspected.
- Add a test for every behavior change. A regression must fail for the
  original reason and pass with the fix. For generated changes, test the
  resulting repository state. A PR that changes behavior without a test will
  not be merged. If a change has no testable behavior, state that explicitly
  in the PR.
- Run the hosted test suite with `for test_file in tests/test-*.py; do python3
  "$test_file"; done`, plus shell syntax, ShellCheck, actionlint, and
  `git diff --check`, and include the results.
- Complete the reasoning, lifecycle, security, and validation sections in the
  pull request template.
- Include the output of the smoke test (`tests/smoke-test.sh`) if you changed
  anything in the Dockerfile or build scripts.
- Keep commits focused - one logical change per PR.

## Security

Do not open public issues for security vulnerabilities. See
[SECURITY.md](SECURITY.md).
