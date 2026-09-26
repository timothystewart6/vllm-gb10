# Model verification: reasoning and tool parser configuration

This document explains why each model in the verify harness catalog is served
with the parser and chat-template flags it has. It is the reasoning reference
for `verify/models.json` and the functional suite in `verify/model-tests.py`.

The authoritative source for all of this is the vLLM documentation:

- Reasoning outputs: <https://docs.vllm.ai/en/latest/features/reasoning_outputs/>
- Tool calling: <https://docs.vllm.ai/en/latest/features/tool_calling/>

## Where this runs in CI

The model matrix runs in two places, deliberately.

`build-image.yaml` carries the integrated gate. Its `verify` job runs the full
catalog through `verify/run-verify.sh` and `release` depends on it, so a release
only happens when all four model suites pass. A model failure in this pipeline
prevents the release and is fixed in the same change that touched the model or
image.

`verify-image-models.yaml` is a separate, dispatch-only workflow (manual trigger,
from `main` only, pinned actions). It lets a codeowner re-verify any already
published image tag or any model subset against the catalog without rebuilding
and without touching the release. Because model additions must be a reviewed
change to `verify/models.json`, the catalog is the single source of truth and
there is no ad-hoc model override path.

Both workflows invoke the same `verify/run-verify.sh`, so the shared logic lives
in one script. GitHub Actions does not share YAML anchors across files, so each
workflow keeps a thin wrapper around that driver instead of duplicating the
harness.

The serving container runs with the hardened profile from
`verify/docker-compose.yaml`: unprivileged by default, no capabilities except
`SYS_PTRACE` (`cap_drop: [ALL]` with `cap_add: [SYS_PTRACE]`),
`no-new-privileges`, and a `pids_limit` cap. This keeps the pinned in-container
benchy path from being able to escalate privileges or fork-bomb the shared
runner over the NFS mounts. Because the container has no `CAP_DAC_OVERRIDE`,
`run-verify.sh` makes the bind-mounted results dir world-writable so benchy can
write its JSON there. `VLLM_PRIVILEGED=true` opts back into a privileged
container if a model ever needs a capability the default cannot supply; that is
an explicit, reviewed exception.

## Why these four models

The catalog exists to prove that the vllm-gb10 image serves several different
model architectures and runtime paths on a single DGX Spark, while keeping CI
reasonably lightweight. Each entry is picked as a representative of a distinct
architecture or runtime path, not to maximize quality. The four models cover the
combinations we most care about: dense, MoE, Mamba, multimodal, reasoning, tool
calling, NVFP4 quantization, FP8 KV cache, and speculative decode.

The image is a fat build, so it can serve all four from the same container. The
models have pinned revisions that live on the NFS HF cache and are pulled by
revision, which keeps the serve deterministic.

### The testing matrix

| catalog name | model id | architecture / runtime path | exercises |
| --- | --- | --- | --- |
| `qwen3-0.6b` | [Qwen/Qwen3-0.6B](https://huggingface.co/Qwen/Qwen3-0.6B) | small dense, thinking disabled | deterministic, streaming |
| `gemma-4-12b` | [google/gemma-4-12B-it](https://huggingface.co/google/gemma-4-12B-it) | Gemma 4 Unified, multimodal + reasoning | deterministic, streaming, reasoning, multimodal |
| `nemotron-lightning` | [nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4](https://huggingface.co/nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4) | NVFP4 MoE + Mamba, speculative decode | deterministic, streaming, reasoning, tool calling, spec decode |
| `qwen3.8-27b-nvfp4` | [nvidia/Qwen3.8-27B-NVFP4](https://huggingface.co/nvidia/Qwen3.8-27B-NVFP4) | NVFP4 + FP8 KV cache, multimodal reasoning | deterministic, streaming, reasoning, tool calling, multimodal |

Every model runs the deterministic and streaming suites as a baseline. The
catalog then toggles the heavier suites per model based on what that model can
actually exercise.

### Coverage matrix

Each row below maps a claimed test to the harness code that produces it, and
whether it is a direct assertion or a log-backed proof. A checked cell means
the harness actually runs that test for that model; an empty cell means the
suite is intentionally not run for that model.

| Test | Implemented by | Kind | Qwen3 0.6B | Gemma 4 12B-it | Nemotron Lightning | Qwen3.8 27B NVFP4 |
| --- | --- | --- | :---: | :---: | :---: | :---: |
| Model startup (health) | `run-verify.sh` [3] health wait | direct | ✓ | ✓ | ✓ | ✓ |
| `/v1/models` registration | `run-verify.sh` [4] registry check | direct | ✓ | ✓ | ✓ | ✓ |
| Deterministic generation | `model-tests.py` `test_deterministic` | direct | ✓ | ✓ | ✓ | ✓ |
| Streaming generation | `model-tests.py` `test_streaming` | direct | ✓ | ✓ | ✓ | ✓ |
| Response JSON validity | `model-tests.py` NaN/Inf + valid-JSON checks | direct | ✓ | ✓ | ✓ | ✓ |
| Tool calling | `model-tests.py` `test_tool` | direct |  |  | ✓ | ✓ |
| Reasoning parser | `model-tests.py` `test_reasoning` | direct |  | ✓ | ✓ | ✓ |
| Multimodal image input | `model-tests.py` `test_multimodal` | direct |  | ✓ |  | ✓ |
| NVFP4 execution | server log `for NVFP4 GEMM` marker | log |  |  | ✓ | ✓ |
| FP8 KV cache | server log `kv_cache_dtype=torch.float8_e4m3fn` marker | log |  |  | ✓ | ✓ |
| MoE execution | server log humming MoE backend markers | log |  |  | ✓ |  |
| Mamba execution | server log flashinfer Mamba backend markers | log |  |  | ✓ |  |
| Speculative decoding | `run-verify.sh` [6] spec-decode probe | direct |  |  | ✓ |  |
| llama-benchy pp/tg | `run-verify.sh` [7] | direct | ✓ | ✓ | ✓ | ✓ |
| llama-benchy concurrency | `run-verify.sh` [7b] | direct | ✓ | ✓ | ✓ | ✓ |
| Post-bench health | `run-verify.sh` post-bench `/health` check | direct | ✓ | ✓ | ✓ | ✓ |
| Server log captured | `run-verify.sh` `docker logs` capture | direct | ✓ | ✓ | ✓ | ✓ |
| Clean shutdown | `run-verify.sh` teardown + exit check | direct | ✓ | ✓ | ✓ | ✓ |

The log-backed rows (NVFP4, FP8 KV, MoE, Mamba) pass only when the kernel or
backend marker for that path actually appears in the captured server log (see
"Direct log validation" for the exact strings). The renderer greps the
`server-<model>-<stamp>.log` the harness captures, so a row cannot pass from a
config flag alone: if the kernel or backend never executed, the marker is
absent and the row is marked failed.

This table mirrors the release-notes test matrix, which the renderer
(`verify/render-verify-report.py`, MATRIX_ROWS) rebuilds from the per-model
`matrix-<model>-<stamp>.json` and `suite-results-<model>-<stamp>.json` files
that the harness drops, plus the log markers from the captured `server` logs.
When you add or remove a row here, update MATRIX_ROWS the
same way so the spec and the report stay in lockstep. The "Next-model startup"
row that the harness observes (each model boots on a freshly-torn-down port,
so a boot also validates the port is freed) is not listed in the renderer and
so is not in this table either.

### Selection rationale

- `qwen3-0.6b` is the smallest dense checkpoint that is still a real, current
  model family (Qwen3). It is a small dense baseline and the fast sanity check
  for normal model loading and generation, used to shake out harness and
  serving bugs cheaply before the large models run. Thinking is disabled so the
  deterministic and streaming tests stay simple.
- `gemma-4-12b` covers Gemma 4 Unified, which adds the multimodal path, and is
  served as a reasoning model (Gemma 4 reasons only when enabled, so the catalog
  turns thinking on). It proves the image can load a newer Gemma architecture,
  exercise the reasoning parser, and answer from image input (the red-square
  test), with the multimodal check reading the color from `content` or
  `reasoning`.
- `nemotron-lightning` is the most exercise-heavy entry. It exercises NVFP4
  quantization, the MoE and Mamba paths, and FP8 KV cache, plus reasoning and
  tool parsing. Its speculative decoding runs with the MTP configuration NVIDIA
  publishes for the DGX Spark. It also carries the reasoning and tool-calling
  suites.
- `qwen3.8-27b-nvfp4` is NVIDIA's current Qwen 3.8 checkpoint. It exercises the
  NVFP4 and FP8 paths and multimodal input, paired with reasoning and tool
  parsing, so the catalog proves these features coexist in one serve rather
  than only in isolation.

Use the instruction-tuned variant for chat and reasoning models. The original
catalog entry pointed at the base checkpoint (`google/gemma-4-12B`), which
produces degenerate output and never reasons: the base model only stops on
EOS (eos list `[1]`), never emits the turn or tool-response tokens, and its
tokenizer config carries no Gemma chat template, so the vendored template's
thinking channel does not activate. It was changed to the `-it`
(instruction-tuned) checkpoint `google/gemma-4-12B-it`, whose tokenizer config
defines the full response schema including `thinking.open` and whose EOS list
is `[1, 106, 50]` (EOS, turn, tool-response). Reason and chat models should
always be served from the `-it` variant when one exists; the base checkpoint
is only appropriate for fine-tuning, not serving.

## The reasoning content contract

Reasoning models return two separate fields from the chat completions endpoint:

- `message.reasoning` - the thinking steps that led to the answer
- `message.content` - the final answer only

Note the vLLM doc warning: `reasoning` used to be called `reasoning_content`.
vLLM migrated the field name but left an empty `reasoning_content` shim, so
client code written against the old name silently reads empty output. Read
`reasoning`, not `reasoning_content`.

Two consequences drive the harness design:

1. We never string-munge model output to strip a thinking block from
   `message.content`. That is a fragile workaround for a missing parser. When
   a reasoning parser is enabled, vLLM already splits reasoning and content
   correctly. The deterministic and multimodal tests still read both fields
   for reasoning-enabled models, because a model can spend its whole budget
   thinking and leave `content` empty. The deterministic check prioritizes
   `content` and only falls back to `reasoning` when content is empty, to
   close the prompt-echo loophole; the multimodal check may use either field
   because its expected value is absent from the prompt.
2. Streaming behaves differently under a reasoning parser. The thinking text
   streams into each chunk's `delta.reasoning`, and `delta.content` only
   receives the final answer. A streaming test that only accumulates
   `delta.content` will see zero content if the model spends its whole token
   budget reasoning. This is expected and documented behavior, not a bug.

One subtlety the deterministic test accounts for: a reasoning model can put the
whole answer in `message.reasoning` with empty `content` even on a non-streaming
request, if it spends the generation thinking. A reasoning trace also tends to
echo the full prompt, so treating it as the answer would let a model pass by
repeating the prompt. The check therefore prioritizes final `content`: for
reasoning-enabled models the token budget is raised so the model can finish
thinking and emit real content, and the check requires the `GB10_TEST_OK` token
in `content` when content is present. Content falls back to `reasoning` only
when `content` is empty, and only when the reasoning carries the token beyond a
pure prompt echo. Plain models must equal the token exactly. This keeps the
round-trip proof without letting an echoed thinking trace count as an answer.
The multimodal check is different because its expected value (`red`) is absent
from the prompt, so there is no echo loophole: for reasoning-enabled models the
color may appear in `content` or the reasoning trace (a reasoning model can
describe "the square is red" with empty content), while plain models must output
exactly `red`.

## Thinking is on by default for some model families

- Qwen3 series reasons by default. To keep it as a plain dense model you must
  disable thinking server-wide with
  `--default-chat-template-kwargs '{"enable_thinking": false}'`.
- Gemma 4 reasons only when enabled (`enable_thinking: true` or client
  `reasoning_effort`). The catalog serves gemma with thinking enabled, as a
  reasoning model.

## Per-model configuration

The harness exercises four routes across the catalog. Each model is configured
as a representative of an architecture or runtime path, not to maximize quality.

### qwen3-0.6b - small dense baseline

Chosen as the smallest dense model, reasonings disabled because it is the
fast sanity baseline. Qwen3 reasons by default, so we turn it off:

```json
"default_chat_template_kwargs": { "enable_thinking": false }
```

This keeps both the deterministic and the streaming tests simple: without
thinking tokens, `message.content` is the answer and streaming emits content
deltas normally. No reasoning parser is set because this model does not test
reasoning.

### gemma-4-12b - Gemma 4 Unified + multimodal + reasoning

Gemma 4 Unified is a multimodal, reasoning model. It is served from the
instruction-tuned checkpoint `google/gemma-4-12B-it`, with the `gemma4`
reasoning parser and thinking enabled
(`--default-chat-template-kwargs '{"enable_thinking": true}'`), so the catalog
exercises reasoning and multimodal together on this entry. The reasoning suite
checks that `message.reasoning` is populated, and the multimodal suite sends
the red square and requires the model to answer `red` (from `content` or
`reasoning`, since a thinking model can answer via reasoning).

The catalog must point at the `-it` checkpoint, not the base one. The base
checkpoint does not define the Gemma turn framework in its tokenizer config and
its EOS list is only `[1]` (`<eos>`), so any chat or reasoning generation loops
forever or returns degenerate text. The `-it` variant's tokenizer config carries
the full response schema (`thinking.open`, turn and tool-response tokens) and
its EOS list is `[1, 106, 50]` (`<eos>`, `<turn|>`, `<|tool_response>`), so the
vendored template's thinking channel activates and the model answers cleanly.

Gemma 4's tokenizer ships no `chat_template` in its tokenizer config. Since
transformers 4.44, vLLM refuses to fall back to a hardcoded default and returns
HTTP 400 unless a template is supplied. The harness vendors vLLM's Gemma 4 chat
template at `verify/tool_chat_template_gemma4.jinja`, bind-mounts the verify
dir into the container at `/etc/vllm/chat-templates`, and passes
`--chat-template /etc/vllm/chat-templates/tool_chat_template_gemma4.jinja` for
this model via the catalog's `serve.chat_template` field. With thinking enabled
the template emits a proper thinking channel instead of the empty thought
wrapper it would otherwise insert, so the model answers instead of looping on
the word "thought".

### nemotron-lightning - NVFP4 / MoE / Mamba / speculative decode

The most exercise-heavy catalog entry. It combines:

- `--reasoning-parser=nemotron_v3` - Nemotron Series 3 proprietary reasoning
  format (thinking / response tags).
- `--tool-call-parser=qwen3_coder` - the coder tool-call format.
- NVFP4 quantization, FP8 KV cache, MoE and Mamba backends, and MTP
  speculative decoding (`method=mtp`, 5 speculative tokens).

Reasoning, tool calling, and spec-decode suites are all enabled for this model.

The spec-decode probe in `run-verify.sh` [6] sends a short prompt with a small
token budget and requires the response to contain output. A reasoning model can
spend that whole budget in `message.reasoning` and return `content: null` while
still generating normally (this is exactly what nemotron does on a 64-token
budget). The probe therefore accepts non-empty `content` OR non-empty
`reasoning` as proof the speculative decode path produced output. This is a
deliberately looser rule than the deterministic suite's content-first check:
the spec-decode probe only needs to prove the draft path decoded tokens, not
that a specific answer was produced, and its expected value (`content` or
`reasoning` populated) is not planted in the prompt.

### qwen3.8-27b-nvfp4 - NVFP4 multimodal reasoning

A Qwen3-family NVFP4 multimodal model. It combines:

- `--reasoning-parser=qwen3` - Qwen3 reasoning format.
- `--tool-call-parser=qwen3_coder` - the coder tool-call format.
- `--mm-encoder-tp-mode=data` with `--enable-chunked-prefill` for the
  multimodal encoder.

Reasoning, tool calling, and multimodal suites are all enabled.

## Tool calling flags

Both tool-calling models need the pair:

```text
--enable-auto-tool-choice
--tool-call-parser=qwen3_coder
```

`--enable-auto-tool-choice` is mandatory to let the model decide when to call a
tool. The parser name follows the model's tool format. vLLM also documents a
separate `qwen3_xml` parser for the specific large Qwen3-Coder releases
(`Qwen3-Coder-480B-A35B` and `30B-A3B`); the NVFP4 coder variants in this
catalog use `qwen3_coder`, which is the correct parser for their format.

## Mapping a new model

When adding a catalog entry, resolve these in order:

1. Family. Qwen3 reasons by default; Gemma 4 does not. Enable or disable
   thinking deliberately, do not leave it implicit unless that is the intent.
2. Reasoning format. Pick the `--reasoning-parser` from the vLLM reasoning
   outputs doc for the family, or omit it if the model should not reason.
3. Tool format. Pick the `--tool-call-parser` from the vLLM tool calling doc,
   and include `--enable-auto-tool-choice` whenever tools are enabled.
4. Multimodal, quantization, speculative decode and other runtime flags per the
   model card. Put JSON-valued flags (speculative config, default chat template
   kwargs) in the dedicated catalog fields rather than raw `flags` so the
   override generator can quote them correctly.
5. Chat template. If the tokenizer ships no `chat_template` (Gemma 4), vendor
   the family's template under `verify/` and set `serve.chat_template` to its
   filename so the override generator emits `--chat-template` at the
   in-container mount path. Models whose tokenizer defines a template need no
   entry.

Then provision the checkpoint and validate on hardware:

6. Checkpoint selection. For any chat or reasoning model, prefer the
   instruction-tuned (`-it` or Instruct) variant over the base checkpoint. Base
   checkpoints are trained for fine-tuning and do not define the chat turn
   framework, so a served base model returns degenerate output and can loop on
   EOS. Verify the choice by inspecting `tokenizer_config.json` for a
   `chat_template` and the full response schema, and `generation_config.json`
   for the EOS token list; a served Gemma 4 model needs at least the turn
   token in its EOS list to stop cleanly.
7. Provision to the NFS HF cache. Download the exact pinned revision with
   `snapshot_download` (or `hf download`) into the model cache at
   `/mnt/llm/models/huggingface`, then pin `revision` in the catalog to that
   commit hash. The harness resolves models offline by revision, so the pinned
   hash makes serving deterministic regardless of upstream changes.
8. Reasoning-only responses. A reasoning model can spend its whole token budget
   in `message.reasoning` and return empty `content` (nemotron does this on a
   64-token budget). The deterministic and multimodal checks give reasoning
   models a larger token budget so they can finish thinking and emit `content`,
   and only fall back to reading the `reasoning` field when `content` is empty.
   The streaming and spec-decode checks read `reasoning` directly by design; do
   not assume `content` is always populated.
9. Benchmark shape. llama-benchy requests `pp + tg` sequences. The driver
   clamps `BENCH_PP` to prompt sizes whose `pp + largest tg + 64` fits the
   served model's `max_model_len`, so every declared shape actually runs and
   the matrix row reflects a real measurement. Small-context models simply lose
   their oversized 32768 row instead of sending a request the server rejects
   with HTTP 400. If every prompt size exceeds the window, the pp/tg bench
   records a failure rather than silently skipping. Keep `BENCH_PP` at or below
   `max_model_len` minus the largest `BENCH_TG` to keep the full declared set.
10. Direct log validation. The release-notes renderer greps the captured
    `server-<model>-<stamp>.log` for the runtime markers that prove each path
    actually executed: `kv_cache_dtype`, the selected GEMM kernel (for example
    `Using HummingNvFp4LinearKernel for NVFP4 GEMM`), and MoE and Mamba backend
    selection. A configured path row fails when its marker is absent, so the
    report cannot claim a kernel or backend that never ran. The
    `reasoning_parser` and tool parser lines plus speculative decode metrics are
    documented here for manual audit. See "Direct log validation" below for the
    exact markers per model.

## Direct log validation

The functional suites prove models load and generate, but not which kernel or
backend executes for a given runtime path. The server log captures the actual
selection inline. These are the markers found in the full-pass logs (stamp
`20260925-005052`) that directly confirm each path.

### gemma-4-12b (`server-gemma-4-12b-<stamp>.log`)

- Reasoning parser: `reasoning_parser='gemma4'` in `StructuredOutputsConfig`.
- Thinking enabled: `enable_thinking: True` in `default_chat_template_kwargs`.
- Prefix caching: `enable_prefix_caching: True`.
- Attention backend: TRITON_ATTN (selected for the heterogeneous head dims of
  Gemma 4 Unified).
- Sampling: `FlashInfer for top-p & top-k sampling`.
- KV cache: `Using LBNHC KV cache layout`, `GPU KV cache size: 832,473 tokens`,
  `84.28 GiB available KV cache`.

### nemotron-lightning (`server-nemotron-lightning-<stamp>.log`)

- Architecture: `Resolved architecture: NemotronHMTPModel`.
- Quantization / KV: `quantization=modelopt_mixed`, `kv_cache_dtype=fp8_e4m3`.
- Linear + MoE backends: `moe_backend='humming'`, `linear_backend='humming'`.
- NVFP4 GEMM kernel: `Using HummingNvFp4LinearKernel for NVFP4 GEMM`.
- FP8 linear kernel: `Selected HummingFP8ScaledMMLinearKernel`.
- MoE execution: `Using indexed gemm for humming moe`,
  `Using 'HUMMING' NvFp4 MoE backend`.
- Mamba execution: `mamba_backend: flashinfer`,
  `Using FlashInfer Mamba SSU algorithm: simple`,
  `Using flashinfer Mamba SSU backend`,
  `Warming up Mamba2 SSD Triton kernels`.
- Speculative decode: `speculative_config: {method: mtp, num_speculative_tokens:
  5}`, and live runtime metrics such as `SpecDecoding metrics: Mean acceptance
  length: 4.49, Accepted throughput: 24.54 tokens/s, Drafted throughput: 35.20
  tokens/s, Avg Draft acceptance rate: 69.7%`. The metrics lines prove the MTP
  draft path actually ran during the bench, not just that the flag loaded.

### qwen3.8-27b-nvfp4 (`server-qwen3.8-27b-nvfp4-<stamp>.log`)

- Reasoning / tool parsers: `reasoning_parser='qwen3'`,
  `tool_call_parser: qwen3_coder`.
- Quantization / KV: `quantization=modelopt_mixed`, `kv_cache_dtype=fp8_e4m3`.
- NVFP4 GEMM kernel: `Using FlashInferCutlassNvFp4LinearKernel for NVFP4 GEMM`.
- FP8 linear kernel: `Selected FlashInferFP8ScaledMMLinearKernel`.
- Multimodal: `Using AttentionBackendEnum.FLASH_ATTN for MMEncoderAttention`,
  `mm_encoder_tp_mode: data`.
- FP8 KV resolution: `kv_cache_dtype=torch.float8_e4m3fn`.

These markers are a direct, log-backed statement that the configured runtime
path executed, which is the strongest verification available short of end-to-end
kernel profiling.
