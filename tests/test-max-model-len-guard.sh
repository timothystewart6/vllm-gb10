#!/usr/bin/env bash
# tests/test-max-model-len-guard.sh
#
# Unit tests for scripts/vllm-serve-safe.sh.
# No GPU, no vLLM, no model weights required — stubs `vllm` on PATH
# and uses a local config.json fixture.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WRAPPER="$REPO_ROOT/scripts/vllm-serve-safe.sh"

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

# Fixture: model directory with max_position_embeddings=40960 (Qwen3-32B).
mkdir -p "$work/model"
cat > "$work/model/config.json" <<'JSON'
{"max_position_embeddings": 40960, "model_type": "qwen3"}
JSON

# Stub `vllm` so exec calls are observable without a real binary.
mkdir -p "$work/bin"
cat > "$work/bin/vllm" <<'SH'
#!/usr/bin/env bash
printf 'STUB_VLLM:'; printf ' %s' "$@"; printf '\n'
SH
chmod +x "$work/bin/vllm"
export PATH="$work/bin:$PATH"

pass() { printf 'ok  %s\n' "$1"; }
fail() { printf 'FAIL %s: %s\n' "$1" "$2" >&2; exit 1; }

# 1. Under limit passes through unchanged.
out="$("$WRAPPER" "$work/model" --max-model-len 32768 2>/dev/null)"
[[ "$out" == *"32768"* ]] || fail "under-limit" "expected 32768 in: $out"
pass "under-limit passes through"

# 2. Exactly at limit passes through.
out="$("$WRAPPER" "$work/model" --max-model-len 40960 2>/dev/null)"
[[ "$out" == *"40960"* ]] || fail "at-limit" "expected 40960 in: $out"
pass "at-limit passes through"

# 3. Over limit with no autoclamp → exit 65 + REFUSING message.
"$WRAPPER" "$work/model" --max-model-len 65536 >/dev/null 2>"$work/err" && \
  fail "over-limit-refuse" "expected non-zero exit" || true
grep -q "REFUSING" "$work/err" || fail "over-limit-refuse" "no REFUSING message in stderr"
pass "over-limit refuses with REFUSING message"

# 4. VLLM_GB10_AUTOCLAMP=1 rewrites to model max.
out="$(VLLM_GB10_AUTOCLAMP=1 "$WRAPPER" "$work/model" --max-model-len 65536 2>/dev/null)"
[[ "$out" == *"40960"* ]]  || fail "autoclamp" "expected clamped value 40960: $out"
[[ "$out" != *"65536"* ]]  || fail "autoclamp" "original oversized value leaked: $out"
pass "VLLM_GB10_AUTOCLAMP=1 clamps to model max"

# 5. --max-model-len=VALUE (equals form) is handled.
out="$("$WRAPPER" "$work/model" --max-model-len=32768 2>/dev/null)"
[[ "$out" == *"32768"* ]] || fail "equals-form" "expected 32768 in: $out"
pass "--max-model-len=VALUE form parsed correctly"

# 6. No --max-model-len → pure pass-through, no flag injected.
out="$("$WRAPPER" "$work/model" --port 8000 2>/dev/null)"
[[ "$out" != *"--max-model-len"* ]] || fail "no-flag" "wrapper injected unwanted flag: $out"
pass "no --max-model-len passes through without modification"

printf '\nall 6 tests passed\n'
