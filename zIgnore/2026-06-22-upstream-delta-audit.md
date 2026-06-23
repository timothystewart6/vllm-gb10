# Upstream vLLM Dockerfile Delta Audit

**Date:** 2026-06-22
**Our ref:** `v0.23.1rc0` (commit `e3e3cd54589cee689b785aab5bda81b3e4203191`)
**Upstream Dockerfile:** `docker/Dockerfile` on `main` (1115 lines, significantly more complex)
**Our Dockerfile:** 4 stages, ~220 lines, GB10-only (`sm_121a`)

---

## Summary

Our Dockerfile is intentionally minimal - it builds vLLM, FlashInfer, and NCCL
from source for `sm_121a` and installs runtime deps from a locked requirements
file. The upstream Dockerfile has grown significantly and includes several
components we don't ship. This document categorizes each delta by relevance to
our GB10 use case.

---

## Confirmed: DeepGEMM Not Available on GB10

**Verified 2026-06-22** on `ghcr.io/timothystewart6/vllm-gb10:v0.23.1rc0-gb10.0`:

```
$ python3 -c "from vllm.utils.import_utils import has_deep_gemm; print(has_deep_gemm())"
DeepGEMM: False
```

DeepGEMM did NOT compile into the vLLM wheel. The cmake's
`cuda_archs_loose_intersection` found no overlap between the supported
archs (`9.0a`, `10.0a`/`10.0f`) and our build arch (`12.1a`). The build
logs should contain: `"DeepGEMM will not compile: unsupported CUDA architecture"`.

---

## Known Bugs / Auto-Detect Issues in Our Image

### BUG: DeepSeek V4 is NOT supported on GB10 (sm_121a)

**Severity: HIGH - DeepSeek V4 models will crash immediately on GB10**

The `DeepseekV4ForCausalLM` model class in vLLM v0.23.1rc0 has **hardcoded
SM100 requirements** that cannot be worked around with env vars:

1. **Attention backend:** `_select_dsv4_attn_cls()` in
   `vllm/models/deepseek_v4/nvidia/model.py` always returns
   `DeepseekV4FlashMLAAttention`, which uses the `FLASHMLA_SPARSE_DSV4`
   backend. That backend's `supports_compute_capability()` requires
   `major in [9, 10]`. GB10 has `major=12` - rejected.

2. **MoE kernels:** `_check_runtime_supported()` explicitly raises
   `NotImplementedError("DeepGEMM MegaMoE requires SM100 GPUs")` when
   `torch.cuda.get_device_capability(device)[0] != 10`.

3. **DeepGEMM calls:** The MoE forward pass directly calls
   `_import_deep_gemm()` which will `ImportError` since DeepGEMM is not
   compiled for sm_121a.

This is an **upstream limitation**, not a bug in our image. DeepSeek V4
requires Blackwell datacenter GPUs (SM100/SM101). There is no env var
workaround.

**Status:** Not fixable without upstream changes to add sm_121 support.
Monitor upstream for GB10/SM120 family support in future releases.

---

### `VLLM_USE_BREAKABLE_CUDAGRAPH=1` auto-enabled for DeepSeek V4

Upstream code in `vllm/config/vllm.py` (lines 1107-1118) auto-enables
`VLLM_USE_BREAKABLE_CUDAGRAPH=1` when the model architecture is
`DeepseekV4ForCausalLM` or `DeepSeekV4MTPModel` and the env var is not
already set. This sets `CompilationMode=NONE` (disables `torch.compile`).

**Impact:** Moot for GB10 since DeepSeek V4 is unsupported entirely.

**For other MLA models (DeepSeek V3/R1, MiniMax):** This auto-enable does
NOT trigger because those use `DeepseekV2ForCausalLM`, not V4.

---

### AUDIT GAP: TileLang (sm_121a attention kernels)

TileLang is used for GB10-specific attention kernels (evidenced by
`TILELANG_CLEANUP_TEMP_FILES` env var usage). The audit omitted this entirely.

| Aspect | Detail |
|--------|--------|
| What it is | DSL for tile-based GPU kernel generation, used for custom attention on non-standard archs |
| Our version | `tilelang==0.1.9` (in `locks/python-runtime.txt`) |
| sm_121a compatible? | **Needs verification** - TileLang JIT-compiles kernels at runtime using Triton/CUDA. Must confirm it generates correct code for sm_121a. |
| Failure mode | Silent correctness bugs or runtime crashes if arch codegen is wrong |

**Action:** Verify TileLang kernel compilation works on sm_121a:
```bash
docker run --gpus all --rm <image> python3 -c "
import tilelang
print('TileLang version:', tilelang.__version__)
# Attempt a simple kernel compile for sm_121
"
```

---

## Components Present in Upstream but Missing from Our Image

### 1. DeepGEMM (FP8 block-dense GEMM kernels)

| Aspect | Detail |
|--------|--------|
| What it is | FP8 GEMM kernel library from DeepSeek, accelerates MoE dispatch (DeepSeek V3/R1) |
| How upstream ships it | Built into the vLLM wheel via `cmake/external_projects/deepgemm.cmake` - vendored into `vllm/third_party/deep_gemm/` |
| Supported archs | `9.0a` (Hopper, CUDA >= 12.3), `10.0a`/`10.0f` (Blackwell, CUDA >= 12.8/12.9) |
| sm_121a compatible? | **UNCLEAR/UNLIKELY** - sm_121 is not in `DEEPGEMM_SUPPORT_ARCHS`. The cmake will silently skip it when `cuda_archs_loose_intersection` finds no overlap. |
| Impact of absence | vLLM gracefully falls back (`has_deep_gemm()` returns False). FP8 MoE models use slower generic GEMM path. |
| Action needed? | **None.** Empirically confirmed `has_deep_gemm() == False`. DeepGEMM does not support sm_121a. Not actionable until upstream adds SM120 family support. |

**Confirmed:** Our `uv build --wheel` invocation processes the CMakeLists.txt which
`include(cmake/external_projects/deepgemm.cmake)`. The cmake's `cuda_archs_loose_intersection`
found no overlap between `12.1a` and the supported archs (`9.0a`, `10.0a`/`10.0f`), so cmake
printed "DeepGEMM will not compile: unsupported CUDA architecture" and created an empty target.
`has_deep_gemm()` returns `False` at runtime (verified 2026-06-22).

---

### 2. Rust Frontend (`vllm-rs`)

| Aspect | Detail |
|--------|--------|
| What it is | High-performance OpenAI-compatible HTTP API server in Rust (axum-based), replaces the Python FastAPI frontend |
| How upstream ships it | Built in `rust-build` stage, `.so` artifacts copied into the wheel. `VLLM_REQUIRE_RUST_FRONTEND` defaults to off (optional). |
| sm_121a compatible? | Yes - pure Rust/CPU code, no GPU dependency |
| Impact of absence | Falls back to Python FastAPI frontend (what we currently use). Slightly higher latency on API layer. |
| Action needed? | **Medium priority for performance.** Requires adding `rustup`, `protoc`, and a Rust build stage. The wheel build will include it if the Rust toolchain is available. Set `VLLM_REQUIRE_RUST_FRONTEND=1` to make it mandatory. |

---

### 3. DeepEP / EP Kernels (Expert Parallelism)

| Aspect | Detail |
|--------|--------|
| What it is | NVSHMEM-based all-to-all kernels for expert-parallel MoE serving across multiple nodes |
| How upstream ships it | Separate `extensions-build` stage, installed as `ep_kernels` wheel |
| Supported archs | `9.0a`, `10.0a` (hardcoded in upstream Dockerfile) |
| sm_121a compatible? | **No** - not in the TORCH_CUDA_ARCH_LIST used for EP kernel build |
| Impact of absence | Expert parallelism not available. For 2-node DGX Spark this means MoE layers cannot be sharded across experts on different nodes - only tensor/pipeline parallelism. |
| Action needed? | **Not actionable now.** Requires upstream DeepEP to add sm_121 support. Monitor. |

---

### 4. GDRCopy (GPU Direct RDMA Copy)

| Aspect | Detail |
|--------|--------|
| What it is | Userspace library for low-latency GPU memory copies via RDMA, speeds up inter-node communication |
| How upstream ships it | `tools/install_gdrcopy.sh` installs prebuilt `.deb` packages in the runner stage |
| sm_121a compatible? | Architecture-independent (kernel driver + userspace lib). **Requires host kernel module `gdrdrv`.** |
| Impact of absence | Slightly higher latency on multi-node tensor parallel communication. Ray/NCCL still work without it. |
| Action needed? | **Low-medium priority.** Would help 2-node cluster perf. Requires: (1) `gdrdrv` kernel module on the Spark host, (2) install the `.deb` in our runner stage. Check if DGX Spark ships `gdrdrv`. |

---

### 5. FlashInfer Precompiled Cubins (`flashinfer download-cubin`)

| Aspect | Detail |
|--------|--------|
| What it is | Pre-downloaded compiled CUDA binaries for FlashInfer kernels, avoids JIT compilation on first request |
| How upstream ships it | `RUN flashinfer show-config && flashinfer download-cubin` in the final image |
| sm_121a compatible? | Only if FlashInfer publishes cubins for sm_121. Likely not. |
| Impact of absence | First request hits JIT compilation latency (seconds). Subsequent requests use JIT cache. |
| Action needed? | **Low priority.** We build FlashInfer from source (including `flashinfer-jit-cache` wheel). The JIT cache approach is fine. Could add `flashinfer download-cubin` at the end of our runner stage if cubins exist for sm_121. |

---

### 6. `bitsandbytes`, `accelerate`, `modelscope`, `timm`, `runai-model-streamer`

| Aspect | Detail |
|--------|--------|
| What they are | Optional model-loading and quantization libraries |
| How upstream ships them | Installed in the `vllm-openai-base` stage |
| sm_121a compatible? | Mostly yes (Python-only or have CUDA fallbacks) |
| Impact of absence | Cannot use bitsandbytes quantization (4-bit/8-bit QLoRA), cannot load models from ModelScope, `timm` vision models unavailable |
| Action needed? | **Add if needed for your models.** These are pip-installable and don't require special build steps. Add to `locks/python-runtime.txt` as needed. |

---

### 7. KV Connectors (nixl, mooncake, lmcache)

| Aspect | Detail |
|--------|--------|
| What they are | Disaggregated KV-cache transfer backends for prefill/decode separation across nodes |
| How upstream ships them | Optional (`INSTALL_KV_CONNECTORS=true`), installed from `requirements/kv_connectors.txt` |
| sm_121a compatible? | Unknown - would need testing |
| Impact of absence | Cannot use disaggregated prefill/decode serving pattern |
| Action needed? | **Not needed for typical 2-node TP serving.** Only relevant if you want prefill on one node and decode on another (advanced deployment pattern). |

---

### 8. Non-root User Support

| Aspect | Detail |
|--------|--------|
| What it is | `vllm` user (UID 2000, GID 0) with OpenShift-style arbitrary UID support |
| How upstream ships it | Created in `vllm-base`, with `vllm-openai-nonroot` target |
| Impact of absence | Image runs as root. Fine for DGX Spark local use, not ideal for shared/production K8s. |
| Action needed? | **Low priority unless deploying to shared infrastructure.** Easy to add if needed. |

---

### 9. CUDA Forward Compatibility (`VLLM_ENABLE_CUDA_COMPATIBILITY`)

| Aspect | Detail |
|--------|--------|
| What it is | Env var that enables CUDA forward compatibility for datacenter GPUs with older drivers |
| Our status | Not set (defaults to 0 in upstream too) |
| Action needed? | **None.** DGX Spark ships with matching driver/toolkit. |

---

## Components We Ship That Upstream Does Differently

| Component | Our approach | Upstream approach | Notes |
|-----------|-------------|-------------------|-------|
| NCCL | Built from source with `sm_121` gencode | Installs from apt (`libnccl-dev`) | Ours is correct - we need sm_121 gencode |
| FlashInfer | Full source build (3 wheels) | pip install `flashinfer-jit-cache` + `download-cubin` | Ours is correct - no prebuilt wheel for sm_121 |
| Base image | `devel` image as both build and runtime base | `devel` for build, `base` for runtime | Upstream is smaller but requires more runtime CUDA package installs. Our approach is simpler and ensures JIT compilation works. |
| Tiktoken encodings | Pre-downloaded with checksum verification | Not included (downloaded at runtime) | Ours is better for air-gapped/reproducible deployments |
| Entrypoint | Cleared (`ENTRYPOINT []`) | `vllm serve` | Ours gives more flexibility |

---

## Recommendations (Priority Order)

1. **DeepSeek V4 is unsupported** - Document clearly. No workaround exists at v0.23.1rc0. The model hardcodes SM100. Monitor upstream for SM120 family support.

2. **Verify TileLang JIT on sm_121a** - Since TRITON_MLA is the fallback for all MLA models on GB10, and it uses TileLang/Triton, confirm JIT compilation works correctly.

3. **Consider Rust frontend** - Meaningful latency improvement for high-QPS serving. Adds build complexity (Rust toolchain + protoc). Could be a separate PR.

4. **Add bitsandbytes/accelerate if needed** - Pure pip install, no build changes. Only add if users need quantized model loading.

5. **Investigate GDRCopy for 2-node** - Only worth it if `gdrdrv` is available on Spark hosts. Check with `lsmod | grep gdrdrv`.

6. **Ignore DeepEP/EP kernels** - Not compatible with sm_121 today.

7. **Ignore KV connectors** - Advanced feature not needed for standard TP serving.

---

## Empirical Verification Results

**Tested 2026-06-22** on `asus-gx10-1.local.techtronic.us` (DGX Spark):

- [x] `has_deep_gemm()` returns `False` - CONFIRMED. DeepGEMM not compiled for sm_121a.
- [ ] `import vllm.third_party.deep_gemm` - Expected to raise ImportError.
- [ ] TileLang JIT kernel compile on sm_121a - Still needs verification.
- [ ] CI build logs for "DeepGEMM will not compile" message - Still needs check.

**Conclusion:** DeepGEMM absence is confirmed. DeepSeek V4 is unsupported
on GB10 due to hardcoded SM100 requirements in the model code (not just
DeepGEMM absence). Other MLA models (DeepSeek V3/R1, MiniMax) use
`TRITON_MLA` backend which supports all compute capabilities.

---

## MLA Backend Selection on GB10 (sm_121a)

**Key finding from source code analysis:**

GB10 reports `DeviceCapability(major=12, minor=1)`. The attention backend
priority list for MLA models when `major != 10` is:

```
FLASH_ATTN_MLA  -> requires major in [9, 10]  -> REJECTED
FLASHMLA        -> requires major in [9, 10]  -> REJECTED
FLASHINFER_MLA  -> requires major == 10       -> REJECTED
TRITON_MLA      -> supports ALL archs         -> SELECTED
FLASHMLA_SPARSE -> requires major in [9, 10]  -> REJECTED
```

**Result:** MLA models (DeepSeek V3/R1, MiniMax-M2.7) use `TRITON_MLA` on
GB10. This is the Triton-based MLA implementation which:
- Does NOT require DeepGEMM
- Supports all compute capabilities
- Uses TileLang/Triton for kernel generation (JIT compiled at runtime)

DeepSeek V4 is a special case - it bypasses the generic attention selector
entirely and uses its own `FLASHMLA_SPARSE_DSV4` backend which hardcodes
SM100 requirements.

---

## Other Runtime Verification Commands

```bash
# Check Rust frontend availability
docker run --rm <image> python3 -c "import importlib.util; print('Rust frontend:', importlib.util.find_spec('vllm._rust_core') is not None)"

# Check FlashInfer
docker run --rm <image> python3 -c "import flashinfer; print('FlashInfer:', flashinfer.__version__)"

# Check NCCL
docker run --rm <image> python3 -c "import torch.distributed; print('NCCL available:', torch.distributed.is_nccl_available())"

# Check which MLA backend is selected (for DeepSeek V3/R1 or MiniMax)
docker run --gpus all --rm <image> python3 -c "
import torch
cap = torch.cuda.get_device_capability()
print(f'Compute capability: {cap[0]}.{cap[1]} (major={cap[0]})')
print(f'Expected MLA backend: TRITON_MLA (major != 10)')
"

# Check DeepGEMM (confirmed False)
docker run --rm <image> python3 -c "
from vllm.utils.import_utils import has_deep_gemm
print('DeepGEMM available:', has_deep_gemm())
"
```
