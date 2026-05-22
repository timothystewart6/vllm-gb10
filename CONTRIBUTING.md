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

1. Edit the relevant `_REF` line in `versions.env` (e.g. `VLLM_REF=v0.21.0`) on a branch.
2. Open a PR. The `run-bump.yaml` workflow detects the change to `versions.env`, runs
   `scripts/bump.sh` on the DGX Spark runner, and commits the resolved `_COMMIT` SHAs,
   updated `GB10_BUILD`, and regenerated lockfiles back to your branch automatically.
3. Review the diff that CI committed, then merge.

You do not need to SSH into the Spark or run anything locally.

**Do not edit lockfiles by hand.** They are generated outputs.

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
- Include the output of the smoke test (`scripts/smoke-test.sh`) if you changed
  anything in the Dockerfile or build scripts.
- Keep commits focused - one logical change per PR.

## Security

Do not open public issues for security vulnerabilities. See
[SECURITY.md](SECURITY.md).
