# DeepSeek V4 Flash 0731 on two GB10 nodes

This example serves [DeepSeek V4 Flash 0731](https://huggingface.co/deepseek-ai/DeepSeek-V4-Flash-0731) using the **standard vllm-gb10 image** across two NVIDIA GB10 systems (ASUS GX10 or DGX Spark), with Ray tensor parallelism 2. No model fork or additional image build is needed.

The configuration below was qualified on **two ASUS GX10 systems** and then deployed for daily use. It is pinned to a tested image digest and checkpoint revision. See [benchmarks.md](benchmarks.md) for measured throughput, long-context latency, repeatability and hardware/software provenance.

> This is a two-node recipe. It was **not** qualified on a single GB10. It uses host networking, InfiniBand/RoCE devices, and privileged containers; use only on trusted, access-controlled hosts and networks. The vLLM port has no built-in authentication in this example.

## Prerequisites

- Two GB10 systems (one NVIDIA GB10 GPU and 128 GB unified memory each), with Docker Engine, NVIDIA Container Toolkit and the Docker Compose plugin.
- A working high-speed inter-node link (tested with CX-7 QSFP, RoCE v2, MTU 9000), with routes between your chosen head and worker IPs.
- Sufficient local free disk for the **full checkpoint** on both systems, plus kernel compilation caches and temporary files. The two nodes must be able to read the exact same model revision. Model loading used approximately 79 GiB of unified memory per GPU in the tested configuration.
- Access to the Hugging Face checkpoint, and a token if required by the model.
- Ray connectivity over the inter-node link. Restrict all exposed ports to the trusted cluster/network. The vLLM API binds to 0.0.0.0:8000 on the head.

Discover the correct network devices on each node:

```bash
ip -br address
ibdev2netdev
show_gids
```

Set the interface, IB HCA and RoCE v2 GID index for **your own hardware**. The tested pair used `mlx5_0:1` and GID index `3`; those names and index values are *not portable*. Verify MTU and inter-node reachability before starting the cluster. The dual-HCA path was investigated but did not show a repeatable serving advantage over the selected single-HCA path.

## Setup and start

Use the same checked-out example directory on both systems. The files mount the launch scripts read-only into the containers.

1. Copy `.env.example` to `.env` **on both nodes**. Fill in `HEAD_IP`, `WORKER_IP`, `QSFP_IFACE`, `IB_HCA`, `IB_GID_INDEX`, and absolute `HF_CACHE_DIR` / `RUNTIME_CACHE_DIR` paths. Supply `HF_TOKEN` when required. Values such as `QSFP_IFACE` can differ between nodes.
2. Make the cache directories before startup. The full pinned model revision must be present on **both nodes**. On first start the model can download (`HF_HUB_OFFLINE=0`, `TRANSFORMERS_OFFLINE=0`); once the checkpoint is cached, optionally switch both to `1` for reproducible offline operation. Do not switch them to `1` before the complete model has been cached.
3. Start the **head first**, then the worker. The head script starts Ray and waits up to 15 minutes for both nodes and two GPUs to become available *before* launching vLLM. Start the worker promptly; a cold model load or first-time kernel compilation can take significantly longer after the worker joins.

On **head**:

```bash
mkdir -p "$(grep '^HF_CACHE_DIR=' .env | cut -d= -f2-)" "$(grep '^RUNTIME_CACHE_DIR=' .env | cut -d= -f2-)"
docker compose -f docker-compose.head.yaml config
docker compose -f docker-compose.head.yaml up -d
```

On **worker**:

```bash
mkdir -p "$(grep '^HF_CACHE_DIR=' .env | cut -d= -f2-)" "$(grep '^RUNTIME_CACHE_DIR=' .env | cut -d= -f2-)"
docker compose -f docker-compose.worker.yaml config
docker compose -f docker-compose.worker.yaml up -d
```

> The example pins the image to an immutable digest. If you intentionally use another image or model revision, the benchmark figures no longer describe that exact runtime.

Follow startup logs:

```bash
# Head
docker compose -f docker-compose.head.yaml logs -f vllm-ray-head
# Worker
docker compose -f docker-compose.worker.yaml logs -f vllm-ray-worker
```

## Verify the cluster and API

On the head, once the model has finished loading:

```bash
docker exec deepseek-v4-ray-head ray status
curl -fsS http://127.0.0.1:8000/health
curl -fsS http://127.0.0.1:8000/v1/models
```

Ray must report two live nodes and two GPUs. The model list should report `deepseek-ai/DeepSeek-V4-Flash-0731` and a configured maximum length of 524288.

Try a request on the head:

```bash
curl -fsS http://127.0.0.1:8000/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"deepseek-ai/DeepSeek-V4-Flash-0731","messages":[{"role":"user","content":"Write a short hello-world program in Python."}],"temperature":0,"max_tokens":256}'
```

For streaming, send the same request with `"stream":true` and use `curl -N`. For tool calling, use the OpenAI-compatible `tools` request field with a named function and `tool_choice:"auto"`. This runtime enables the model-specific `deepseek_v4` reasoning and tool parsers.

### Check the *actual* kernel/backend paths

A flag alone doesn't establish which implementation executed. Check both container logs:

- `B12X_MXFP4_BF16` and `B12xExperts`: A16 B12X MoE
- `Selected DeepGemmFp8BlockScaledMMKernel`: compatible FP8 linear path
- DSpark speculative decoder with 3 draft tokens, greedy
- FlashInfer SM120 sparse MLA
- FP8 KV cache and configured `21474836480` bytes per GPU
- `CUDAGraphMode.FULL_AND_PIECEWISE` and breakable CUDA graphs

The head starts with `--default-chat-template-kwargs '{"thinking":false}'`, matching the **direct vLLM serving configuration**. Client-specific reasoning defaults (such as a separate Copilot proxy's `high` reasoning effort) are outside this standalone recipe.

## The qualified profile

| Setting | Value |
|---|---|
| Image | `ghcr.io/timothystewart6/vllm-gb10@sha256:39fc41b2e0a91d0c75fe03cbf00a715524bd8fab23114d37710a26e1de034333` |
| Model revision | `7872f01b1d1fe23eabc4c98b48bffcef5a386062` |
| Executor | Ray, TP2, PP1 |
| Linear / MoE | `auto` / `b12x`, forced A16 (`B12X_MXFP4_BF16`) |
| Speculation | DSpark k3 greedy |
| KV | FP8, 20 GiB per GPU, 256-token blocks |
| Context / scheduler | 524288 tokens, max 8 sequences, 8192 batched tokens |
| CUDA graphs | `FULL_AND_PIECEWISE`, breakable, capture size 32 |
| Sparse indexer | top-k `auto`, 128 MiB workspace |
| Scheduling / caching | async scheduling, prefix caching |
| GPU memory utilization | 0.90 |

See [benchmarks.md](benchmarks.md) and [benchmark-results.json](benchmark-results.json) for reproduction and measured expectations.

## Troubleshooting

- **Only one GPU / Ray placement stalls:** Check that the head and worker IPs are reachable over the selected QSFP interfaces, `ray status` shows both nodes, and all required Ray ports are permitted between them. Start the worker while the head is waiting. Confirm both nodes pulled the exact image digest.
- **NCCL hangs / slow inter-node traffic:** Check active HCA and RoCE v2 GID index with `ibdev2netdev` / `show_gids`. Do not assume `mlx5_0:1` and index `3` match your machines.
- **Model cannot load / out of memory:** Confirm model files on both hosts, disk and memory headroom, and 20 GiB/GPU KV reservation. The configured 524K context may require substantially more cold-prefill time at long inputs.
- **B12X `wo_a` / DeepGEMM layout assertion:** Use `--linear-backend auto` with `--moe-backend b12x`, **not** `--linear-backend b12x`. The full B12X linear issue is tracked in [#163](https://github.com/timothystewart6/vllm-gb10/issues/163).
- **Long startup:** Initial checkpoint download and compilation can be lengthy. The Compose healthcheck allows up to one hour before considering the API unhealthy; inspect logs for progress.

To shut down, run `docker compose -f docker-compose.head.yaml down` on the head, then `docker compose -f docker-compose.worker.yaml down` on the worker. Model and runtime cache directories are preserved.

## Scope and limitations

This recipe supports the tested **two-node tensor-parallel** configuration. DeepEP/expert-parallel support is not established and is not required by TP2. Genuine mixed-depth concurrency was not scored. Full B12X dense linear is blocked by [#163](https://github.com/timothystewart6/vllm-gb10/issues/163). Explicit FlashInfer sparse top-k did not outperform the selected `auto` policy in the final measurements. Sparse MLA autotuning used default tactic fallback because no usable tuning cache entries were present. These are documented limitations, not prerequisites to running the recipe.
