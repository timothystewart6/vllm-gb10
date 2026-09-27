#!/usr/bin/env python3
"""Regression tests for OCI image label and annotation contracts."""

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def read(path):
    return (ROOT / path).read_text(encoding="utf-8")


def test_dockerfile_runner_stage_declares_oci_labels():
    """The runner stage must bake in the OCI label set."""
    dockerfile = read("Dockerfile")
    runner = dockerfile.split("FROM base AS runner", 1)[1]
    runner_head = runner.split("WORKDIR /workspace", 1)[0]

    assert "ARG VLLM_REF" in runner_head
    assert "ARG GB10_BUILD" in runner_head

    required = {
        "org.opencontainers.image.title",
        "org.opencontainers.image.description",
        "org.opencontainers.image.source",
        "org.opencontainers.image.url",
        "org.opencontainers.image.version",
        "org.opencontainers.image.licenses",
    }
    labels_block = runner.split("LABEL", 1)[1].split("ENTRYPOINT", 1)[0]
    for key in required:
        assert key in labels_block, f"missing LABEL {key}"


def test_dockerfile_labels_reference_build_args():
    """Version and build labels must compose from the resolved build args."""
    dockerfile = read("Dockerfile")
    runner = dockerfile.split("FROM base AS runner", 1)[1]
    labels_block = runner.split("LABEL", 1)[1].split("ENTRYPOINT", 1)[0]
    assert 'org.opencontainers.image.version="${VLLM_REF}-gb10.${GB10_BUILD}"' in labels_block


def test_build_workflow_passes_oci_annotations_to_build():
    """Buildx must receive the metadata action annotations output so GHCR can
    render org.opencontainers.image.description on version pages. For OCI
    manifests GitHub reads the description from the manifest annotations
    field, not config.Labels, so without this pass-through version pages keep
    showing "No description provided" even though the config labels are set.
    """
    workflow = read(".github/workflows/build-image.yaml")
    assert "steps.meta.outputs.annotations" in workflow
    assert "annotations: ${{ steps.meta.outputs.annotations }}" in workflow


if __name__ == "__main__":
    test_dockerfile_runner_stage_declares_oci_labels()
    test_dockerfile_labels_reference_build_args()
    test_build_workflow_passes_oci_annotations_to_build()
    print("PASS: OCI image label and annotation contracts")
