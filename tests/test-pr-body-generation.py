#!/usr/bin/env python3
"""
Tests for PR body generation (scripts/generate-pr-body.sh logic).

Tests cover:
  - diff_from_git_diff parsing
  - format_changes_with_lockfiles output
  - Lockfile change detection
  - Apt snapshot date extraction
  - Edge cases: empty diff, new/removed variables, comment lines,
    commit SHA changes, multi-hunk diffs
"""

import hashlib
import sys
import textwrap

sys.path.insert(0, "scripts")
from versions_diff import (
    COMPONENT_LABELS,
    LOCKFILES,
    diff_from_git_diff,
    format_changes_with_lockfiles,
    format_change_lines,
    file_sha256,
    extract_apt_snapshot_date,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

def build_body(component_changes, lock_changes=None):
    """Build the full PR body markdown (mirrors generate-pr-body.sh logic)."""
    if lock_changes is None:
        lock_changes = {}
    change_lines = format_changes_with_lockfiles(
        component_changes, lock_changes, COMPONENT_LABELS
    )
    lines = ["### Changes in this PR", ""]
    if change_lines:
        lines.extend(change_lines)
    else:
        lines.append("- No component changes detected (unexpected)")
    return "\n".join(lines) + "\n"


# Shortcut: parse diff text and build body in one step
def body_from_diff(diff_text, lock_changes=None):
    changes = diff_from_git_diff(diff_text)
    # Filter out GB10_BUILD (same as generate-pr-body.sh does)
    component_changes = {k: v for k, v in changes.items() if k != "GB10_BUILD"}
    return build_body(component_changes, lock_changes)


def body_body(body):
    """Extract the change lines (after '### Changes in this PR') from a body."""
    lines = body.splitlines()
    try:
        idx = lines.index("### Changes in this PR")
    except ValueError:
        return []
    rest = lines[idx + 1:]
    # Skip blank line after header
    if rest and rest[0] == "":
        rest = rest[1:]
    return [l for l in rest if l]


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------

SCENARIOS = [
    # (name, diff_text, lock_changes, expected_change_count, expected_body_lines)
    (
        "Single component update (vLLM only)",
        textwrap.dedent("""\
        diff --git a/versions.env b/versions.env
        index abc..def 100644
        --- a/versions.env
        +++ b/versions.env
        @@ -1,5 +1,5 @@
        -VLLM_REF=v0.24.0
        +VLLM_REF=v0.25.1
        """),
        {},
        1,
        ["- **vLLM**: v0.24.0 -> v0.25.1"],
    ),
    (
        "Multiple component updates",
        textwrap.dedent("""\
        diff --git a/versions.env b/versions.env
        index abc..def 100644
        --- a/versions.env
        +++ b/versions.env
        @@ -1,5 +1,5 @@
        -VLLM_REF=v0.24.0
        +VLLM_REF=v0.25.1
        -UV_VERSION=0.11.29
        +UV_VERSION=0.11.30
        -NCCL_REF=v2.30.4-1
        +NCCL_REF=v2.30.5-1
        -FLASHINFER_REF=v0.6.12
        +FLASHINFER_REF=v0.6.13
        -TORCH_VERSION=2.10.0
        +TORCH_VERSION=2.11.0
        -RAY_VERSION=2.55.0
        +RAY_VERSION=2.56.0
        """),
        {},
        6,
        [
            "- **FlashInfer**: v0.6.12 -> v0.6.13",
            "- **NCCL**: v2.30.4-1 -> v2.30.5-1",
            "- **Ray**: 2.55.0 -> 2.56.0",
            "- **PyTorch**: 2.10.0 -> 2.11.0",
            "- **uv**: 0.11.29 -> 0.11.30",
            "- **vLLM**: v0.24.0 -> v0.25.1",
        ],
    ),
    (
        "CUDA base with slashes in value + GB10_BUILD (GB10_BUILD filtered)",
        textwrap.dedent("""\
        diff --git a/versions.env b/versions.env
        index abc..def 100644
        --- a/versions.env
        +++ b/versions.env
        @@ -1,5 +1,5 @@
        -CUDA_BASE_IMAGE=nvidia/cuda:13.2.0-devel-ubuntu24.04
        +CUDA_BASE_IMAGE=nvidia/cuda:13.3.0-devel-ubuntu24.04
        -GB10_BUILD=1
        +GB10_BUILD=2
        """),
        {},
        1,
        [
            "- **CUDA base**: nvidia/cuda:13.2.0-devel-ubuntu24.04"
            " -> nvidia/cuda:13.3.0-devel-ubuntu24.04",
        ],
    ),
    (
        "UV version bump only",
        textwrap.dedent("""\
        diff --git a/versions.env b/versions.env
        index abc..def 100644
        --- a/versions.env
        +++ b/versions.env
        @@ -1,5 +1,5 @@
        -UV_VERSION=0.11.29
        +UV_VERSION=0.11.30
        """),
        {},
        1,
        ["- **uv**: 0.11.29 -> 0.11.30"],
    ),
    (
        "No changes (empty diff, no lock changes)",
        "",
        {},
        1,
        ["- No component changes detected (unexpected)"],
    ),
    (
        "GB10_BUILD increment alongside vLLM ref change",
        textwrap.dedent("""\
        diff --git a/versions.env b/versions.env
        index abc..def 100644
        --- a/versions.env
        +++ b/versions.env
        @@ -1,5 +1,5 @@
        -VLLM_REF=v0.25.0
        +VLLM_REF=v0.25.1
        -GB10_BUILD=5
        +GB10_BUILD=6
        """),
        {},
        1,
        [
            "- **vLLM**: v0.25.0 -> v0.25.1",
        ],
    ),
    (
        "FlashInfer mismatch correction matching vLLM pin",
        textwrap.dedent("""\
        diff --git a/versions.env b/versions.env
        index abc..def 100644
        --- a/versions.env
        +++ b/versions.env
        @@ -1,5 +1,5 @@
        -FLASHINFER_REF=v0.6.12
        +FLASHINFER_REF=v0.6.13
        """),
        {},
        1,
        ["- **FlashInfer**: v0.6.12 -> v0.6.13"],
    ),
    (
        "PyTorch, torchvision, torchaudio all bump together",
        textwrap.dedent("""\
        diff --git a/versions.env b/versions.env
        index abc..def 100644
        --- a/versions.env
        +++ b/versions.env
        @@ -1,5 +1,5 @@
        -TORCH_VERSION=2.10.0
        +TORCH_VERSION=2.11.0
        -TORCHVISION_VERSION=0.25.0
        +TORCHVISION_VERSION=0.26.0
        -TORCHAUDIO_VERSION=2.10.0
        +TORCHAUDIO_VERSION=2.11.0
        """),
        {},
        3,
        [
            "- **torchaudio**: 2.10.0 -> 2.11.0",
            "- **torchvision**: 0.25.0 -> 0.26.0",
            "- **PyTorch**: 2.10.0 -> 2.11.0",
        ],
    ),
    (
        "Unknown variable name in diff uses key as label",
        textwrap.dedent("""\
        diff --git a/versions.env b/versions.env
        index abc..def 100644
        --- a/versions.env
        +++ b/versions.env
        @@ -1,5 +1,5 @@
        -SOME_NEW_VAR=old
        +SOME_NEW_VAR=new
        """),
        {},
        1,
        ["- **SOME_NEW_VAR**: old -> new"],
    ),
    (
        "Variable removed entirely (no + counterpart) includes removed entry",
        textwrap.dedent("""\
        diff --git a/versions.env b/versions.env
        index abc..def 100644
        --- a/versions.env
        +++ b/versions.env
        @@ -1,5 +1,5 @@
        -OBSOLETE_VAR=old_value
        -VLLM_REF=v0.24.0
        +VLLM_REF=v0.25.1
        """),
        {},
        2,
        [
            "- **OBSOLETE_VAR**: old_value -> (removed)",
            "- **vLLM**: v0.24.0 -> v0.25.1",
        ],
    ),
    (
        "Variable added entirely (no - counterpart)",
        textwrap.dedent("""\
        diff --git a/versions.env b/versions.env
        index abc..def 100644
        --- a/versions.env
        +++ b/versions.env
        @@ -1,5 +1,5 @@
        -VLLM_REF=v0.24.0
        +VLLM_REF=v0.25.1
        +BRAND_NEW_VAR=1.0.0
        """),
        {},
        1,
        [
            "- **vLLM**: v0.24.0 -> v0.25.1",
        ],
    ),
    (
        "URL with slashes in value (PYTORCH_INDEX_URL change)",
        textwrap.dedent("""\
        diff --git a/versions.env b/versions.env
        index abc..def 100644
        --- a/versions.env
        +++ b/versions.env
        @@ -1,5 +1,5 @@
        -PYTORCH_INDEX_URL=https://download.pytorch.org/whl/cu130
        +PYTORCH_INDEX_URL=https://download.pytorch.org/whl/cu131
        """),
        {},
        1,
        [
            "- **PYTORCH_INDEX_URL**: "
            "https://download.pytorch.org/whl/cu130 -> "
            "https://download.pytorch.org/whl/cu131"
        ],
    ),
    (
        "Comment lines in diff are ignored",
        textwrap.dedent("""\
        diff --git a/versions.env b/versions.env
        index abc..def 100644
        --- a/versions.env
        +++ b/versions.env
        @@ -1,7 +1,7 @@
        -# This is a comment line
        -VLLM_REF=v0.24.0
        +VLLM_REF=v0.25.1
        -# Another comment that changed
        +# Another comment that changed (updated)
        -UV_VERSION=0.11.29
        +UV_VERSION=0.11.30
        """),
        {},
        2,
        [
            "- **uv**: 0.11.29 -> 0.11.30",
            "- **vLLM**: v0.24.0 -> v0.25.1",
        ],
    ),
    (
        "Commit SHA changes with ref changes (bump.sh resolved commit)",
        textwrap.dedent("""\
        diff --git a/versions.env b/versions.env
        index abc..def 100644
        --- a/versions.env
        +++ b/versions.env
        @@ -1,5 +1,5 @@
        -NCCL_COMMIT=73cf112295c33aee2b895f329f592f2a9b4b0f97
        +NCCL_COMMIT=aabbccddee1234567890abcdef1234567890abcd
        -VLLM_REF=v0.25.1
        +VLLM_REF=v0.26.0
        -VLLM_COMMIT=752a3a504485790a2e8491cacbb35c137339ad34
        +VLLM_COMMIT=ffee1234567890abcdef1234567890abcdef1234
        -FLASHINFER_COMMIT=57ba7eeb7ea3003a2d6ad5d9a057c4f952709bac
        +FLASHINFER_COMMIT=bbbbccccddddeeee1234567890abcdef12345678
        """),
        {},
        4,
        [
            "- **FLASHINFER_COMMIT**: "
            "57ba7eeb7ea3003a2d6ad5d9a057c4f952709bac -> "
            "bbbbccccddddeeee1234567890abcdef12345678",
            "- **NCCL_COMMIT**: "
            "73cf112295c33aee2b895f329f592f2a9b4b0f97 -> "
            "aabbccddee1234567890abcdef1234567890abcd",
            "- **VLLM_COMMIT**: "
            "752a3a504485790a2e8491cacbb35c137339ad34 -> "
            "ffee1234567890abcdef1234567890abcdef1234",
            "- **vLLM**: v0.25.1 -> v0.26.0",
        ],
    ),
    (
        "Diff across multiple hunks",
        textwrap.dedent("""\
        diff --git a/versions.env b/versions.env
        index abc..def 100644
        --- a/versions.env
        +++ b/versions.env
        @@ -1,5 +1,5 @@
        -VLLM_REF=v0.24.0
        +VLLM_REF=v0.25.1
        @@ -10,14 +10,14 @@
        -RAY_VERSION=2.55.0
        +RAY_VERSION=2.56.0
        """),
        {},
        2,
        [
            "- **Ray**: 2.55.0 -> 2.56.0",
            "- **vLLM**: v0.24.0 -> v0.25.1",
        ],
    ),
    (
        "Only commit SHAs change, refs stay same (rebuild only)",
        textwrap.dedent("""\
        diff --git a/versions.env b/versions.env
        index abc..def 100644
        --- a/versions.env
        +++ b/versions.env
        @@ -1,5 +1,5 @@
        -NCCL_COMMIT=73cf112295c33aee2b895f329f592f2a9b4b0f97
        +NCCL_COMMIT=newsha111111111111111111111111111111111111
        -VLLM_COMMIT=752a3a504485790a2e8491cacbb35c137339ad34
        +VLLM_COMMIT=newsha222222222222222222222222222222222222
        -FLASHINFER_COMMIT=57ba7eeb7ea3003a2d6ad5d9a057c4f952709bac
        +FLASHINFER_COMMIT=newsha3333333333333333333333333333333333
        """),
        {},
        3,
        [
            "- **FLASHINFER_COMMIT**: "
            "57ba7eeb7ea3003a2d6ad5d9a057c4f952709bac -> "
            "newsha3333333333333333333333333333333333",
            "- **NCCL_COMMIT**: "
            "73cf112295c33aee2b895f329f592f2a9b4b0f97 -> "
            "newsha111111111111111111111111111111111111",
            "- **VLLM_COMMIT**: "
            "752a3a504485790a2e8491cacbb35c137339ad34 -> "
            "newsha222222222222222222222222222222222222",
        ],
    ),
    (
        "Version bump + lockfile changes together (uv + all lockfiles)",
        textwrap.dedent("""\
        diff --git a/versions.env b/versions.env
        index abc..def 100644
        --- a/versions.env
        +++ b/versions.env
        @@ -1,5 +1,5 @@
        -UV_VERSION=0.11.29
        +UV_VERSION=0.11.30
        """),
        {
            "LOCK:locks/python-bootstrap.txt": (
                "sha256:e6d414e1c17a",
                "sha256:e5031a741513",
            ),
            "LOCK:locks/python-build.txt": (
                "sha256:77f8a45e76ba",
                "sha256:6a2812bb292d",
            ),
            "LOCK:locks/python-runtime.txt": (
                "sha256:91eda20c8c46",
                "sha256:5fc181aa35a8",
            ),
        },
        4,
        [
            "- **uv**: 0.11.29 -> 0.11.30",
            "- **python bootstrap lock**: "
            "`e6d414e1c17a` -> `e5031a741513`",
            "- **python build lock**: "
            "`77f8a45e76ba` -> `6a2812bb292d`",
            "- **python runtime lock**: "
            "`91eda20c8c46` -> `5fc181aa35a8`",
        ],
    ),
    (
        "Apt snapshot date change in lockfiles",
        textwrap.dedent("""\
        diff --git a/versions.env b/versions.env
        index abc..def 100644
        --- a/versions.env
        +++ b/versions.env
        @@ -1,5 +1,5 @@
        -UV_VERSION=0.11.29
        +UV_VERSION=0.11.30
        """),
        {
            "LOCK:locks/apt-sources.list": (
                "20260601T000000Z",
                "20260714T000000Z",
            ),
        },
        2,
        [
            "- **uv**: 0.11.29 -> 0.11.30",
            "- **apt snapshot**: 20260601T000000Z -> 20260714T000000Z",
        ],
    ),
    (
        "Lockfiles only, no versions.env changes",
        "",
        {
            "LOCK:locks/apt-packages.txt": (
                "sha256:aaaa11111111",
                "sha256:bbbb22222222",
            ),
            "LOCK:locks/python-bootstrap.txt": (
                "sha256:cccc33333333",
                "sha256:dddd44444444",
            ),
        },
        2,
        [
            "- **apt packages**: "
            "`aaaa11111111` -> `bbbb22222222`",
            "- **python bootstrap lock**: "
            "`cccc33333333` -> `dddd44444444`",
        ],
    ),
    (
        "Apt snapshot same date but content hash differs",
        textwrap.dedent("""\
        diff --git a/versions.env b/versions.env
        index abc..def 100644
        --- a/versions.env
        +++ b/versions.env
        @@ -1,5 +1,5 @@
        -VLLM_REF=v0.24.0
        +VLLM_REF=v0.25.1
        """),
        {
            "LOCK:locks/apt-sources.list": (
                "sha256:aaaa11111111",
                "sha256:bbbb22222222",
            ),
        },
        2,
        [
            "- **vLLM**: v0.24.0 -> v0.25.1",
            "- **apt snapshot**: "
            "`aaaa11111111` -> `bbbb22222222`",
        ],
    ),
    (
        "Version bump + apt snapshot date + lockfile hash changes",
        textwrap.dedent("""\
        diff --git a/versions.env b/versions.env
        index abc..def 100644
        --- a/versions.env
        +++ b/versions.env
        @@ -1,5 +1,5 @@
        -TORCH_VERSION=2.10.0
        +TORCH_VERSION=2.11.0
        """),
        {
            "LOCK:locks/apt-sources.list": (
                "20260601T000000Z",
                "20260714T000000Z",
            ),
            "LOCK:locks/python-runtime.txt": (
                "sha256:aaa111",
                "sha256:bbb222",
            ),
        },
        3,
        [
            "- **PyTorch**: 2.10.0 -> 2.11.0",
            "- **apt snapshot**: 20260601T000000Z -> 20260714T000000Z",
            "- **python runtime lock**: "
            "`aaa111` -> `bbb222`",
        ],
    ),
    (
        "Unknown lockfile path uses path as fallback label",
        textwrap.dedent("""\
        diff --git a/versions.env b/versions.env
        index abc..def 100644
        --- a/versions.env
        +++ b/versions.env
        @@ -1,5 +1,5 @@
        -VLLM_REF=v0.24.0
        +VLLM_REF=v0.25.1
        """),
        {
            "LOCK:locks/some-new-lock.txt": (
                "sha256:aaaa11111111",
                "sha256:bbbb22222222",
            ),
        },
        2,
        [
            "- **vLLM**: v0.24.0 -> v0.25.1",
            "- **locks/some-new-lock.txt**: "
            "`aaaa11111111` -> `bbbb22222222`",
        ],
    ),
    (
        "Lockfile value without sha256: prefix left as-is",
        textwrap.dedent("""\
        diff --git a/versions.env b/versions.env
        index abc..def 100644
        --- a/versions.env
        +++ b/versions.env
        @@ -1,5 +1,5 @@
        -VLLM_REF=v0.24.0
        +VLLM_REF=v0.25.1
        """),
        {
            "LOCK:locks/custom-tag.txt": (
                "v1.0",
                "v2.0",
            ),
        },
        2,
        [
            "- **vLLM**: v0.24.0 -> v0.25.1",
            "- **locks/custom-tag.txt**: v1.0 -> v2.0",
        ],
    ),
    (
        "All known components change at once -- smoke test",
        textwrap.dedent("""\
        diff --git a/versions.env b/versions.env
        index abc..def 100644
        --- a/versions.env
        +++ b/versions.env
        @@ -1,5 +1,5 @@
        -VLLM_REF=v0.24.0
        +VLLM_REF=v0.25.1
        -FLASHINFER_REF=v0.6.12
        +FLASHINFER_REF=v0.6.13
        -NCCL_REF=v2.30.4-1
        +NCCL_REF=v2.30.5-1
        -UV_VERSION=0.11.29
        +UV_VERSION=0.11.30
        -TORCH_VERSION=2.10.0
        +TORCH_VERSION=2.11.0
        -TORCHVISION_VERSION=0.25.0
        +TORCHVISION_VERSION=0.26.0
        -TORCHAUDIO_VERSION=2.10.0
        +TORCHAUDIO_VERSION=2.11.0
        -TRITON_VERSION=3.5.0
        +TRITON_VERSION=3.6.0
        -NVSHMEM_VERSION=3.4.0
        +NVSHMEM_VERSION=3.4.5
        -TVM_FFI_VERSION=0.1.8
        +TVM_FFI_VERSION=0.1.9
        -TILELANG_VERSION=0.1.8
        +TILELANG_VERSION=0.1.9
        -NUMBA_VERSION=0.64.0
        +NUMBA_VERSION=0.65.0
        -RAY_VERSION=2.55.0
        +RAY_VERSION=2.56.0
        -FASTSAFETENSORS_VERSION=0.2.1
        +FASTSAFETENSORS_VERSION=0.2.2
        -INSTANTTENSOR_VERSION=0.1.7
        +INSTANTTENSOR_VERSION=0.1.8
        -CUDA_BASE_IMAGE=nvidia/cuda:13.2.0-devel-ubuntu24.04
        +CUDA_BASE_IMAGE=nvidia/cuda:13.3.0-devel-ubuntu24.04
        -BITSANDBYTES_VERSION=0.48.0
        +BITSANDBYTES_VERSION=0.49.2
        -ACCELERATE_VERSION=1.13.0
        +ACCELERATE_VERSION=1.14.0
        """),
        {},
        18,
        [
            "- **Accelerate**: 1.13.0 -> 1.14.0",
            "- **bitsandbytes**: 0.48.0 -> 0.49.2",
            "- **CUDA base**: nvidia/cuda:13.2.0-devel-ubuntu24.04"
            " -> nvidia/cuda:13.3.0-devel-ubuntu24.04",
            "- **fastsafetensors**: 0.2.1 -> 0.2.2",
            "- **FlashInfer**: v0.6.12 -> v0.6.13",
            "- **instanttensor**: 0.1.7 -> 0.1.8",
            "- **NCCL**: v2.30.4-1 -> v2.30.5-1",
            "- **Numba**: 0.64.0 -> 0.65.0",
            "- **NVSHMEM**: 3.4.0 -> 3.4.5",
            "- **Ray**: 2.55.0 -> 2.56.0",
            "- **TileLang**: 0.1.8 -> 0.1.9",
            "- **torchaudio**: 2.10.0 -> 2.11.0",
            "- **torchvision**: 0.25.0 -> 0.26.0",
            "- **PyTorch**: 2.10.0 -> 2.11.0",
            "- **Triton**: 3.5.0 -> 3.6.0",
            "- **TVM-FFI**: 0.1.8 -> 0.1.9",
            "- **uv**: 0.11.29 -> 0.11.30",
            "- **vLLM**: v0.24.0 -> v0.25.1",
        ],
    ),
]


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

passed = 0
failed = 0


def check(condition, message):
    global passed, failed
    if condition:
        passed += 1
    else:
        failed += 1
        print(f"  FAIL: {message}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Run scenario tests
# ---------------------------------------------------------------------------

for name, diff_text, lock_changes, expected_count, expected_lines in SCENARIOS:
    print(f"Scenario: {name}")
    try:
        body = body_from_diff(diff_text, lock_changes)
        body_lines = body_body(body)

        actual_count = len(body_lines)
        check(
            actual_count == expected_count,
            f"Expected {expected_count} change lines, got {actual_count}",
        )

        for i, expected in enumerate(expected_lines):
            if i < len(body_lines):
                check(
                    body_lines[i] == expected,
                    f"Line {i}: expected {expected!r}, got {body_lines[i]!r}",
                )
            else:
                check(False, f"Missing line {i}: {expected!r}")

        print(f"  Body lines ({actual_count}):")
        for line in body_lines:
            print(f"    {line}")
    except Exception as e:
        failed += 1
        print(f"  EXCEPTION: {e}", file=sys.stderr)
    print()


# Additional unit tests for utility functions
print("Unit tests: file_sha256")
h = file_sha256("hello")
check(len(h) == 12, f"Expected 12 chars, got {len(h)}: {h}")
check(h == hashlib.sha256(b"hello").hexdigest()[:12], "SHA256 mismatch")

print()
print("Unit tests: _format_lockfile_value")
# Import the private helper directly
from versions_diff import _format_lockfile_value
check(
    _format_lockfile_value("sha256:abc123def456") == "`abc123def456`",
    f"sha256 prefix: {_format_lockfile_value('sha256:abc123def456')!r}",
)
check(
    _format_lockfile_value("20260601T000000Z") == "20260601T000000Z",
    f"date: {_format_lockfile_value('20260601T000000Z')!r}",
)
check(
    _format_lockfile_value("v1.0") == "v1.0",
    f"plain tag: {_format_lockfile_value('v1.0')!r}",
)
check(
    _format_lockfile_value("") == "",
    f"empty: {_format_lockfile_value('')!r}",
)
# Value containing sha256: but not as prefix should not be stripped
check(
    _format_lockfile_value("foo sha256:bar") == "foo sha256:bar",
    f"embedded sha256: {_format_lockfile_value('foo sha256:bar')!r}",
)

print()
print("Unit tests: extract_apt_snapshot_date")
content = textwrap.dedent("""\
    deb https://snapshot.ubuntu.com/ubuntu/20260601T000000Z/ noble main
    deb https://snapshot.ubuntu.com/ubuntu/20260601T000000Z/ noble-updates main
""")
d = extract_apt_snapshot_date(content)
check(d == "20260601T000000Z", f"Expected 20260601T000000Z, got {d!r}")

d2 = extract_apt_snapshot_date("no match here")
check(d2 is None, f"Expected None, got {d2!r}")

d3 = extract_apt_snapshot_date("")
check(d3 is None, f"Expected None for empty, got {d3!r}")

print()
print()
if failed:
    print(f"FAILED: {failed} of {passed + failed} checks failed")
    sys.exit(1)
else:
    print(f"All {passed} checks passed!")
    sys.exit(0)
