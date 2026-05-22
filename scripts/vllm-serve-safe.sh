#!/usr/bin/env bash
# scripts/vllm-serve-safe.sh
#
# Preflight wrapper around `vllm serve` that prevents the silent
# --max-model-len > max_position_embeddings foot-gun.
#
# BACKGROUND
# ----------
# vLLM warns at startup if --max-model-len exceeds the model's
# max_position_embeddings, then loads anyway. The first inference
# request triggers a CUDA device-side assert on the RoPE kernel:
#
#   Assertion `index out of bounds: 0 <= tmp16 < N` failed.
#   terminate called after throwing an instance of 'c10::AcceleratorError'
#     what():  CUDA error: device-side assert triggered
#   EngineDeadError
#
# Because the GB10 loads a 61 GB model from NFS (~13-15 min per restart),
# this creates a particularly painful crash loop: load → first request →
# crash → reload → repeat. VLLM_ALLOW_LONG_MAX_MODEL_LEN=1 suppresses the
# warning but does NOT prevent the crash. See issue #7.
#
# BEHAVIOR
# --------
#   Default:               hard error before vLLM starts (exit 65)
#   VLLM_GB10_AUTOCLAMP=1: silently clamp --max-model-len to model max
#   No --max-model-len:    no-op pass-through to `vllm serve`
#
# USAGE
# -----
#   docker run --gpus all -v ~/.cache/huggingface:/root/.cache/huggingface \
#     ghcr.io/timothystewart6/vllm-gb10:latest \
#     vllm-serve-safe Qwen/Qwen3-32B --max-model-len 65536 [other flags...]
#
#   # Auto-clamp instead of refusing:
#   docker run --gpus all -e VLLM_GB10_AUTOCLAMP=1 \
#     -v ~/.cache/huggingface:/root/.cache/huggingface \
#     ghcr.io/timothystewart6/vllm-gb10:latest \
#     vllm-serve-safe Qwen/Qwen3-32B --max-model-len 65536

set -euo pipefail

log() { printf '[vllm-serve-safe] %s\n' "$*" >&2; }
die() { log "ERROR: $*"; exit 64; }

# ---------------------------------------------------------------------------
# Parse argv: find the model id (first non-flag positional) and the value
# of --max-model-len if present.
# ---------------------------------------------------------------------------
MODEL=""
MAX_LEN=""
MAX_LEN_IDX=-1
ARGS=("$@")

i=0
while [[ $i -lt ${#ARGS[@]} ]]; do
  a="${ARGS[$i]}"
  case "$a" in
    --max-model-len)
      MAX_LEN="${ARGS[$(( i+1 ))]:-}"
      MAX_LEN_IDX=$(( i+1 ))
      i=$(( i+2 )); continue ;;
    --max-model-len=*)
      MAX_LEN="${a#*=}"
      MAX_LEN_IDX=$i
      i=$(( i+1 )); continue ;;
    -*)
      next="${ARGS[$(( i+1 ))]:-}"
      [[ -n "$next" && "$next" != -* ]] && i=$(( i+2 )) || i=$(( i+1 ))
      continue ;;
    *)
      [[ -z "$MODEL" ]] && MODEL="$a"
      i=$(( i+1 )) ;;
  esac
done

if [[ -z "$MAX_LEN" ]]; then
  log "no --max-model-len; passing through"
  exec vllm serve "$@"
fi

[[ -n "$MODEL" ]] || die "--max-model-len set but model id could not be parsed from argv"

# ---------------------------------------------------------------------------
# Resolve config.json. Two cases:
#   1. Local path (starts with / or .): read directly.
#   2. HF repo id: look in HF cache, else fetch just config.json via hub.
# ---------------------------------------------------------------------------
config_json=""

if [[ "$MODEL" == /* || "$MODEL" == .* ]]; then
  config_json="$MODEL/config.json"
  [[ -f "$config_json" ]] || die "no config.json at $config_json"
else
  hf_home="${HF_HOME:-${HOME}/.cache/huggingface}"
  cached="$(find "$hf_home/hub" -path "*models--${MODEL//\//--}*/snapshots/*/config.json" 2>/dev/null | head -n1)"
  if [[ -n "$cached" ]]; then
    config_json="$cached"
  else
    log "config.json not in HF cache; fetching via huggingface_hub"
    tmp="$(mktemp -d)"
    trap 'rm -rf "$tmp"' EXIT
    python3 - "$MODEL" "$tmp" <<'PY'
import sys
from huggingface_hub import hf_hub_download
model, dest = sys.argv[1], sys.argv[2]
print(hf_hub_download(repo_id=model, filename="config.json", local_dir=dest))
PY
    config_json="$tmp/config.json"
  fi
fi

# ---------------------------------------------------------------------------
# Extract max_position_embeddings. Handles top-level and text_config nesting
# (used by some multimodal models).
# ---------------------------------------------------------------------------
model_max="$(python3 - "$config_json" <<'PY'
import json, sys
with open(sys.argv[1]) as f:
    c = json.load(f)
for key in ("max_position_embeddings",):
    if key in c:
        print(c[key]); sys.exit(0)
tc = c.get("text_config") or {}
if "max_position_embeddings" in tc:
    print(tc["max_position_embeddings"]); sys.exit(0)
sys.exit(2)
PY
)" || die "could not find max_position_embeddings in $config_json"

log "model=$MODEL  max_position_embeddings=$model_max  requested=$MAX_LEN"

# Within bounds — pass through unchanged.
[[ "$MAX_LEN" -le "$model_max" ]] && exec vllm serve "$@"

# Over limit: clamp or refuse.
if [[ "${VLLM_GB10_AUTOCLAMP:-0}" == "1" ]]; then
  log "VLLM_GB10_AUTOCLAMP=1: clamping --max-model-len $MAX_LEN -> $model_max"
  ARGS[$MAX_LEN_IDX]="$model_max"
  exec vllm serve "${ARGS[@]}"
fi

cat >&2 <<EOF
[vllm-serve-safe] REFUSING TO START.

  Model:                       $MODEL
  max_position_embeddings:     $model_max
  --max-model-len requested:   $MAX_LEN

vLLM will warn but load anyway, then crash on the first inference request
with a CUDA device-side assert. VLLM_ALLOW_LONG_MAX_MODEL_LEN=1 hides the
warning but does not prevent the crash.

Fix one of:
  - Lower --max-model-len to <= $model_max
  - Set VLLM_GB10_AUTOCLAMP=1 to auto-clamp silently
  - Use a model variant with YaRN/NTK RoPE scaling if you need longer context
EOF
exit 65
