#!/usr/bin/env bash
# scripts/verify-reproducible-local.sh
#
# Local equivalent of .github/workflows/verify-reproducible.yaml.
# Builds the runner image twice, extracts the authoritative artifacts, and
# fails if any authoritative artifact differs between builds.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${REPO_ROOT}"

VERIFY_DIR="${VERIFY_DIR:-/tmp/verify-local}"
PLATFORM="${PLATFORM:-linux/arm64}"
TARGET="${TARGET:-runner}"
BUILD1_TAG="${BUILD1_TAG:-vllm-gb10:verify-local-build1}"
BUILD2_TAG="${BUILD2_TAG:-vllm-gb10:verify-local-build2}"
KEEP_IMAGES="${KEEP_IMAGES:-0}"

export SOURCE_DATE_EPOCH="${SOURCE_DATE_EPOCH:-$(git log -1 --format=%ct)}"

if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "INFO: working tree is dirty; Docker context includes local changes."
  echo "INFO: build-metadata.yaml still records repo_commit=$(git rev-parse HEAD)."
  echo ""
fi

rm -rf "${VERIFY_DIR}"
mkdir -p "${VERIFY_DIR}/build1" "${VERIFY_DIR}/build2"

echo "Generating build-metadata.yaml"
bash scripts/render-metadata.sh > build-metadata.yaml

build_args=()
while IFS= read -r token; do
  build_args+=("${token}")
done < <(scripts/build-args.sh | xargs -n1)

build_image() {
  local tag="$1"
  shift

  docker buildx build \
    --target "${TARGET}" \
    --platform "${PLATFORM}" \
    --load \
    --provenance=false \
    --sbom=false \
    -t "${tag}" \
    "${build_args[@]}" \
    "$@" \
    .
}

extract_artifacts() {
  local tag="$1"
  local out_dir="$2"

  docker run --rm "${tag}" \
    cat /workspace/build-artifacts/wheel-sha256.txt \
    > "${out_dir}/wheel-sha256.txt"
  docker run --rm "${tag}" \
    cat /workspace/build-artifacts/nccl-sha256.txt \
    > "${out_dir}/nccl-sha256.txt"
  docker run --rm "${tag}" \
    cat /workspace/build-metadata.yaml \
    > "${out_dir}/build-metadata.yaml"
  docker run --rm "${tag}" \
    python3 -m pip freeze \
    > "${out_dir}/pip-freeze.txt"
  docker run --rm "${tag}" \
    bash -c "dpkg-query -W -f='\${Package}=\${Version}\n' | sort" \
    > "${out_dir}/apt-versions.txt"
  docker inspect --format='{{.Id}}' "${tag}" \
    > "${out_dir}/image-id.txt"

  echo "${tag} image ID: $(cat "${out_dir}/image-id.txt")"
}

remove_image() {
  local tag="$1"

  if [[ "${KEEP_IMAGES}" == "1" ]]; then
    return 0
  fi

  docker rmi "${tag}" >/dev/null 2>&1 || true
}

echo "Build 1 of 2 (warm cache): ${BUILD1_TAG}"
build_image "${BUILD1_TAG}"

echo "Extracting artifacts from build 1"
extract_artifacts "${BUILD1_TAG}" "${VERIFY_DIR}/build1"
remove_image "${BUILD1_TAG}"

echo "Build 2 of 2 (no-cache): ${BUILD2_TAG}"
build_image "${BUILD2_TAG}" --no-cache

echo "Extracting artifacts from build 2"
extract_artifacts "${BUILD2_TAG}" "${VERIFY_DIR}/build2"
remove_image "${BUILD2_TAG}"

echo "Comparing authoritative artifacts"
FAILED=0
for artifact in nccl-sha256.txt wheel-sha256.txt build-metadata.yaml pip-freeze.txt apt-versions.txt; do
  if diff -u \
      "${VERIFY_DIR}/build1/${artifact}" \
      "${VERIFY_DIR}/build2/${artifact}" \
      > "${VERIFY_DIR}/diff-${artifact}.txt" 2>&1
  then
    echo "PASS: ${artifact}"
  else
    echo "FAIL: ${artifact} differs between builds:"
    cat "${VERIFY_DIR}/diff-${artifact}.txt"
    FAILED=$((FAILED + 1))
  fi
done

echo ""
BUILD1_ID="$(cat "${VERIFY_DIR}/build1/image-id.txt")"
BUILD2_ID="$(cat "${VERIFY_DIR}/build2/image-id.txt")"
if [[ "${BUILD1_ID}" == "${BUILD2_ID}" ]]; then
  echo "INFO: image digests match (${BUILD1_ID})"
else
  echo "INFO: image digests differ (expected - layer mtimes/order may differ)"
  echo "  build 1: ${BUILD1_ID}"
  echo "  build 2: ${BUILD2_ID}"
fi

echo ""
if [[ "${FAILED}" -ne 0 ]]; then
  echo "RESULT: reproducibility check FAILED (${FAILED} artifact(s) differ)"
  echo "Artifacts and diffs: ${VERIFY_DIR}"
  exit 1
fi

echo "RESULT: all authoritative artifacts match - build is reproducible"
echo "Artifacts and diffs: ${VERIFY_DIR}"