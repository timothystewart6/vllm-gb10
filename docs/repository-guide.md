# Repository guide and change reasoning

This repository produces a reproducible vLLM image for one platform: NVIDIA
DGX Spark with GB10. Its automation is a pipeline, so a change is not complete
when one script or workflow step works. Review the input, every transformation,
the generated repository state, and each downstream consumer.

## Agent instruction source

`AGENTS.md` is the single source of shared repository instructions.
`CLAUDE.md` imports it for Claude Code compatibility and must not repeat its
content. Put instructions that apply to every coding agent in `AGENTS.md`.
Add tool-specific text only when another agent cannot follow the shared rule,
and keep that exception in the importing compatibility file.

## Repository map

| Layer | Source of truth | Purpose |
|---|---|---|
| User and release behavior | `README.md` | Supported platform, image use, tags, and release expectations |
| Pinned inputs | `versions.env` | Complete declarative build input set |
| Input policy | `scripts/versions_env.py` | Schema, value validation, and build-revision roles |
| Upstream detection | `scripts/check-updates.sh` | Finds candidate release changes without publishing them |
| Monitor boundary | `scripts/validate-monitor-update.py` and `.github/workflows/monitor-upstream-releases.yaml` | Restricts generated PRs to approved declarative changes |
| Trusted generation | `.github/workflows/run-bump.yaml` and `scripts/bump.sh` | Resolves reviewed refs and regenerates locks on DGX Spark |
| Image definition | `Dockerfile`, `scripts/build-args.sh`, `locks/`, and `checksums/` | Converts validated inputs into reproducible build layers |
| Build and verification | `.github/workflows/build-image.yaml` and `.github/workflows/verify-reproducible.yaml` | Builds trusted `main`, runs smoke checks, and verifies output |
| Release output | `.github/workflows/create-release.yaml`, `scripts/render-metadata.sh`, and `scripts/generate-release-notes.sh` | Publishes tags, metadata, and release notes |
| Contracts | `tests/test-*.py` | Derives cross-layer completeness and security requirements |
| Trust model | `docs/contributor-ci-security.md` | Separates untrusted PR data from persistent runners and credentials |

## Reason from invariants

Start by writing the invariant that failed. Examples include:

- one `versions.env` input produces one validated and reproducible image;
- a monitored value change reaches build accounting, generation, build
  arguments, metadata, and release output;
- untrusted pull request code never runs on a persistent runner;
- reviewed declarative data is applied only to the exact approved commit;
- generated output changes only its documented allowlist;
- a green detection job is not accepted until the generated repository state
  passes hosted tests.

Then trace the value or control decision in both directions. Find where it
originates, how it is validated and transformed, which files it generates,
which later jobs consume it, and what evidence proves the final behavior.
Search by semantic role and variable name, not only by the failing message.

## Before opening an issue

1. Reproduce against the latest applicable image, commit, or workflow run.
2. Search open and closed issues and pull requests for the error, component,
   variable, workflow, and affected release.
3. Decide whether the failure belongs to this image, repository automation,
   DGX Spark environment, or an upstream project.
4. Capture expected behavior, actual behavior, minimal reproduction, exact
   version or commit, sanitized logs, and workflow URL.
5. Link related work and explain what is different about the new report.
6. Use a private security advisory instead of a public issue for a suspected
   vulnerability.

An unexplained failed command is evidence, not yet a root cause. If the cause
is not proven, state a bounded hypothesis and what evidence would confirm or
reject it.

## Before opening a pull request

1. Read the issue history, related pull requests, relevant documentation,
   implementation, callers, consumers, tests, and recent commits.
2. Reproduce the original failure with a focused test or documented manual
   procedure before changing behavior.
3. Identify every affected lifecycle stage using the repository map.
4. Preserve the trusted-runner boundary. Treat upstream responses, contributor
   branches, generated diffs, refs, digests, package metadata, and filenames as
   untrusted data until validated.
5. Prefer derived or table-driven contracts over copied lists and fixed counts.
   A new schema key or monitored key should fail closed until all required
   consumers are present.
6. Test normal, boundary, malformed, duplicate, missing, stale, and adversarial
   inputs that are plausible at each trust boundary.
7. For automation, test the state before the transition and the repository
   state produced after it. Continue through the next workflow handoff.
8. Inspect the complete diff and changed-file list. Confirm that unrelated
   files, secrets, temporary output, and ignored files are absent.
9. Run every applicable local check and record exact results. Identify DGX
   Spark, credentialed, hardware, or post-merge checks that remain.
10. Update documentation when an invariant, lifecycle, operator action, or
    security assumption changed.

## Validation baseline

The GitHub-hosted test workflow discovers every `tests/test-*.py` file. Run the
same suites locally, then run the repository's shell and workflow checks:

```bash
for test_file in tests/test-*.py; do python3 "${test_file}"; done
actionlint .github/workflows/*.yaml
bash -n scripts/*.sh tests/*.sh
shellcheck -S warning scripts/*.sh tests/*.sh
python3 -m py_compile scripts/*.py tests/*.py
git diff --check
```

Dockerfile and build-script changes also require the DGX Spark smoke test.
Never weaken or bypass a check because it cannot run locally. Record the
remaining trusted or hardware-dependent validation and follow the documented
workflow.

## Review standard

A review is complete only when it can answer:

- What invariant failed, and what evidence proves the root cause?
- Why is the change in this repository rather than upstream?
- Which callers, consumers, generated outputs, and later workflows were
  inspected?
- Which test fails on the original behavior and passes after the change?
- What malformed, stale, duplicate, or adversarial cases were considered?
- Does any untrusted value reach code execution, credentials, persistent
  storage, a privileged runner, a git ref, or a published artifact?
- What happens immediately after merge, and what proves that stage is
  compatible?

If any answer is unknown, document the gap instead of presenting the change as
fully verified.
