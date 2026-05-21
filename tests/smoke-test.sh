#!/usr/bin/env bash
# tests/smoke-test.sh
#
# Post-build sanity check for the vllm-gb10 image on DGX Spark (GB10 / sm_121a).
# Runs inside the container - do not execute on the host directly.
#
# Usage from the workflow (image already loaded):
#   IMAGE="ghcr.io/timothystewart6/vllm-gb10:${VLLM_REF}-gb10.${GB10_BUILD}"
#   docker run --rm --gpus all \
#     -v "${GITHUB_WORKSPACE}/tests/smoke-test.sh:/tmp/smoke-test.sh:ro" \
#     "${IMAGE}" bash /tmp/smoke-test.sh
#
# Standalone (with the image tagged locally as vllm-node):
#   docker run --rm --gpus all \
#     -v "$(pwd)/tests/smoke-test.sh:/tmp/smoke-test.sh:ro" \
#     vllm-node bash /tmp/smoke-test.sh

set -euo pipefail

PASS=0
FAIL=0

pass() { echo "  PASS: $*"; PASS=$((PASS+1)); }
fail() { echo "  FAIL: $*" >&2; FAIL=$((FAIL+1)); }

echo "=== vllm-gb10 smoke test ==="
echo

# -----------------------------------------------------------------
# 1. Python imports
# -----------------------------------------------------------------
echo "[1/5] Python imports"
python3 - <<'PYEOF'
import sys, importlib

for pkg in ("torch", "vllm", "flashinfer"):
    try:
        mod = importlib.import_module(pkg)
        print(f"  {pkg}: {getattr(mod, '__version__', '(no __version__)')}")
    except ImportError as e:
        print(f"  MISSING: {pkg} - {e}", file=sys.stderr)
        sys.exit(1)
PYEOF
pass "torch / vllm / flashinfer all importable"

# -----------------------------------------------------------------
# 2. CUDA availability
# -----------------------------------------------------------------
echo
echo "[2/5] CUDA availability"
python3 - <<'PYEOF'
import sys, torch

if not torch.cuda.is_available():
    print("  CUDA not available", file=sys.stderr)
    sys.exit(1)

n = torch.cuda.device_count()
for i in range(n):
    name = torch.cuda.get_device_name(i)
    cap  = torch.cuda.get_device_capability(i)
    print(f"  gpu{i}: {name}  capability={cap[0]}.{cap[1]}")
    if cap[0] != 12:
        print(f"  WARNING: expected compute capability 12.x (GB10/sm_121a), got {cap[0]}.{cap[1]}")
PYEOF
pass "CUDA visible"

# -----------------------------------------------------------------
# 3. NCCL symlink points at the Spark-aware library
# -----------------------------------------------------------------
echo
echo "[3/5] NCCL symlink"
python3 - <<'PYEOF'
import os, sys

LINK = "/usr/local/lib/python3.12/dist-packages/nvidia/nccl/lib/libnccl.so.2"
if not os.path.lexists(LINK):
    print(f"  symlink missing: {LINK}", file=sys.stderr)
    sys.exit(1)

target = os.readlink(LINK)
real   = os.path.realpath(LINK)
print(f"  {LINK}")
print(f"    -> {target}")
print(f"  realpath: {real}")

if not os.path.exists(real):
    print(f"  symlink target does not exist: {real}", file=sys.stderr)
    sys.exit(1)

# Must point somewhere under /usr/lib (our Spark-aware NCCL), not the
# wheel-bundled copy under dist-packages/nvidia/nccl/lib/.
if "dist-packages/nvidia/nccl" in real:
    print(f"  WARN: symlink still resolves inside wheel bundle ({real}); "
          "custom NCCL may not be active", file=sys.stderr)
    sys.exit(1)
PYEOF
pass "NCCL symlink targets Spark-aware library"

# -----------------------------------------------------------------
# 4. Build artifacts present inside the image
# -----------------------------------------------------------------
echo
echo "[4/5] Build artifacts"
for f in \
    /workspace/build-artifacts/wheel-sha256.txt \
    /workspace/build-artifacts/nccl-sha256.txt \
    /workspace/build-metadata.yaml; do
    if [[ ! -f "$f" ]]; then
        fail "missing $f"
    else
        pass "$f present"
    fi
done

echo
echo "  -- wheel-sha256.txt --"
cat /workspace/build-artifacts/wheel-sha256.txt

echo
echo "  -- build-metadata.yaml (first 8 lines) --"
head -8 /workspace/build-metadata.yaml

# -----------------------------------------------------------------
# 5. vllm --version sanity
# -----------------------------------------------------------------
echo
echo "[5/5] vllm --version"
python3 -m vllm.scripts --help > /dev/null 2>&1 || true
python3 -c "import vllm; print('  vllm:', vllm.__version__)"
pass "vllm module loads cleanly"

# -----------------------------------------------------------------
# Summary
# -----------------------------------------------------------------
echo
echo "=== smoke test summary: ${PASS} passed, ${FAIL} failed ==="
if [[ "${FAIL}" -gt 0 ]]; then
    exit 1
fi
