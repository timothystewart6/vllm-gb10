# Contributing

This repo builds and ships a reproducible vLLM Docker image for the NVIDIA DGX
Spark (GB10 / sm_121a). Contributions are welcome, but the scope is intentionally
narrow: keep the build working, keep the pins current, keep it reproducible.

## How builds work

All version inputs live in [`versions.env`](versions.env). Every field that can
be pinned by commit SHA is pinned - there are no floating tags in the build.

The lockfiles under [`locks/`](locks/) and build checksums under
[`checksums/`](checksums/) must be regenerated whenever `versions.env` changes.
This is done by [`scripts/bump.sh`](scripts/bump.sh), which **must run on an
aarch64 DGX Spark** (it compiles and resolves platform-specific dependencies).

### Bumping a component

1. Edit the relevant `_REF` line in `versions.env` (e.g. `VLLM_REF=v0.21.0`).
2. SSH into the DGX Spark and run `scripts/bump.sh` from the repo root.
   `bump.sh` updates `_COMMIT`, increments `GB10_BUILD` (or resets to `0` on a
   new `VLLM_REF`), and regenerates all lockfiles.
3. Open a PR with the `versions.env` and lockfile changes.

**Do not edit lockfiles by hand.** They are generated outputs.

## What is and is not in scope

| In scope | Out of scope |
|---|---|
| Fixing build failures on GB10 | Porting to other GPU architectures |
| Bumping pinned component versions | Adding new Python packages to the image |
| CI workflow improvements | Changing the base OS |
| Reproducibility fixes | Runtime configuration / serving scripts |

## Opening a PR

- Target `main`.
- Include the output of the smoke test (`scripts/smoke-test.sh`) if you changed
  anything in the Dockerfile or build scripts.
- Keep commits focused - one logical change per PR.

## Security

Do not open public issues for security vulnerabilities. See
[SECURITY.md](SECURITY.md).
