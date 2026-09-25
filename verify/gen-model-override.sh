#!/usr/bin/env bash
#
# Generate a compose override file for a single model from verify/models.json.
#
# The base verify/docker-compose.yaml hardcodes the Nemotron Lightning flags in
# its command (that was the single-model harness). This script emits a
# per-model override that:
#   - writes that model's --revision and --served-model-name pins
#   - replaces the GPU/context tuning numbers
#   - replaces the production-specific serve flags with the model's own flags
#     (a dense model must NOT receive Lightning's moe/humming/mamba flags)
#
# The override is emitted to stdout. run-verify.sh captures it into a generated
# git-ignored file (verify/generated/<model>-compose.override.yaml) and passes
# it to `docker compose -f <base> -f <override> up`.
#
# jq is required (the driver already requires it for the harness).

set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CATALOG="${CATALOG:-${DIR}/models.json}"
PORT="${VLLM_PORT:-8010}"

emit_override() {
  local name="$1"
  local model rev gpu maxlen batched maxseqs quant kv_dtype spec_config chat_kwargs chat_template
  model="$(jq -r --arg n "$name" '.models[] | select(.name == $n) | .model' "$CATALOG")"
  rev="$(jq -r --arg n "$name" '.models[] | select(.name == $n) | .revision // ""' "$CATALOG")"
  gpu="$(jq -r --arg n "$name" '.models[] | select(.name == $n) | .serve.gpu_memory_utilization // "0.92"' "$CATALOG")"
  maxlen="$(jq -r --arg n "$name" '.models[] | select(.name == $n) | .serve.max_model_len // "131072"' "$CATALOG")"
  batched="$(jq -r --arg n "$name" '.models[] | select(.name == $n) | .serve.max_num_batched_tokens // "4096"' "$CATALOG")"
  maxseqs="$(jq -r --arg n "$name" '.models[] | select(.name == $n) | .serve.max_num_seqs // "4"' "$CATALOG")"
  # Quantization / KV-cache dtype are separate serve flags when present.
  quant="$(jq -r --arg n "$name" '.models[] | select(.name == $n) | .serve.quantization // ""' "$CATALOG")"
  kv_dtype="$(jq -r --arg n "$name" '.models[] | select(.name == $n) | .serve.kv_cache_dtype // ""' "$CATALOG")"
  # Speculative decoding config (e.g. {"method":"mtp","num_speculative_tokens":5})
  # is emitted as JSON for --speculative-config.
  spec_config="$(jq -c --arg n "$name" '.models[] | select(.name == $n) | .serve.speculative_config // empty' "$CATALOG")"
  # Default chat template kwargs (e.g. {"enable_thinking": false}) are emitted
  # as JSON for --default-chat-template-kwargs. Configures server-wide template
  # behavior (e.g. disabling Qwen3's default-on thinking for a dense baseline).
  chat_kwargs="$(jq -c --arg n "$name" '.models[] | select(.name == $n) | .serve.default_chat_template_kwargs // empty' "$CATALOG")"
  # Vendored chat template filename (e.g. tool_chat_template_gemma4.jinja),
  # mounted read-only into the container by the base compose. Used for models
  # whose tokenizer ships no chat_template (Gemma 4), which transformers
  # v4.44+ refuses to infer.
  chat_template="$(jq -r --arg n "$name" '.models[] | select(.name == $n) | .serve.chat_template // ""' "$CATALOG")"


  # Catches a malformed catalog (e.g. a name not in models.json).
  if [[ -z "${model}" || "${model}" == "null" ]]; then
    echo "ERROR: model '${name}' not found in ${CATALOG}" >&2
    return 1
  fi

  # Compose override YAML. The command is rebuilt as a full list so the
  # model-specific flags are emitted verbatim from the catalog. Values here are
  # simple strings (model ids, revision hashes, flags) with no YAML metacharacters.
  {
    printf 'services:\n  vllm-serve:\n    command:\n'
    printf '      - vllm\n      - serve\n'
    printf '      - "%s"\n' "${model}"
    [[ -n "${rev}" ]] && {
      printf '      - --revision\n      - "%s"\n' "${rev}"
    }
    printf '      - --served-model-name\n      - "%s"\n' "${model}"
    printf '      - --host\n      - 0.0.0.0\n'
    printf '      - --port\n      - "%s"\n' "${PORT}"
    printf '      - --gpu-memory-utilization\n      - "%s"\n' "${gpu}"
    printf '      - --max-model-len\n      - "%s"\n' "${maxlen}"
    printf '      - --max-num-batched-tokens\n      - "%s"\n' "${batched}"
    printf '      - --max-num-seqs\n      - "%s"\n' "${maxseqs}"
    [[ -n "${quant}" ]] && {
      printf '      - --quantization\n      - "%s"\n' "${quant}"
    }
    [[ -n "${kv_dtype}" ]] && {
      printf '      - --kv-cache-dtype\n      - "%s"\n' "${kv_dtype}"
    }
    [[ -n "${spec_config}" ]] && {
      # The speculative config is JSON (contains double quotes), so emit it as
      # a single-quoted YAML scalar: JSON never contains a single quote, so the
      # literal content survives unescaped.
      printf '      - --speculative-config\n      - '\''%s'\''\n' "${spec_config}"
    }
    [[ -n "${chat_kwargs}" ]] && {
      # Same treatment for the JSON default chat-template kwargs: single-quoted
      # YAML scalar so the embedded double quotes survive verbatim.
      printf '      - --default-chat-template-kwargs\n      - '\''%s'\''\n' "${chat_kwargs}"
    }
    [[ -n "${chat_template}" ]] && {
      # The vendored template lives at /etc/vllm/chat-templates/<file> in the
      # container (mounted read-only by the base compose from CHAT_TEMPLATE_DIR).
      printf '      - --chat-template\n      - "/etc/vllm/chat-templates/%s"\n' "${chat_template}"
    }
    jq -r --arg n "$name" '.models[] | select(.name == $n) | .serve.flags[]? | "      - \"\(.)\""' "$CATALOG"
  }
}

if [[ $# -ne 1 ]]; then
  echo "usage: $0 <model-name>" >&2
  exit 1
fi
emit_override "$1"
