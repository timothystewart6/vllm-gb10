#!/usr/bin/env bash
# scripts/hash-build-artifacts.sh
#
# Produces reproducibility-friendly hashes for build artifacts.
#
# For compiled shared libraries (libnccl.so.2 and any .so inside compiled
# wheels such as flashinfer_jit_cache and vllm), hashes the exported symbol
# table (`nm -D --defined-only`, sorted) instead of the raw binary bytes.
# This is robust against residual nvcc/ptxas nondeterminism for sm_121a
# AOT code while still catching ABI changes, missing symbols, and missing
# files.
#
# For pure wheels (flashinfer_cubin, flashinfer_python) and for non-.so
# files inside compiled wheels, raw byte hashes are used.
#
# Output is one record per artifact, suitable for `diff` comparison across
# two builds.
#
# Usage:
#   hash-build-artifacts.sh nccl   <path-to-libnccl.so>
#   hash-build-artifacts.sh wheels <wheel-dir> [wheel-dir ...]

set -euo pipefail

mode="${1:?usage: $0 nccl <so> | wheels <dir> [dir ...]}"
shift

symhash_so() {
  local so="$1"
  nm -D --defined-only "$so" 2>/dev/null \
    | awk '{print $2" "$3}' \
    | LC_ALL=C sort \
    | sha256sum \
    | awk '{print $1}'
}

case "$mode" in
  nccl)
    so="${1:?usage: $0 nccl <so>}"
    printf '%s  %s:symbols\n' "$(symhash_so "$so")" "$(basename "$so")"
    ;;

  wheels)
    shopt -s nullglob
    for dir in "$@"; do
      for whl in "$dir"/*.whl; do
        name="$(basename "$whl")"
        case "$name" in
          flashinfer_jit_cache-*|vllm-*)
            # Compiled wheel: combine symbol-table hashes of .so files and
            # byte hashes of all other files. Aggregate to one line per wheel
            # so the diff stays readable.
            tmpdir="$(mktemp -d)"
            unzip -qq "$whl" -d "$tmpdir"
            agg="$(
              (
                cd "$tmpdir"
                find . -type f -name '*.so' -print0 \
                  | LC_ALL=C sort -z \
                  | while IFS= read -r -d '' so; do
                      printf '%s  %s:symbols\n' "$(symhash_so "$so")" "${so#./}"
                    done
                # Exclude *.dist-info/RECORD: it contains sha256 of every
                # file in the wheel including .so bytes. Since .so bytes
                # drift across builds (we gate them via symbol-equivalence
                # above), RECORD drifts too. Excluding it prevents that
                # drift from leaking back into the non-.so byte aggregate.
                find . -type f ! -name '*.so' \
                       ! -path '*/[A-Za-z0-9_.+-]*.dist-info/RECORD' -print0 \
                  | LC_ALL=C sort -z \
                  | xargs -0 sha256sum
              ) | sha256sum | awk '{print $1}'
            )"
            rm -rf "$tmpdir"
            printf '%s  %s:contents\n' "$agg" "$name"
            ;;
          *)
            # Pure wheel: byte hash.
            printf '%s  %s:bytes\n' "$(sha256sum "$whl" | awk '{print $1}')" "$name"
            ;;
        esac
      done
    done
    ;;

  *)
    echo "Unknown mode: $mode" >&2
    exit 64
    ;;
esac
