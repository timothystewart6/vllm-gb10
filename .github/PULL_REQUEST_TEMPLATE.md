## What does this PR do?

<!-- One or two sentences. -->

## Checklist

- [ ] This change is specific to the GB10 build or CI - not a patch to upstream vLLM, NCCL, FlashInfer, or PyTorch behavior
- [ ] `versions.env` changes only (bump) - CI will run `bump.sh` and commit resolved SHAs and lockfiles automatically
- [ ] Dockerfile or script changes tested on DGX Spark
- [ ] Smoke test output included below if Dockerfile or build scripts changed

## Smoke test output

<!-- Paste output of tests/smoke-test.sh if Dockerfile or build scripts changed.
     CI runs this automatically, but including it here speeds up review. -->

## Upstream issue (if applicable)

<!-- If this works around an upstream bug, link the upstream issue or PR here
     so we can track when a proper fix lands and this workaround can be removed. -->
