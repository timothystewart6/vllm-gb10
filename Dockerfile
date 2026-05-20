# syntax=docker/dockerfile:1.7

ARG CUDA_BASE_IMAGE
ARG CUDA_BASE_DIGEST

############################################################
# STAGE 1a: apt-base - CUDA + system packages + uv bootstrap
# Cache key: apt-sources.list, apt-packages.txt, python-bootstrap.txt
############################################################
# Base image is pinned by digest, not just tag - Docker tags are mutable.
FROM ${CUDA_BASE_IMAGE}@${CUDA_BASE_DIGEST} AS apt-base

ARG BUILD_JOBS=8
ARG UV_VERSION
ARG PYTORCH_INDEX_URL
ARG PYPI_INDEX_URL
ARG TORCH_CUDA_ARCH_LIST=12.1a
ARG SOURCE_DATE_EPOCH

ENV MAX_JOBS=${BUILD_JOBS} \
    CMAKE_BUILD_PARALLEL_LEVEL=${BUILD_JOBS} \
    DEBIAN_FRONTEND=noninteractive \
    PIP_BREAK_SYSTEM_PACKAGES=1 \
    UV_SYSTEM_PYTHON=1 UV_BREAK_SYSTEM_PACKAGES=1 UV_LINK_MODE=copy \
    TORCH_CUDA_ARCH_LIST=${TORCH_CUDA_ARCH_LIST} \
    SOURCE_DATE_EPOCH=${SOURCE_DATE_EPOCH} \
    TRITON_PTXAS_PATH=/usr/local/cuda/bin/ptxas

COPY locks/apt-sources.list /tmp/apt-sources.list
COPY locks/apt-packages.txt /tmp/apt-packages.txt
COPY locks/python-bootstrap.txt /tmp/python-bootstrap.txt
COPY locks/python-build.txt /tmp/python-build.txt
COPY locks/python-runtime.txt /tmp/python-runtime.txt

# Replace apt sources with pinned snapshot, install exact-versioned packages,
# then bootstrap uv from the hashed lockfile.
RUN rm -f /etc/apt/sources.list.d/*.list /etc/apt/sources.list.d/*.sources \
 && cp /tmp/apt-sources.list /etc/apt/sources.list \
 && apt-get update \
 && grep -vE '^\s*(#|$)' /tmp/apt-packages.txt \
      | xargs apt-get install -y --no-install-recommends \
 && rm -rf /var/lib/apt/lists/* \
 && python3 -m pip install --no-cache-dir --require-hashes --no-deps \
      -r /tmp/python-bootstrap.txt \
      --index-url ${PYPI_INDEX_URL}

############################################################
# STAGE 1b: torch-base - PyTorch stack on top of apt-base
# Cache key: python-build.txt, PYTORCH_INDEX_URL
# Rebuilds only when PyTorch/triton version changes.
############################################################
FROM apt-base AS torch-base

# PyTorch stack and build deps - exact versions and hashes from the lockfile.
RUN --mount=type=cache,id=uv-cache,target=/root/.cache/uv \
    uv pip install --require-hashes -r /tmp/python-build.txt \
      --index-url ${PYPI_INDEX_URL} \
      --extra-index-url ${PYTORCH_INDEX_URL} \
      --index-strategy unsafe-best-match

############################################################
# STAGE 1c: base - NCCL built from source on top of torch-base
# Cache key: NCCL_REPO, NCCL_REF, NCCL_COMMIT
# Rebuilds only when NCCL version changes.
############################################################
FROM torch-base AS base

ARG TORCH_VERSION
ARG TORCHVISION_VERSION
ARG TORCHAUDIO_VERSION
ARG TRITON_VERSION
ARG NVSHMEM_VERSION
ARG TVM_FFI_VERSION
ARG TILELANG_VERSION
ARG NUMBA_VERSION
ARG NCCL_REPO
ARG NCCL_REF
ARG NCCL_COMMIT

# NCCL - clone the exact tag, verify the commit SHA matches versions.env,
# then build with sm_121 gencode and install as .deb so it replaces the
# stock libnccl on the system.
RUN git clone --depth 1 -b ${NCCL_REF} ${NCCL_REPO} /opt/nccl \
 && cd /opt/nccl \
 && [ "$(git rev-parse HEAD)" = "${NCCL_COMMIT}" ] \
 && make -j ${BUILD_JOBS} src.build \
      NVCC_GENCODE="-gencode=arch=compute_121,code=sm_121" \
 && make install PREFIX=/usr/local \
 && ldconfig

############################################################
# STAGE 2: flashinfer-builder - build FlashInfer wheels
############################################################
FROM base AS flashinfer-builder
ARG FLASHINFER_REPO
ARG FLASHINFER_REF
ARG FLASHINFER_COMMIT
ARG FLASHINFER_CUDA_ARCH_LIST=12.1a
ENV FLASHINFER_CUDA_ARCH_LIST=${FLASHINFER_CUDA_ARCH_LIST}

RUN git clone --recursive ${FLASHINFER_REPO} /workspace/flashinfer \
 && cd /workspace/flashinfer \
 && git checkout ${FLASHINFER_REF} \
 && [ "$(git rev-parse HEAD)" = "${FLASHINFER_COMMIT}" ] \
 && git submodule update --init --recursive

WORKDIR /workspace/flashinfer
RUN --mount=type=cache,id=uv-cache,target=/root/.cache/uv \
    --mount=type=cache,id=ccache,target=/root/.ccache \
    uv build --no-build-isolation --wheel . --out-dir=/wheels -v \
 && cd flashinfer-cubin && uv build --no-build-isolation --wheel . --out-dir=/wheels -v \
 && cd ../flashinfer-jit-cache && uv build --no-build-isolation --wheel . --out-dir=/wheels -v

############################################################
# STAGE 3: vllm-builder - build vLLM wheel
############################################################
FROM base AS vllm-builder
ARG VLLM_REPO
ARG VLLM_REF
ARG VLLM_COMMIT

RUN git clone --recursive ${VLLM_REPO} /workspace/vllm \
 && cd /workspace/vllm \
 && git checkout ${VLLM_REF} \
 && [ "$(git rev-parse HEAD)" = "${VLLM_COMMIT}" ] \
 && git submodule update --init --recursive

WORKDIR /workspace/vllm
RUN --mount=type=cache,id=uv-cache,target=/root/.cache/uv \
    --mount=type=cache,id=ccache,target=/root/.ccache \
    python3 use_existing_torch.py \
 && uv build --no-build-isolation --wheel . --out-dir=/wheels -v

# NOTE: No patches, no PR reverts, no requirements/cuda.txt edits. If upstream
# vLLM does not build cleanly against the pinned stack, fix by bumping
# versions.env and lockfiles to a combination that does.

############################################################
# STAGE 4: runner - final published image
############################################################
# Intentionally FROM base (not the raw CUDA base image) so the runner
# inherits NCCL, PyTorch, and the CUDA toolchain from stage 1, keeping them
# consistent with what the builder-stage wheels were linked against.
# The image is larger than a stripped runtime-only image; that tradeoff is
# accepted.
FROM base AS runner
ARG PYTORCH_INDEX_URL
ARG PYPI_INDEX_URL

WORKDIR /workspace

# Tiktoken encodings - static blobs from OpenAI, verified against committed
# checksums so the build fails loudly if upstream ever changes the bytes.
COPY checksums/tiktoken.sha256 /tmp/tiktoken.sha256
RUN mkdir -p tiktoken_encodings \
 && wget -O tiktoken_encodings/o200k_base.tiktoken  https://openaipublic.blob.core.windows.net/encodings/o200k_base.tiktoken \
 && wget -O tiktoken_encodings/cl100k_base.tiktoken https://openaipublic.blob.core.windows.net/encodings/cl100k_base.tiktoken \
 && cd tiktoken_encodings && sha256sum -c /tmp/tiktoken.sha256
ENV TIKTOKEN_ENCODINGS_BASE=/workspace/tiktoken_encodings

# instanttensor==0.1.0 requires libboost headers to compile from sdist.
# Installed here (not in apt-base) so only the runner stage is invalidated
# when this dep changes, preserving expensive upstream build caches.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libboost-dev \
 && rm -rf /var/lib/apt/lists/*

# Install runtime deps and the wheels built in stages 2 and 3.
# External deps are resolved from the hashed lockfile; locally-built wheels
# are installed by exact file path.
RUN --mount=type=bind,from=flashinfer-builder,source=/wheels,target=/fi-wheels \
    --mount=type=bind,from=vllm-builder,source=/wheels,target=/vllm-wheels \
    --mount=type=cache,id=uv-cache,target=/root/.cache/uv \
    uv pip install --require-hashes -r /tmp/python-runtime.txt \
      --index-url ${PYPI_INDEX_URL} \
      --extra-index-url ${PYTORCH_INDEX_URL} \
      --index-strategy unsafe-best-match \
 && uv pip install --no-deps /fi-wheels/*.whl /vllm-wheels/*.whl \
 && mkdir -p /workspace/build-artifacts \
 && sha256sum /fi-wheels/*.whl /vllm-wheels/*.whl \
      > /workspace/build-artifacts/wheel-sha256.txt

# Point PyTorch's bundled NCCL symlink at the Spark-aware NCCL built in base.
# Without this, torch/ray would load the wheel-bundled NCCL instead.
RUN rm -f /usr/local/lib/python3.12/dist-packages/nvidia/nccl/lib/libnccl.so.2 \
 && ln -s /usr/lib/aarch64-linux-gnu/libnccl.so.2 \
      /usr/local/lib/python3.12/dist-packages/nvidia/nccl/lib/libnccl.so.2 \
 && sha256sum /usr/lib/aarch64-linux-gnu/libnccl.so.2 \
      > /workspace/build-artifacts/nccl-sha256.txt

COPY build-metadata.yaml /workspace/build-metadata.yaml
# No ENTRYPOINT - users run: docker run ... <image> vllm serve ...
CMD ["bash"]
