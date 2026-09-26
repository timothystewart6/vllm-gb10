#!/usr/bin/env bash
#
# vllm-gb10 CI verify driver (multi-model matrix).
#
# Serves the freshly-built vllm-gb10 image across a catalog of models, waits
# for readiness, runs a deterministic functional test plus optional per-model
# suites (tool calling, reasoning, multimodal, spec-decode), then runs a
# llama-benchy workload matrix against each served model from inside the
# container (via uvx). Models come from verify/models.json, each carrying its
# own serve flags/tuning and a `tests` map that selects which optional suites
# to run. Each model is booted, checked, and benched in turn, torn down between
# models, so this proves the image handles several architectures, quant
# formats, parsers, multimodal paths, and runtime kernels on one GB10. Exits
# non-zero if any model's stage fails.
#
# Intended to run on the self-hosted GB10 runner as the gha-runner user. It
# uses only what that user already has: docker, docker compose, curl, jq,
# python3, and a vllm-gb10 image that can reach PyPI. It does NOT need sudo or
# a host llama-benchy install - benchy runs inside the container so no host
# provisioning is required and the benchmark targets the exact image under
# test. The functional test suite runs on the host (python3 stdlib only), then
# llama-benchy runs in the container.
#
# Usage (from the repo root, on the runner):
#   IMAGE=ghcr.io/timothystewart6/vllm-gb10:<tag> bash verify/run-verify.sh
#
# Models come from verify/models.json. To run just one model:
#   MODELS=nemotron-lightning bash verify/run-verify.sh
#
# Env overrides (all optional):
#   MODELS              comma-separated catalog names to run (default: all)
#   IMAGE               image to run (CI sets the canonical tag)
#   VLLM_PORT=8010      port the server binds
#   HF_CACHE            host HF hub cache (default NFS model share)
#   VLLM_CACHE          host vLLM compile cache
#   VERIFY_RESULT_DIR   host dir bind-mounted at /results in the container
#   BENCH_PP="128 2048 8192 32768"   llama-benchy prompt sizes
#   BENCH_TG="32 128"                llama-benchy generation sizes
#   BENCH_RUNS=3                      measured runs per shape (plus 1 warmup)
#   BENCHY_VERSION=0.4.0             pinned llama-benchy release (uvx @<version>)
#   BENCH_CONC_PP=2048  BENCH_CONC_TG=128  concurrency workload shape
#   BENCH_CONC="1 4"                  concurrency levels
#   SKIP_BENCH=1       skip the llama-benchy stage
#   KEEP_SERVER=1      leave the LAST container running after the checks
#   HEALTH_TIMEOUT_MIN=20   max minutes to wait for /health per model
#   CATALOG             path to the model catalog (default verify/models.json)
#

set -euo pipefail

# --- config ----------------------------------------------------------------
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_FILE="${DIR}/docker-compose.yaml"
CATALOG="${CATALOG:-${DIR}/models.json}"
GEN_OVERRIDE="${DIR}/gen-model-override.sh"
GENERATED_DIR="${DIR}/generated"
RESULT_DIR="${VERIFY_RESULT_DIR:-${DIR}/results}"
STAMP="$(date +%Y%m%d-%H%M%S)"

IMAGE="${IMAGE:-ghcr.io/timothystewart6/vllm-gb10:v0.30.0-gb10.2}"
VLLM_PORT="${VLLM_PORT:-8010}"
MODELS="${MODELS:-}"
# Legacy single-model override (kept for local convenience): if MODEL is set,
# it names a catalog entry to run instead of all models.
LEGACY_MODEL="${MODEL:-}"
HF_CACHE="${HF_CACHE:-/mnt/llm/models/huggingface}"
VLLM_CACHE="${VLLM_CACHE:-/mnt/llm/vllm-compile-cache}"
BENCH_PP="${BENCH_PP:-128 2048 8192 32768}"
BENCH_TG="${BENCH_TG:-32 128}"
BENCH_RUNS="${BENCH_RUNS:-3}"
BENCH_CONC_PP="${BENCH_CONC_PP:-2048}"
BENCH_CONC_TG="${BENCH_CONC_TG:-128}"
BENCH_CONC="${BENCH_CONC:-1 4}"
KEEP_SERVER="${KEEP_SERVER:-0}"
SKIP_BENCH="${SKIP_BENCH:-0}"
# Cold first boot on GB10 does weight load + torch.compile + Mamba2 warmup +
# FlashInfer autotune + CUDA graph capture, which can take 10+ minutes, and a
# 30B A3B NVFP4 model takes a while to load weights too. Warm boots (compile
# cache populated) are much faster. Override to tune.
HEALTH_TIMEOUT_MIN="${HEALTH_TIMEOUT_MIN:-20}"

# The deterministic token the functional suite requires after normalization.
DETERMINISTIC_TOKEN="${DETERMINISTIC_TOKEN:-GB10_TEST_OK}"

# llama-benchy is invoked inside the serving container via uvx, so its version
# must be a reviewed, pinned build input rather than resolving "latest" at
# runtime (an upstream release could otherwise execute unreviewed code inside
# the host-networked container with NFS mounts). The container runs
# unprivileged by default (VLLM_PRIVILEGED=false), so that code has no
# privileged device access. The pin below mirrors what the harness was
# validated against; bump it deliberately, review the release notes, and
# re-validate on the runner.
BENCHY_VERSION="${BENCHY_VERSION:-0.4.0}"

# Publish the config to the environment so `docker compose` can interpolate
# these values (compose reads the child process environment, not unexported
# shell variables). The `--env-file /dev/null` flag in the up command keeps a
# stray local .env from overriding them. The model tunables now live in the
# per-model override generated from the catalog, so the single-model env
# versions are explicitly cleared to avoid leaking into interpolation.
export IMAGE VLLM_PORT HF_CACHE VLLM_CACHE VERIFY_RESULT_DIR VLLM_HOST_IP CHAT_TEMPLATE_DIR
unset MODEL MODEL_REVISION 2>/dev/null || true
unset GPU_MEMORY_UTILIZATION MAX_MODEL_LEN MAX_NUM_BATCHED_TOKENS MAX_NUM_SEQS 2>/dev/null || true

# The compose service that the `up` and `exec` commands reference. This must be
# the compose *service* name (vllm-serve), NOT the container_name
# (vllm-gb10-verify): `docker compose exec` looks services up by the service
# name and reports "is not running" when given the container_name instead.
SERVICE="vllm-serve"
CONTAINER="vllm-gb10-verify"

BASE_URL="http://127.0.0.1:${VLLM_PORT}/v1"
HEALTH_URL="http://127.0.0.1:${VLLM_PORT}/health"

# Pick the models to run. Explicit MODELS wins; else a legacy MODEL catalog
# name; else every entry in the catalog.
if [[ -n "${MODELS}" ]]; then
  MODEL_LIST="${MODELS//,/ }"
elif [[ -n "${LEGACY_MODEL}" ]]; then
  MODEL_LIST="${LEGACY_MODEL}"
else
  MODEL_LIST="$(jq -r '.models[].name' "${CATALOG}")"
fi

if [[ -z "${MODEL_LIST}" ]]; then
  echo "ERROR: no models to run (catalog is empty or unreadable: ${CATALOG})" >&2
  exit 1
fi

PASS=0
FAIL=0

say()  { printf '\n[%s] %s\n' "$(date +%H:%M:%S)" "$*"; }
pass() { printf '  PASS: %s\n' "$*"; PASS=$((PASS + 1)); }
fail() { printf '  FAIL: %s\n' "$*" >&2; FAIL=$((FAIL + 1)); }

# -- helper: docker compose ------------------------------------------------
# docker_compose() wraps `docker compose` so the binary/plugin difference is
# handled in one place and shellcheck stays quiet.
docker_compose() {
  docker compose "$@"
}

# --- preflight -------------------------------------------------------------
require() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "ERROR: required command not found: $1" >&2
    exit 1
  }
}

# Fetch a pipeline-safe value from the catalog (returns non-zero if absent).
model_val() {
  # $1 = model name, $2 = jq expression
  local v
  v="$(jq -r --arg n "$1" ".models[] | select(.name == \$n) | $2" "${CATALOG}" 2>/dev/null || true)"
  printf '%s\n' "${v}"
}

# Whether a per-model optional test suite is enabled (true/false).
model_test_enabled() {
  # $1 = model name, $2 = suite key
  [[ "$(model_val "$1" ".tests.$2 // false")" == "true" ]]
}

say "vllm-gb10 CI verify (multi-model matrix)"
echo "  image:       ${IMAGE}"
echo "  models:      ${MODEL_LIST}"
echo "  port:        ${VLLM_PORT}"
echo "  hf_cache:    ${HF_CACHE}"
echo "  result_dir:  ${RESULT_DIR}"
echo "  benchy:      uvx llama-benchy@${BENCHY_VERSION} (inside container)"
echo "  bench_pp:    ${BENCH_PP}"
echo "  bench_tg:    ${BENCH_TG}"
echo "  bench_conc:  ${BENCH_CONC} (pp=${BENCH_CONC_PP}/tg=${BENCH_CONC_TG})"
echo

require docker
require docker_compose
require curl
require jq
require python3

# Fail fast if the port is already in use.
if curl -sf --max-time 3 "${HEALTH_URL}" >/dev/null 2>&1; then
  echo "ERROR: port ${VLLM_PORT} already has a live health endpoint." >&2
  exit 1
fi

mkdir -p "${RESULT_DIR}" "${GENERATED_DIR}"
# The serving container is hardened (unprivileged, cap_drop ALL) so its root
# has no CAP_DAC_OVERRIDE and cannot bypass file permissions on the bind
# mount. The in-container benchy therefore needs real write permission at
# /results. The results dir only ever holds per-run JSONs that CI uploads,
# so world-writable is safe here and matches how CI artifact dirs behave.
# Host-side files (meta, server log, matrix, summary) are written by the
# gha-runner user that owns the dir; benchy JSONs land here too as root.
chmod 0777 "${RESULT_DIR}"

cleanup() {
  if [[ "${KEEP_SERVER}" != "1" ]]; then
    say "stopping server"
    docker compose -f "${COMPOSE_FILE}" down --remove-orphans >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

# --- 1. ensure the image is present ----------------------------------------
say "=== [1] image present? ==="
if docker image inspect "${IMAGE}" >/dev/null 2>&1; then
  pass "image ${IMAGE} present locally"
else
  echo "  pulling ${IMAGE} ..."
  docker pull "${IMAGE}"
  pass "image ${IMAGE} pulled"
fi

# --- loop over each model --------------------------------------------------
read -r -a MODEL_ARRAY <<<"${MODEL_LIST}"
MODEL_COUNT="${#MODEL_ARRAY[@]}"
MODEL_INDEX=0

for MODEL_NAME in ${MODEL_LIST}; do
  MODEL_INDEX=$((MODEL_INDEX + 1))
  IS_LAST=0
  [[ "${MODEL_INDEX}" -eq "${MODEL_COUNT}" ]] && IS_LAST=1

  MODEL_ID="$(model_val "${MODEL_NAME}" '.model')"
  MODEL_REV="$(model_val "${MODEL_NAME}" '.revision // ""')"
  # This model's served context window, used to clamp the benchmark shapes so
  # every requested pp/tg combination fits (see [7] below).
  MODEL_MAX_LEN="$(model_val "${MODEL_NAME}" '.serve.max_model_len // "131072"')"

  if [[ -z "${MODEL_ID}" || "${MODEL_ID}" == "null" ]]; then
    say "=== [skip] '${MODEL_NAME}' not found in ${CATALOG} ==="
    fail "model '${MODEL_NAME}' missing from catalog"
    continue
  fi

  say "=== model: ${MODEL_NAME} ==="
  echo "  id:    ${MODEL_ID}"
  [[ -n "${MODEL_REV}" && "${MODEL_REV}" != "null" ]] && echo "  rev:   ${MODEL_REV}"

  # llama-benchy builds its corpus with a tokenizer. With a reasoning/tool
  # parser or the plain model name it falls back to a `gpt2` tokenizer fetched
  # from HuggingFace, which the offline serving container cannot load (the
  # `tokenizers` lib ignores HF_HUB_OFFLINE). Point `--tokenizer` at the served
  # model's snapshot dir (a local path the container already has via the HF
  # cache mount at /root/.cache/huggingface), so `LightweightTokenizer` loads
  # tokenizer.json directly from disk with no network access.
  # HF hub dir name: models--<org>--<model>, e.g. Qwen/Qwen3-0.6B -> models--Qwen--Qwen3-0.6B
  HUB_DIR="models--${MODEL_ID//\//--}"
  BENCH_TOKENIZER="/root/.cache/huggingface/hub/${HUB_DIR}/snapshots/${MODEL_REV}"
  echo "  benchy tokenizer: ${BENCH_TOKENIZER}"

  # Generate this model's compose override (its own serve flags + pins) and
  # write it into the generated dir so `up` sees the same file every time.
  OVERRIDE_FILE="${GENERATED_DIR}/${MODEL_NAME}-compose.override.yaml"
  if ! bash "${GEN_OVERRIDE}" "${MODEL_NAME}" > "${OVERRIDE_FILE}"; then
    fail "could not generate override for '${MODEL_NAME}'"
    continue
  fi

  # --- 2. start the server (this model) -----------------------------------
  say "--- [2] start server ${MODEL_NAME} ==="
  START_TS="$(date +%s)"
  if ! docker_compose -f "${COMPOSE_FILE}" -f "${OVERRIDE_FILE}" --env-file /dev/null up -d --no-build; then
    fail "compose up -d failed for ${MODEL_NAME}"
    echo "--- last 60 container log lines ---" >&2
    docker logs --tail 60 "${CONTAINER}" 2>&1 >&2 || true
    continue
  fi
  pass "compose up -d (${IMAGE})"

  # --- 3. wait for health --------------------------------------------------
  say "--- [3] wait for /health ${MODEL_NAME} (timeout ${HEALTH_TIMEOUT_MIN} min) ==="
  HEALTHY=0
  for _ in $(seq $((HEALTH_TIMEOUT_MIN * 6))); do
    if curl -sf --max-time 5 "${HEALTH_URL}" >/dev/null 2>&1; then
      HEALTHY=1
      break
    fi
    # Surface recent logs on failure to help debugging.
    if ! docker inspect -f '{{.State.Running}}' "${CONTAINER}" 2>/dev/null | grep -q true; then
      echo "  container exited early; recent logs:" >&2
      docker logs --tail 40 "${CONTAINER}" 2>&1 >&2 || true
      break
    fi
    sleep 10
  done

  if [[ "${HEALTHY}" != "1" ]]; then
    fail "/health not ready for ${MODEL_NAME} within timeout"
    echo "--- last 60 log lines ---" >&2
    docker logs --tail 60 "${CONTAINER}" 2>&1 >&2 || true
    continue
  fi
  pass "/health ready on ${VLLM_PORT} for ${MODEL_NAME}"

  # --- [4] model registry -----------------------------------------------------
  # /health answers as soon as the API layer is up, which can be before the
  # engine has finished loading weights and registered the model. Gate the
  # "ready" state on the served model appearing in /v1/models instead, since
  # that is the real readiness signal the rest of the harness depends on.
  say "--- [4] /v1/models registers ${MODEL_NAME} ==="
  REGISTERED=0
  MODELS_JSON=""
  for _ in $(seq $((HEALTH_TIMEOUT_MIN * 6))); do
    MODELS_JSON="$(curl -sf --max-time 10 "${BASE_URL}/models" 2>/dev/null || true)"
    if echo "${MODELS_JSON}" | jq -e --arg m "${MODEL_ID}" '.data[] | select(.id == $m)' >/dev/null 2>&1; then
      REGISTERED=1
      break
    fi
    if ! docker inspect -f '{{.State.Running}}' "${CONTAINER}" 2>/dev/null | grep -q true; then
      echo "  container exited while waiting for model registration; recent logs:" >&2
      docker logs --tail 40 "${CONTAINER}" 2>&1 >&2 || true
      break
    fi
    sleep 10
  done

  if [[ "${REGISTERED}" != "1" ]]; then
    fail "model '${MODEL_ID}' not found in /v1/models"
    echo "  last models response: ${MODELS_JSON}" >&2
    echo "--- last 60 log lines ---" >&2
    docker logs --tail 60 "${CONTAINER}" 2>&1 >&2 || true
    continue
  fi
  pass "model '${MODEL_ID}' registered"

  # Startup time is measured to the real readiness signal: the model appearing
  # in /v1/models (step [4]). /health answers before the engine finishes
  # loading weights, so recording startup after it would under-report the time
  # users actually wait for the model to be ready.
  STARTUP_S="$(($(date +%s) - START_TS))"

  # Record startup + server info for the summary.
  IMAGE_ID="$(docker inspect -f '{{.Image}}' "${CONTAINER}" 2>/dev/null || echo "${IMAGE}")"
  STARTUP_MEM_MIB=0
  if docker inspect -f '{{.State.Running}}' "${CONTAINER}" 2>/dev/null | grep -q true; then
    # One current-memory sample taken after registration and before the
    # functional/bench workloads. This is NOT a peak over the model run (docker
    # stats --no-stream is a single instantaneous reading), so it is reported
    # as startup_mem_mib, not peak_mem_mib. Host RSS of the container's main
    # process, in MiB. docker stats formats usage as "1.234GiB / 31.32GiB"
    # (usage / limit): take only the first operand and convert either unit to
    # MiB so multi-gigabyte servers report a real number instead of 0.
    STARTUP_MEM_MIB="$(docker stats --no-stream --format '{{.MemUsage}}' "${CONTAINER}" 2>/dev/null \
      | awk '{v=$1; if (v ~ /GiB$/) {gsub(/GiB/,"",v); printf "%.0f", v*1024} else if (v ~ /MiB$/) {gsub(/MiB/,"",v); printf "%d", v}}')"
    [[ -z "${STARTUP_MEM_MIB}" || "${STARTUP_MEM_MIB}" == "0" ]] && STARTUP_MEM_MIB="n/a"
  fi

  # --- 5. functional test suite ----------------------------------------------
  # Deterministic text + streaming always run. Optional suites are gated by the
  # catalog's per-model `tests` map. Runs on the host with python3 stdlib only.
  say "--- [5] functional tests ${MODEL_NAME} ==="
  VLLM_TESTS=""
  for suite in tool reasoning multimodal; do
    if model_test_enabled "${MODEL_NAME}" "$suite"; then
      VLLM_TESTS="${VLLM_TESTS:+${VLLM_TESTS},}${suite}"
    fi
  done
  if model_test_enabled "${MODEL_NAME}" spec_decode; then
    # Spec-decode validation is its own generation probe (see below), not a
    # model-tests suite. Nothing to add to VLLM_TESTS here.
    :
  fi

  if ! VLLM_BASE_URL="${BASE_URL}" \
        VLLM_MODEL="${MODEL_ID}" \
        VLLM_MODEL_NAME="${MODEL_NAME}" \
        VLLM_RESULT_DIR="${RESULT_DIR}" \
        VLLM_STAMP="${STAMP}" \
        VLLM_RED_SQUARE="${DIR}/fixtures/red-square.png" \
        VLLM_TESTS="${VLLM_TESTS}" \
        VLLM_TEMP="0.0" \
        VLLM_MAX_TOKENS="4096" \
        VLLM_DETERMINISTIC_TOKEN="${DETERMINISTIC_TOKEN}" \
        python3 "${DIR}/model-tests.py"; then
    fail "functional tests failed for ${MODEL_NAME}"
    # A failed suite must still free port 8010 and the GPU for the next model,
    # so tear the server down here before continuing the loop. Do not run the
    # (expensive) bench or record a passing meta for this model.
    docker_compose -f "${COMPOSE_FILE}" -f "${OVERRIDE_FILE}" --env-file /dev/null down --remove-orphans >/dev/null 2>&1 || true
    continue
  fi
  pass "functional tests passed for ${MODEL_NAME}"

  # --- [6] spec-decode generation probe (Lightning) --------------------------
  SPECD_RESULT="skip"
  if model_test_enabled "${MODEL_NAME}" spec_decode; then
    say "--- [6] spec-decode generation probe ${MODEL_NAME} ==="
    SPECD_OUT="${RESULT_DIR}/specdecode-${MODEL_NAME}-${STAMP}.json"
    # N = num_speculative_tokens (only meaningful when > 1).
    N="$(model_val "${MODEL_NAME}" '.serve.speculative_config.num_speculative_tokens // 0')"
    METHOD="$(model_val "${MODEL_NAME}" '.serve.speculative_config.method // ""')"
    http_code="$(curl -s -o "${SPECD_OUT}" -w '%{http_code}' --max-time 300 \
      -X POST "${BASE_URL}/chat/completions" \
      -H 'Content-Type: application/json' \
      -d "{\"model\":\"${MODEL_ID}\",\"messages\":[{\"role\":\"user\",\"content\":\"Say the word 'guitar' and stop.\"}],\"max_tokens\":64}")"
    if [[ "${http_code}" != "200" ]]; then
      SPECD_RESULT="fail"
      fail "spec-decode generation probe returned HTTP ${http_code}"
      cat "${SPECD_OUT}" >&2 || true
    else
      # A reasoning model can spend the whole token budget in the reasoning
      # channel, leaving `content` null even though it produced tokens. Check
      # content first, then reasoning, so the probe proves draft-token
      # generation produced output rather than failing on a reasoning model's
      # empty content field.
      SPECD_CONTENT="$(jq -r '.choices[0].message.content // ""' "${SPECD_OUT}")"
      if [[ -z "${SPECD_CONTENT}" ]]; then
        SPECD_CONTENT="$(jq -r '.choices[0].message.reasoning // ""' "${SPECD_OUT}")"
      fi
      if [[ -z "${SPECD_CONTENT}" ]]; then
        SPECD_RESULT="fail"
        fail "spec-decode generation probe returned no content (${METHOD}, N=${N})"
      else
        SPECD_RESULT="pass"
        pass "spec-decode generation probe OK (method=${METHOD}, N=${N}): output_len=${#SPECD_CONTENT}"
      fi
    fi
  fi

  # --- [7] llama-benchy (inside the container via uvx) -------------------------
  BENCH_PPTG_RESULT="skip"
  BENCH_CONC_RESULT="skip"
  if [[ "${SKIP_BENCH}" == "1" ]]; then
    say "--- [7] llama-benchy SKIPPED (SKIP_BENCH=1) ==="
  else
    say "--- [7] llama-benchy ${MODEL_NAME} (in-container) ==="
    BENCH_OUT="bench-${MODEL_NAME}-${STAMP}.json"

    # Clamp the prompt sizes so every pp/tg combination fits this model's
    # served context window: prompt + the longest generation + a small safety
    # margin must be <= max_model_len. A shape that exceeds the window is
    # rejected by the server with HTTP 400 (the doc's "Benchmark shape" note).
    # These smaller models used to lose their 32768 row that way. With the
    # clamp every declared shape actually runs, so the matrix row reflects a
    # real measurement instead of a silently-dropped request.
    BENCH_TG_MAX=0
    for tg in ${BENCH_TG}; do
      (( tg > BENCH_TG_MAX )) && BENCH_TG_MAX=$((tg))
    done
    BENCH_PP_CLAMPED=""
    for pp in ${BENCH_PP}; do
      if (( pp + BENCH_TG_MAX + 64 <= MODEL_MAX_LEN )); then
        BENCH_PP_CLAMPED="${BENCH_PP_CLAMPED:+${BENCH_PP_CLAMPED} }${pp}"
      fi
    done
    if [[ -z "${BENCH_PP_CLAMPED}" ]]; then
      BENCH_PPTG_RESULT="fail"
      fail "no benchmark prompt size fits ${MODEL_NAME} max_model_len=${MODEL_MAX_LEN} (max tg=${BENCH_TG_MAX})"
    else
      say "  clamped bench pp: ${BENCH_PP_CLAMPED} (max_model_len=${MODEL_MAX_LEN})"

      # Default llama-benchy runs a warmup phase first (unless --no-warmup), then
      # --runs measured iterations per shape. We want 1 warmup + several measured
      # runs, which is the default behavior, so we omit --no-warmup.
      # --no-cache adds random noise to requests + sends cache-prompt=false to
      # disable prompt caching during the benchmark.
      # The `uvx llama-benchy@<version>` pin (not bare `llama-benchy`) makes the
      # benchmark tool a reviewed, locked build input instead of resolving
      # "latest" at runtime inside the serving container (unprivileged by
      # default). Bump BENCHY_VERSION deliberately and re-validate on the
      # runner.
      # shellcheck disable=SC2086  # BENCH_PP_CLAMPED is intentionally word-split.
      if docker_compose -f "${COMPOSE_FILE}" -f "${OVERRIDE_FILE}" exec -T "${SERVICE}" \
          uvx "llama-benchy@${BENCHY_VERSION}" \
          --base-url "${BASE_URL}" \
          --model "${MODEL_ID}" \
          --tokenizer "${BENCH_TOKENIZER}" \
          --pp ${BENCH_PP_CLAMPED} \
          --tg ${BENCH_TG} \
          --depth 0 \
          --runs "${BENCH_RUNS}" \
          --exact-tg \
          --latency-mode generation \
          --concurrency 1 \
          --no-cache \
          --save-result "/results/${BENCH_OUT}" \
          --format json; then
        if [[ -f "${RESULT_DIR}/${BENCH_OUT}" ]]; then
          BENCH_PPTG_RESULT="pass"
          pass "llama-benchy (pp/tg matrix) completed for ${MODEL_NAME} (${RESULT_DIR}/${BENCH_OUT})"
        else
          BENCH_PPTG_RESULT="fail"
          fail "llama-benchy for ${MODEL_NAME} reported success but no result file at ${RESULT_DIR}/${BENCH_OUT}"
        fi
      else
        BENCH_PPTG_RESULT="fail"
        fail "llama-benchy failed for ${MODEL_NAME}"
        echo "--- container state ---" >&2
        docker inspect -f 'State={{.State.Status}} ExitCode={{.State.ExitCode}} OOMKilled={{.State.OOMKilled}} Error={{.State.Error}}' "${CONTAINER}" 2>&1 || \
          echo "  container ${CONTAINER} is gone (it exited before/during benchy)" >&2
        echo "--- last 60 container log lines ---" >&2
        docker logs --tail 60 "${CONTAINER}" 2>&1 >&2 || true
      fi

      # Concurrency workload: run a separate llama-benchy at the fixed
      # pp/tg shape across the requested concurrency levels. llama-benchy runs
      # all pp/tg combos at every concurrency level, so this must be a separate
      # invocation scoped to one shape. The same version pin applies.
      if [[ -n "${BENCH_CONC}" ]]; then
        say "--- [7b] llama-benchy concurrency ${MODEL_NAME} ==="
        CONC_OUT="bench-conc-${MODEL_NAME}-${STAMP}.json"
        # shellcheck disable=SC2086  # BENCH_CONC is intentionally word-split.
        if docker_compose -f "${COMPOSE_FILE}" -f "${OVERRIDE_FILE}" exec -T "${SERVICE}" \
            uvx "llama-benchy@${BENCHY_VERSION}" \
            --base-url "${BASE_URL}" \
            --model "${MODEL_ID}" \
            --tokenizer "${BENCH_TOKENIZER}" \
            --pp "${BENCH_CONC_PP}" \
            --tg "${BENCH_CONC_TG}" \
            --depth 0 \
            --runs "${BENCH_RUNS}" \
            --latency-mode generation \
            --concurrency ${BENCH_CONC} \
            --no-cache \
            --save-result "/results/${CONC_OUT}" \
            --format json; then
          if [[ -f "${RESULT_DIR}/${CONC_OUT}" ]]; then
            BENCH_CONC_RESULT="pass"
            pass "llama-benchy concurrency completed for ${MODEL_NAME} (${RESULT_DIR}/${CONC_OUT})"
          else
            BENCH_CONC_RESULT="fail"
            fail "llama-benchy concurrency for ${MODEL_NAME}: no result file at ${RESULT_DIR}/${CONC_OUT}"
          fi
        else
          BENCH_CONC_RESULT="fail"
          fail "llama-benchy concurrency failed for ${MODEL_NAME}"
          echo "--- last 40 container log lines ---" >&2
          docker logs --tail 40 "${CONTAINER}" 2>&1 >&2 || true
        fi
      fi
    fi
  fi

  # --- post-health / summary JSON ------------------------------------------
  # Best-effort health re-check after bench and a per-model metrics/result
  # record that the summarizer combines into the cross-model summary.
  POST_HEALTH_RESULT="pass"
  if curl -sf --max-time 5 "${HEALTH_URL}" >/dev/null 2>&1; then
    pass "/health still good after bench/${MODEL_NAME}"
  else
    POST_HEALTH_RESULT="fail"
    fail "/health NOT healthy after bench for ${MODEL_NAME}"
  fi

  # Persist the full server log so it rides along in the uploaded
  # verify/results artifact (the plan records server logs per model, not just a
  # tail on failure). Treat failure as a real harness failure like the other
  # stages: it contributes to PASS/FAIL and the final exit status.
  SERVER_LOG="${RESULT_DIR}/server-${MODEL_NAME}-${STAMP}.log"
  SERVER_LOG_RESULT="fail"
  if docker logs "${CONTAINER}" > "${SERVER_LOG}" 2>&1; then
    SERVER_LOG_RESULT="pass"
    pass "saved server log (${SERVER_LOG})"
  else
    fail "could not save server log for ${MODEL_NAME} to ${SERVER_LOG}"
  fi

  MODEL_META="${RESULT_DIR}/meta-${MODEL_NAME}-${STAMP}.json"
  {
    printf '{ "model": %s, "name": %s, "image": %s, "startup_s": %s,\n' \
      "$(jq -n --arg v "${MODEL_ID}" '$v')" \
      "$(jq -n --arg v "${MODEL_NAME}" '$v')" \
      "$(jq -n --arg v "${IMAGE_ID}" '$v')" \
      "${STARTUP_S}"
    printf '  "startup_mem_mib": %s, "revision": %s, "stamp": %s }\n' \
      "$(jq -n --arg v "${STARTUP_MEM_MIB}" '$v')" \
      "$(jq -n --arg v "${MODEL_REV}" '$v')" \
      "$(jq -n --arg v "${STAMP}" '$v')"
  } > "${MODEL_META}"
  pass "recorded model meta (${MODEL_META})"

  # --- teardown between models ----------------------------------------------
  # Bring the server down before booting the next model so ports and the GPU
  # are free. Between models this always happens (each boots on the same
  # isolated port). On the LAST model, KEEP_SERVER=1 leaves it up for manual
  # inspection; the cleanup trap honors the same flag if it is not set.
  if [[ "${KEEP_SERVER}" == "1" && "${IS_LAST}" == "1" ]]; then
    say "--- leaving last server up (KEEP_SERVER=1) ---"
  else
    say "--- stopping server (between models) ---"
    docker_compose -f "${COMPOSE_FILE}" -f "${OVERRIDE_FILE}" --env-file /dev/null down --remove-orphans >/dev/null 2>&1 || true
  fi

  # --- lifecycle: verify a clean stop (process exited) -----------------------
  EXITED=0
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    if ! docker inspect -f '{{.State.Running}}' "${CONTAINER}" 2>/dev/null | grep -q true; then
      EXITED=1
      break
    fi
    sleep 2
  done
  CLEAN_STOP_RESULT="skip"
  if [[ "${KEEP_SERVER}" == "1" && "${IS_LAST}" == "1" ]]; then
    pass "kept last server running (KEEP_SERVER=1)"
  elif [[ "${EXITED}" == "1" ]]; then
    CLEAN_STOP_RESULT="pass"
    pass "server process exited cleanly after stop (${MODEL_NAME})"
  else
    CLEAN_STOP_RESULT="fail"
    fail "server did not exit after stop for ${MODEL_NAME} (container still running)"
  fi

  # --- consolidated per-model matrix record ----------------------------------
  # One machine-readable file per model with every outcome the full report's
  # test matrix lists. The release-notes renderer reads this to rebuild the
  # same matrix (spec-decode, lifecycle, runtime-path and bench rows included),
  # so the release notes stay in lockstep with the harness.
  MATRIX_OUT="${RESULT_DIR}/matrix-${MODEL_NAME}-${STAMP}.json"
  {
    printf '{ "model": %s, "name": %s, "stamp": %s,\n' \
      "$(jq -n --arg v "${MODEL_ID}" '$v')" \
      "$(jq -n --arg v "${MODEL_NAME}" '$v')" \
      "$(jq -n --arg v "${STAMP}" '$v')"
    printf '  "startup": %s, "registry": %s,\n' \
      "$(jq -n --arg v "${HEALTHY:-0}" '$v')" \
      "$(jq -n --arg v "${REGISTERED:-0}" '$v')"
    printf '  "functional": %s, "spec_decode": %s,\n' \
      "$(jq -n --arg v "pass" '$v')" \
      "$(jq -n --arg v "${SPECD_RESULT:-skip}" '$v')"
    printf '  "bench_pptg": %s, "bench_conc": %s,\n' \
      "$(jq -n --arg v "${BENCH_PPTG_RESULT:-skip}" '$v')" \
      "$(jq -n --arg v "${BENCH_CONC_RESULT:-skip}" '$v')"
    printf '  "post_health": %s, "server_log": %s,\n' \
      "$(jq -n --arg v "${POST_HEALTH_RESULT:-skip}" '$v')" \
      "$(jq -n --arg v "${SERVER_LOG_RESULT:-skip}" '$v')"
    printf '  "clean_stop": %s }\n' \
      "$(jq -n --arg v "${CLEAN_STOP_RESULT:-skip}" '$v')"
  } > "${MATRIX_OUT}"
  pass "recorded matrix (${MATRIX_OUT})"
done

# --- summary ---------------------------------------------------------------
if command -v python3 >/dev/null 2>&1; then
  if python3 "${DIR}/summarize-bench.py" "${RESULT_DIR}" > "${RESULT_DIR}/summary-${STAMP}.json" 2>/dev/null; then
    pass "combined bench summary written (${RESULT_DIR}/summary-${STAMP}.json)"
  else
    echo "  (no combined summary: no bench results to aggregate)"
  fi
  # The human-readable markdown report the summarizer documents. Both the JSON
  # and .md ride the uploaded verify/results artifact.
  if python3 "${DIR}/summarize-bench.py" --markdown "${RESULT_DIR}" > "${RESULT_DIR}/summary-${STAMP}.md" 2>/dev/null; then
    pass "markdown summary written (${RESULT_DIR}/summary-${STAMP}.md)"
  else
    echo "  (no markdown summary: no bench results to aggregate)"
  fi
fi

say "=== summary: ${PASS} passed, ${FAIL} failed ==="
if [[ "${FAIL}" -gt 0 ]]; then
  exit 1
fi
echo "VERIFY_PASS"
