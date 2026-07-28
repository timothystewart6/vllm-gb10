## What does this PR do?

<!-- One or two sentences. -->

## Reasoning and evidence

<!-- State the failed invariant, reproduction, root cause or bounded
     hypothesis, and related issues, PRs, or Actions runs. -->

## Lifecycle impact

<!-- List the inputs, detection, validation, generation, build, verification,
     metadata, and release stages inspected. Write "None" only after checking
     the repository guide. -->

## Security impact

<!-- Describe trust-boundary, token, runner, untrusted-input, and generated-file
     effects. State why the existing boundary remains safe. -->

## Validation

<!-- List exact commands and results. Include the original failing case,
     regression coverage, generated repository state, and anything that needs
     DGX Spark or post-merge validation. -->

## Checklist

- [ ] Read the repository guide and relevant contributor and security documentation
- [ ] Searched open and closed issues and PRs and linked related work
- [ ] Inspected callers, consumers, generated outputs, and downstream workflow stages
- [ ] Added a regression that fails for the original reason and passes with this fix
- [ ] Reviewed the complete diff and changed-file list
- [ ] This change is specific to the GB10 build or CI - not a patch to upstream vLLM, NCCL, FlashInfer, or PyTorch behavior
- [ ] `versions.env` changes follow the [input integration checklist](../CONTRIBUTING.md#adding-or-changing-a-versionsenv-input)
- [ ] A maintainer reviewed the exact SHA before manually dispatching trusted `run-bump.yaml`
- [ ] Dockerfile or script changes tested on DGX Spark
- [ ] Smoke test output included below if Dockerfile or build scripts changed

## Smoke test output

<!-- Paste output of tests/smoke-test.sh if Dockerfile or build scripts changed.
     CI runs this automatically, but including it here speeds up review. -->

## Upstream issue (if applicable)

<!-- If this works around an upstream bug, link the upstream issue or PR here
     so we can track when a proper fix lands and this workaround can be removed. -->
