# Performance and reproducibility

These measurements are from **two ASUS GX10 / NVIDIA GB10 (SM121)** systems, with Ray TP2 over the QSFP fabric. The model was served **directly** through vLLM: no proxy, gateway or router was in the scored path.

Numbers are representative of this pinned image and model revision, not promised performance on every Spark installation. In particular, single-request speculative decode throughput showed substantial variation.

## Short-context measurements

| Concurrency | Prompt tokens | Generated tokens per request | Generation tok/s | Prefill tok/s | End-to-end TTFT |
|---|---:|---:|---:|---:|---:|
| 1 | 512 | 512 | 43.745 | 1355.751 | 505 ms |
| 1 | 2048 | 512 | 39.953 | 2148.566 | 1083 ms |
| 2 | 512 | 512 | 63.083 aggregate | — | — |
| 4 | 512 | 512 | 82.894 aggregate | — | — |
| 8 | 512 | 512 | 113.098 aggregate | 1718.773 | 2223 ms |

C1 and C8 are pooled final controls over three clean startups and five scored runs per startup; C2 and C4 were measured in the earlier qualification campaign. Throughput at concurrency > 1 is aggregate across requests. Blank cells were not combined across campaigns.

### Independent-startup repeatability

| Startup | C1 / 512 tok/s | C1 / 2048 tok/s | C8 / 512 aggregate tok/s |
|---|---:|---:|---:|
| 1 | 40.451 | 41.506 | 112.667 |
| 2 | 43.943 | 38.864 | 113.725 |
| 3 | 46.840 | 39.489 | 112.901 |

Each session used five scored runs. Pooled C1/512 mean was 43.745 tok/s but median was 39.912, with SD 9.336 and a 32.343–60.320 observed range. C8 remained more stable between startup sessions.

### Stability

The separate 30-run C8 soak measured **115.064 aggregate tok/s**, with zero observed request failures, engine restarts or OOM. A separate 15-request operational soak passed, with prefix-cache-hit tokens increasing by 1024.

## Long-context measurements

| Input tokens | Generation tok/s | TTFT |
|---|---:|---:|
| 8192 | 42.177 | 4.067 s |
| 32768 | 38.965 | 16.275 s |
| 131072 | 44.403 | 70.575 s |
| 262144 | 52.583 | 159.473 s |

The near-limit qualification completed **two concurrent requests** with 523520 input tokens and exactly 512 generated tokens each. It measured 590.904 s TTFT and 2.476 aggregate generated tok/s. This is a completion/capacity result, **not** a low-latency claim.

Observed aggregate KV capacity was **1,286,757 tokens**, distinct from the **524,288 maximum context tokens per request**. The runtime estimated approximately 2.45× KV concurrency at the configured maximum; that estimate is not a latency or simultaneous-request performance guarantee.

## Benchmark protocol

Benchmarks used **llama-benchy 0.3.8**, generation-latency mode, exact 512-token generation per request, temperature 0, pinned seeds, no prompt-cache reuse, and the direct vLLM endpoint.

To reproduce the final short-context controls (run on the head after `/health` succeeds, with `llama-benchy` installed):

```bash
llama-benchy --base-url http://127.0.0.1:8000/v1 \
  --model deepseek-ai/DeepSeek-V4-Flash-0731 \
  --pp 512 2048 --tg 512 --runs 5 \
  --latency-mode generation --concurrency 1 \
  --no-cache --exact-tg --extra-body temperature=0,seed=20261010 \
  --save-result c1.json --format json

llama-benchy --base-url http://127.0.0.1:8000/v1 \
  --model deepseek-ai/DeepSeek-V4-Flash-0731 \
  --pp 512 --tg 512 --runs 5 \
  --latency-mode generation --concurrency 8 \
  --no-cache --exact-tg --extra-body temperature=0,seed=20261010 \
  --save-result c8.json --format json
```

The original qualification's llama-benchy definitions used the short model label `DeepSeek-V4-Flash-0731`; these copy/paste commands use the full served model name configured in this standalone example. Repeat on three independently started clean instances to characterize variability. The earlier C2/C4 and long-context campaigns used their own pinned definitions, and the 30-run C8 soak used seed `20261009`; they must not be represented as a single matched session.

## Hardware and software provenance

| Component | Tested configuration |
|---|---|
| Systems | Two ASUS GX10, one GB10 each, 128 GB unified memory per node |
| Architecture | GB10 SM121, `sm_121a` |
| Interconnect | CX-7 QSFP, RoCE v2 / NCCL IB, MTU 9000 |
| Selected HCA | `mlx5_0:1`, RoCE GID index 3 (hardware-specific) |
| Parallelism | Ray TP2, PP1 |
| Image release | `v0.31.0-gb10.1` |
| Image digest | `sha256:39fc41b2e0a91d0c75fe03cbf00a715524bd8fab23114d37710a26e1de034333` |
| Model revision | `7872f01b1d1fe23eabc4c98b48bffcef5a386062` |
| vLLM source | `db9527a46873454610df6dbedf79a36d6bf1a7f6` (v0.31.0) |
| Runtime version | `0.31.1.dev0+gdb9527a46.d20261006` |
| CUDA base / PyTorch / Triton | 13.2.0 / 2.13.0 / 3.7.1 |
| NCCL / FlashInfer / B12X / Ray | 2.32.3 / 0.7.0.post1 / 1.5.0 / 2.59.0 |
| Model load memory | Approximately 79.23 GiB per GPU |
| KV reservation | 20 GiB per GPU, FP8 |
| Benchmark tool | llama-benchy 0.3.8 |

The exact serving configuration is in the example's Compose files and start scripts. Selected runtime admission observed `B12X_MXFP4_BF16` (MoE) and `DeepGemmFp8BlockScaledMMKernel` (linear), DSpark k3, FP8 KV and `FULL_AND_PIECEWISE` graphs.

## Configuration decision and caveats

- **Selected:** B12X MoE A16 with linear `auto`, DSpark k3 greedy, FP8 KV, sparse top-k `auto`, breakable piecewise graphs.
- **Not selected:** Explicit FlashInfer top-k measured 34.898 tok/s at C1/512, 46.473 at C1/2048 and 112.569 aggregate at C8/512. It regressed C1/512 and did not improve C8 throughput.
- **Unsupported in this image:** `--linear-backend b12x` for this model triggers the `wo_a` / DeepGEMM BMM layout error, tracked by [#163](https://github.com/timothystewart6/vllm-gb10/issues/163).
- **Autotuning:** The sparse MLA tuning cache was empty; default tactic selection was used.
- **Coverage:** Genuine mixed-depth concurrency was *not tested*. Neither DeepEP expert-parallel support nor 1M context was qualified.

Functional production acceptance separately passed streaming, reasoning, tool calling, multi-turn tool results and a bounded coding-agent task, but these are *not* standardized task-completion-time benchmarks.
