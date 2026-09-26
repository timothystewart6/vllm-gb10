# verify/ - image serve + bench harness

Serves the freshly built `vllm-gb10` image on the self-hosted GB10 runner,
holds it up long enough to prove it boots, runs a deterministic functional test
plus per-model optional suites (tool calling, reasoning, multimodal,
spec-decode) against each cataloged model, and then runs a `llama-benchy`
workload against it. This is the runtime harness behind the `verify` job in
`.github/workflows/build-image.yaml`, promoted from the git-ignored
`zIgnore/vllm-poc` PoC (which ran locally on the host with `sudo`; the CI
version needs none of that).

The point is breadth: on one DGX Spark, prove the image exercises several model
architectures, quant formats, reasoning/tool parsers, multimodal paths, and
runtime kernels, not just "a model starts and says hello."

## Files

| File | Purpose |
| --- | --- |
| `docker-compose.yaml` | Base vLLM server definition (runtime flags, GPU, mounts). Per-model serve flags live in the catalog, not here. |
| `models.json` | Model catalog. One entry per served model: id, pinned snapshot revision, that model's serve flags + tuning, and a `tests` map selecting optional suites. |
| `gen-model-override.sh` | Emits a per-model compose override (its serve flags, quantization, kv-cache-dtype, speculative-config, pins) from `models.json`. |
| `model-tests.py` | Host functional test suite (python3 stdlib only). Deterministic text + streaming always run; tool/reasoning/multimodal are gated by the catalog `tests` map. |
| `run-verify.sh` | CI-safe driver: pull, up, wait for /health, `/v1/models`, functional tests, spec-decode probe, in-container benchy + concurrency bench, recorded metrics, clean teardown, next-model-lifecycle check, aggregated summary. Loops the model catalog. |
| `summarize-bench.py` | Aggregates per-model bench + meta JSON into a combined `summary-<stamp>.json` and `.md`. |
| `fixtures/red-square.png` | 128x128 solid red PNG used by the multimodal test (regenerate with `fixtures/make_red_square.py`). |
| `.env.example` | Local-run overrides. No `.env` is committed. |

## Model catalog

Each model needs its own flags - a dense model must not receive the Lightning
model's MoE/humming/mamba flags. Add models to `verify/models.json`:

```json
{
  "name": "my-model",
  "model": "org/model-id",
  "revision": "<snapshot sha256 from the NFS cache>",
  "serve": {
    "gpu_memory_utilization": "0.92",
    "max_model_len": "131072",
    "max_num_batched_tokens": "4096",
    "max_num_seqs": "4",
    "quantization": "modelopt_fp4",
    "kv_cache_dtype": "fp8_e4m3",
    "speculative_config": {"method": "mtp", "num_speculative_tokens": 5},
    "flags": ["--reasoning-parser=nemotron_v3"]
  },
  "tests": {
    "tool": true,
    "reasoning": true,
    "multimodal": false,
    "spec_decode": true
  }
}
```

`serve.quantization` and `serve.kv_cache_dtype` are emitted as `--quantization`
and `--kv-cache-dtype`; `serve.speculative_config` is emitted as the JSON value
of `--speculative-config`; `serve.default_chat_template_kwargs` is emitted as
the JSON value of `--default-chat-template-kwargs` (used to disable Qwen3's
default-on thinking on the dense baseline). `serve.flags` are extra verbatim
flags. The `tests` map turns the per-model optional suites on or off.

See [docs/model-verification.md](../docs/model-verification.md) for the
reasoning behind each model's parser and chat-template configuration.

The model snapshot must already be on the NFS model share (`HF_CACHE`), because
the container runs offline (`HF_HUB_OFFLINE=1`). None of the cataloged models are gated, so no `HF_TOKEN` is required.

## What the harness proves

For each model, in order:

1. Image present (pull if needed).
2. Compose up, wait for `/health`.
3. `/v1/models` shows the served model registered.
4. Deterministic functional test: prompt "Reply with exactly this text and
   nothing else\n\nGB10_TEST_OK", verify the response carries the token after
   normalization. Plain models must equal `GB10_TEST_OK` exactly in `content`.
   Reasoning-enabled models get a larger token budget so they can finish
   thinking and emit `content`; `content` must contain the token, falling back
   to `reasoning` only when content is empty and the reasoning is more than a
   prompt echo. Proves load + tokenize + chat template + generation + decode +
   OpenAI endpoint working together. Also checks usage has no NaN/Inf.
5. Streaming test: "Count from 1 through 20" yields HTTP 200, >1 chunk, content,
   a clean `[DONE]`, and no server exception.
6. Optional per-model suites (from the catalog `tests` map):
   - `tool`: expose `get_temperature(city)`; assert a parsed tool call names
     `get_temperature` with `city=Minneapolis`.
   - `reasoning`: ask `What is 17 * 19?`; assert the reasoning parser succeeds,
     the schema is valid, and a final answer is present (not quality).
   - `multimodal`: send the red square, ask the color, require `red`.
   - `spec_decode`: a generation probe against the served model while
     speculative decoding is configured (Lightning).
7. `llama-benchy` (in-container) pp/tg workload: prompt sizes
   `128 2048 8192 32768` x generation sizes `32 128`, 1 warmup + several
   measured runs (default `BENCH_RUNS=3`), prompt caching disabled
   (`--no-cache`), exact output length (`--exact-tg`). Prompt sizes whose
   `pp + largest tg + 64` exceeds the served model's `max_model_len` are
   clamped out so every declared shape actually runs instead of returning
   HTTP 400.
8. `llama-benchy` concurrency workload: fixed `pp=2048/tg=128` across
   concurrency levels `1 4`. Aggregate (total) throughput is the comparison
   point.
9. Post-bench `/health` re-check.
10. Record per-model meta (startup seconds, post-registration memory, image,
    revision) for the summary.
11. Clean teardown, verify the process exits, then next model can start.

The pp/tg and concurrency numbers are informational reference points. They are
not a hard CI gate until enough runs exist to know per-model variance.

## Requirements on the runner

The `verify` job runs as the `gha-runner` user (in the `docker` group). The
harness deliberately uses only what that user already has:

- `docker` + `docker compose` plugin (v2)
- `curl`, `jq`, and `python3` on the host (the functional suite is stdlib-only)
- A vllm-gb10 image that can reach PyPI (`uvx` is bundled in the image)
- Read access to the NFS model share at `/mnt/llm/models/huggingface`
  (default `HF_CACHE`; the served model snapshots are already cached there)

No `sudo`, no host `llama-benchy` install, no host `pipx` needed. `llama-benchy`
runs inside the serving container via `uvx` pinned to the reviewed
`BENCHY_VERSION` (default `0.4.0`), so it benchmarks the exact image under
test and needs no host provisioning.

## Container security

The serving container runs unprivileged by default, with the common hardening
profile from `docker-compose.yaml`:

- `privileged: ${VLLM_PRIVILEGED:-false}` - not privileged unless explicitly
  opted in via the env toggle.
- `cap_drop: [ALL]` with `cap_add: [SYS_PTRACE]` - drops every Linux
  capability except the one vLLM's flashinfer/attention JIT needs.
- `security_opt: [no-new-privileges:true]` - a process cannot gain extra
  privileges through setuid binaries or file capabilities.
- `pids_limit: 512` - caps the process count so a runaway benchy/uvx run
  cannot fork-bomb the shared runner.

This keeps the benchy execution path (pinned `uvx llama-benchy@${BENCHY_VERSION}`)
outside a privileged, capability-rich container over the NFS cache mounts. If a
model ever needs a capability an unprivileged container cannot supply, the
maintainer must re-review the security posture before setting
`VLLM_PRIVILEGED=true`.

## Usage

Run from a repo checkout on the runner:

```sh
IMAGE="ghcr.io/timothystewart6/vllm-gb10:v0.30.0-gb10.2" \
  bash verify/run-verify.sh
```

This runs every model in `verify/models.json`, tearing down between models. To
run a subset:

```sh
IMAGE="..." MODELS=nemotron-lightning,qwen3-0.6b bash verify/run-verify.sh
```

Legacy single-model form (`MODEL=<name>` naming a catalog entry) still works.

Env overrides: `MODELS`, `VLLM_PORT`, `HF_CACHE`, `VLLM_CACHE`,
`VERIFY_RESULT_DIR`, `BENCH_RUNS`, `BENCH_PP`, `BENCH_TG`, `BENCH_CONC_PP`,
`BENCH_CONC_TG`, `BENCH_CONC`, `DETERMINISTIC_TOKEN`, `KEEP_SERVER`,
`SKIP_BENCH`, `HEALTH_TIMEOUT_MIN`, `CATALOG`. See the header comment in
`run-verify.sh`.

A successful run prints `VERIFY_PASS`. Results land in `results/` (bind-mounted
at `/results` in the container): deterministic/streaming/tool/reasoning/
multimodal JSON, `bench-*.json`, `bench-conc-*.json`, `meta-*.json`, plus a
combined `summary-<stamp>.json` and `summary-<stamp>.md`, so CI can upload them
as artifacts.

## In-container benchy note

`llama-benchy` runs via `docker compose exec <service> uvx llama-benchy`.
`uvx` installs it into a throwaway cache on first use (a few hundred ms), so
each CI run pays a small warm-up cost rather than persisting a host install.
The `--save-result` path is bound to the host `results/` dir.

## In-container benchy tokenizer note

`llama-benchy` builds its benchmark corpus with a tokenizer. By default it tries
the served model name, then falls back to a hardcoded `gpt2` tokenizer fetched
from HuggingFace. In the fully offline serving container that fetch fails (the
`tokenizers` library's `from_pretrained` ignores `HF_HUB_OFFLINE`), which made
the bench stage crash. `run-verify.sh` passes `--tokenizer` pointing at the
served model's snapshot dir inside the container
(`/root/.cache/huggingface/hub/models--<org>--<model>/snapshots/<rev>`), which
loads `tokenizer.json` directly from disk with no network access. Each model's
snapshot dir exists because the same NFS HF cache is mounted both for serving
and for the in-container bench.
