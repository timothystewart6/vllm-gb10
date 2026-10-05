#!/usr/bin/env bash
# Verify that an upstream vLLM ref retains the helper paths used by Dockerfile.
#
# Usage:
#   bash scripts/validate-vllm-source-layout.sh --tag v0.31.0
#   bash scripts/validate-vllm-source-layout.sh --commit <40-character-sha>

set -euo pipefail

die() {
  printf '[validate-vllm-source-layout] ERROR: %s\n' "$*" >&2
  exit 1
}

if [[ "$#" -ne 2 ]]; then
  die 'Usage is --tag <vLLM-release-tag> or --commit <40-character-sha>.'
fi

mode="$1"
value="$2"
case "${mode}" in
  --tag)
    if ! [[ "${value}" =~ ^v[0-9]+(\.[0-9]+)+(a[0-9]+|b[0-9]+|rc[0-9]+|\.post[0-9]+)?(-[0-9]+)?$ ]]; then
      die "Invalid vLLM release tag ${value}."
    fi
    upstream_ref="refs/tags/${value}"
    display_ref="tag ${value}"
    ;;
  --commit)
    if ! [[ "${value}" =~ ^[0-9a-f]{40}$ ]]; then
      die "Invalid vLLM commit ${value}."
    fi
    upstream_ref="${value}"
    display_ref="commit ${value}"
    ;;
  *)
    die "Unsupported ref mode ${mode}."
    ;;
esac

command -v curl >/dev/null 2>&1 || die 'curl is required.'

readonly upstream_base="https://raw.githubusercontent.com/vllm-project/vllm/${upstream_ref}"
readonly required_helpers=(
  tools/build_rust.sh
  tools/use_existing_torch.py
)

for helper in "${required_helpers[@]}"; do
  if ! source_text=$(curl -fsSL --retry 3 "${upstream_base}/${helper}" 2>/dev/null); then
    die "vLLM source layout mismatch for ${display_ref}. Required helper ${helper} is unavailable."
  fi
  if [[ -z "${source_text}" ]]; then
    die "vLLM source layout mismatch for ${display_ref}. Required helper ${helper} is empty."
  fi
done

printf '[validate-vllm-source-layout] vLLM %s matches the Dockerfile helper contract.\n' \
  "${display_ref}"
