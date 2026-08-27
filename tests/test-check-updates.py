#!/usr/bin/env python3
"""Tests for check-updates.sh using deterministic mocked upstream APIs."""

import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path


SOURCE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SOURCE_ROOT / "scripts"))
MONITOR_POLICY_SPEC = importlib.util.spec_from_file_location(
    "validate_monitor_update",
    SOURCE_ROOT / "scripts" / "validate-monitor-update.py",
)
MONITOR_POLICY = importlib.util.module_from_spec(MONITOR_POLICY_SPEC)
assert MONITOR_POLICY_SPEC.loader is not None
MONITOR_POLICY_SPEC.loader.exec_module(MONITOR_POLICY)
VERSIONS_DIFF_SPEC = importlib.util.spec_from_file_location(
    "versions_diff",
    SOURCE_ROOT / "scripts" / "versions_diff.py",
)
VERSIONS_DIFF = importlib.util.module_from_spec(VERSIONS_DIFF_SPEC)
assert VERSIONS_DIFF_SPEC.loader is not None
VERSIONS_DIFF_SPEC.loader.exec_module(VERSIONS_DIFF)

MONITOR_FIXTURE_BASELINE = {
    "CUDA_BASE_DIGEST": (
        "sha256:a5b6256e470196fc1d5f8f62139d57d3662867746dfe1cb"
        "352d7652024047020"
    ),
    "FLASHINFER_REF": "v0.6.14",
    "NCCL_REF": "v2.30.7-1",
    "NUMBA_VERSION": "0.65.0",
    "QUACK_KERNELS_VERSION": "0.6.1",
    "TILELANG_VERSION": "0.1.9",
    "TORCHAUDIO_VERSION": "2.11.0",
    "TORCHVISION_VERSION": "0.26.0",
    "TORCH_VERSION": "2.11.0",
    "TRITON_VERSION": "3.6.0",
    "TVM_FFI_VERSION": "0.1.10",
    "UV_VERSION": "0.11.32",
    "VLLM_REF": "v0.26.0",
}

FAKE_CURL = r'''#!/usr/bin/env python3
import json
import os
import sys

url = sys.argv[-1]
if os.environ.get("FAKE_FAIL_ALL") == "1":
    sys.exit(22)
if "raw.githubusercontent.com" in url:
    if os.environ.get("FAKE_FAIL_RAW") == "1":
        sys.exit(22)
    if os.environ.get("FAKE_REQUIREMENTS"):
        print(os.environ["FAKE_REQUIREMENTS"])
    elif "/v0.26.0/" in url and os.environ.get("FAKE_CURRENT_REQUIREMENTS") != "1":
        print("""torch==9.9.0
torchvision==9.8.0
torchaudio==9.7.0
apache-tvm-ffi==9.6.0
tilelang==9.5.0
numba==9.4.0
flashinfer-python==9.3.0.post2
quack-kernels==9.3.0""")
    else:
        print("""torch==2.11.0
torchvision==0.26.0
torchaudio==2.11.0
apache-tvm-ffi==0.1.10
tilelang==0.1.9
numba==0.65.0
flashinfer-python==0.6.14
quack-kernels==0.6.1""")
elif "vllm-project/vllm/releases/latest" in url:
    print(json.dumps({"tag_name": os.environ.get("FAKE_VLLM_TAG", "v0.26.0")}))
elif "NVIDIA/nccl/releases/latest" in url:
    print(json.dumps({"tag_name": os.environ.get("FAKE_NCCL_TAG", "v2.30.7-1")}))
elif "astral-sh/uv/releases/latest" in url:
    print(json.dumps({"tag_name": os.environ.get("FAKE_UV_TAG", "0.11.32")}))
elif "flashinfer-ai/flashinfer/releases/latest" in url:
    print(json.dumps({"tag_name": "v0.6.13"}))
elif "/whl/" in url and url.endswith("/torch/"):
    if os.environ.get("FAKE_TORCH_INDEX_HTML"):
        print(os.environ["FAKE_TORCH_INDEX_HTML"])
    else:
        print("""<a href="torch-9.9.0%2Bcu130-cp312-cp312-manylinux_2_28_x86_64.whl" data-core-metadata="sha256:x">x86</a>
<a href="torch-9.9.0%2Bcu130-cp312-cp312-manylinux_2_28_aarch64.whl" data-core-metadata="sha256:a">arm64</a>
<a href="torch-2.11.0%2Bcu130-cp312-cp312-manylinux_2_28_aarch64.whl" data-core-metadata="sha256:b">current</a>""")
elif url.endswith("aarch64.whl.metadata"):
    if os.environ.get("FAKE_FAIL_TORCH_METADATA") == "1":
        sys.exit(22)
    triton_version = "9.6.0" if "torch-9.9.0" in url else "3.6.0"
    metadata = os.environ.get("FAKE_TORCH_METADATA")
    if metadata is None:
        requirement = os.environ.get(
            "FAKE_TRITON_REQUIREMENT",
            f'triton=={triton_version}; platform_system == "Linux" and python_version < "3.15"',
        )
        metadata = f"Requires-Dist: {requirement}"
    print(metadata)
elif "/pypi/triton/json" in url:
    print(json.dumps({"info": {"version": "3.6.0"}}))
elif "/pypi/nvidia-nvshmem-cu13/json" in url:
    print(json.dumps({"info": {"version": "3.4.5"}}))
elif "/pypi/" in url:
    print(json.dumps({"info": {"version": "0.0.0"}}))
elif "hub.docker.com" in url:
    print(json.dumps({"images": [{
        "architecture": "arm64",
        "digest": "sha256:a5b6256e470196fc1d5f8f62139d57d3662867746dfe1cb352d7652024047020"
    }]}))
else:
    print(f"unexpected URL: {url}", file=sys.stderr)
    sys.exit(22)
'''


def write_monitor_fixture_baseline(path):
    text = path.read_text(encoding="utf-8")
    current_values = parse_env(path)
    assert set(MONITOR_FIXTURE_BASELINE) == MONITOR_POLICY.ALLOWED_UPDATE_KEYS
    for key, value in MONITOR_FIXTURE_BASELINE.items():
        current = f"{key}={current_values[key]}"
        assert current in text
        text = text.replace(current, f"{key}={value}", 1)
    path.write_text(text, encoding="utf-8")


def setup_case(directory, source_versions=None):
    root = Path(directory) / "repo"
    (root / "scripts").mkdir(parents=True)
    (root / "locks").mkdir()
    shutil.copy2(SOURCE_ROOT / "scripts" / "check-updates.sh", root / "scripts")
    shutil.copy2(
        SOURCE_ROOT / "scripts" / "validate-monitor-update.py",
        root / "scripts",
    )
    shutil.copy2(
        SOURCE_ROOT / "scripts" / "update-versions-env.py",
        root / "scripts",
    )
    shutil.copy2(SOURCE_ROOT / "scripts" / "versions_env.py", root / "scripts")
    versions = root / "versions.env"
    if source_versions is None:
        shutil.copy2(SOURCE_ROOT / "versions.env", versions)
    else:
        versions.write_text(source_versions, encoding="utf-8")
    write_monitor_fixture_baseline(versions)
    shutil.copy2(
        SOURCE_ROOT / "locks" / "apt-sources.list",
        root / "locks" / "apt-sources.list",
    )
    fake_bin = Path(directory) / "bin"
    fake_bin.mkdir()
    fake_curl = fake_bin / "curl"
    fake_curl.write_text(FAKE_CURL)
    fake_curl.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    return root, env


def run_check(root, env, *arguments):
    return subprocess.run(
        ["bash", str(root / "scripts" / "check-updates.sh"), *arguments],
        cwd=Path(root).parent,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def parse_env(path):
    values = {}
    for line in path.read_text().splitlines():
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    return values


def test_target_vllm_requirements_are_applied():
    with tempfile.TemporaryDirectory() as directory:
        root, env = setup_case(directory)
        result = run_check(root, env, "--update")
        assert result.returncode == 0, result.stderr
        values = parse_env(root / "versions.env")
        assert values["VLLM_REF"] == "v0.26.0"
        assert values["FLASHINFER_REF"] == "v9.3.0.post2"
        assert values["TORCH_VERSION"] == "9.9.0"
        assert values["TORCHVISION_VERSION"] == "9.8.0"
        assert values["TORCHAUDIO_VERSION"] == "9.7.0"
        assert values["TRITON_VERSION"] == "9.6.0"
        assert values["TVM_FFI_VERSION"] == "9.6.0"
        assert values["TILELANG_VERSION"] == "9.5.0"
        assert values["NUMBA_VERSION"] == "9.4.0"
        assert values["QUACK_KERNELS_VERSION"] == "9.3.0"
        assert (root / "versions.env").stat().st_mode & 0o777 == 0o644
        assert "9 component(s) updated in versions.env" in result.stdout


def test_quack_aligns_to_vllm_pin_on_update():
    # Regression: when VLLM_REF bumps, vLLM's quack-kernels pin can change. If
    # QUACK_KERNELS_VERSION stays stale, bump.sh's runtime lock resolution
    # becomes unsatisfiable (quack-kernels==X vs the stale seed). The monitor
    # must align QUACK_KERNELS_VERSION to vLLM's pin.
    with tempfile.TemporaryDirectory() as directory:
        root, env = setup_case(directory)
        env["FAKE_REQUIREMENTS"] = """torch==2.11.0
torchvision==0.26.0
torchaudio==2.11.0
apache-tvm-ffi==0.1.10
tilelang==0.1.9
numba==0.65.0
flashinfer-python==0.6.14
quack-kernels==0.6.2"""
        result = run_check(root, env, "--update")
        assert result.returncode == 0, result.stderr
        values = parse_env(root / "versions.env")
        assert values["QUACK_KERNELS_VERSION"] == "0.6.2"
        assert "QuACK kernels (QUACK_KERNELS_VERSION)" in result.stdout


def test_repository_version_drift_does_not_change_fixture_results():
    with tempfile.TemporaryDirectory() as directory:
        source_versions = (SOURCE_ROOT / "versions.env").read_text(
            encoding="utf-8"
        )
        current_uv = parse_env(SOURCE_ROOT / "versions.env")["UV_VERSION"]
        source_versions = source_versions.replace(
            f"UV_VERSION={current_uv}",
            "UV_VERSION=0.12.0",
            1,
        )
        root, env = setup_case(directory, source_versions)

        assert parse_env(root / "versions.env")["UV_VERSION"] == "0.11.32"
        result = run_check(root, env, "--update")

        assert result.returncode == 0, result.stderr
        assert "9 component(s) updated in versions.env" in result.stdout


def test_every_monitored_repository_value_is_normalized():
    with tempfile.TemporaryDirectory() as directory:
        source_versions = (SOURCE_ROOT / "versions.env").read_text(
            encoding="utf-8"
        )
        source_values = parse_env(SOURCE_ROOT / "versions.env")
        for key in MONITOR_POLICY.ALLOWED_UPDATE_KEYS:
            if key == "CUDA_BASE_DIGEST":
                drifted = "sha256:" + "9" * 64
            elif key == "NCCL_REF":
                drifted = "v88.77.66-1"
            elif key.endswith("_REF"):
                drifted = "v88.77.66"
            else:
                drifted = "88.77.66"
            source_versions = source_versions.replace(
                f"{key}={source_values[key]}",
                f"{key}={drifted}",
                1,
            )

        root, _ = setup_case(directory, source_versions)

        values = parse_env(root / "versions.env")
        for key, expected in MONITOR_FIXTURE_BASELINE.items():
            assert values[key] == expected


def test_fixture_preserves_non_monitored_values_and_structure():
    with tempfile.TemporaryDirectory() as directory:
        source_versions = (SOURCE_ROOT / "versions.env").read_text(
            encoding="utf-8"
        )
        current_ray = parse_env(SOURCE_ROOT / "versions.env")["RAY_VERSION"]
        source_versions = source_versions.replace(
            f"RAY_VERSION={current_ray}",
            "RAY_VERSION=88.77.66",
            1,
        )
        root, _ = setup_case(directory, source_versions)
        normalized = (root / "versions.env").read_text(encoding="utf-8")

        assert parse_env(root / "versions.env")["RAY_VERSION"] == "88.77.66"
        source_non_values = [
            line for line in source_versions.splitlines()
            if not any(
                line.startswith(f"{key}=")
                for key in MONITOR_POLICY.ALLOWED_UPDATE_KEYS
            )
        ]
        normalized_non_values = [
            line for line in normalized.splitlines()
            if not any(
                line.startswith(f"{key}=")
                for key in MONITOR_POLICY.ALLOWED_UPDATE_KEYS
            )
        ]
        assert normalized_non_values == source_non_values


def test_dry_run_does_not_write_versions():
    with tempfile.TemporaryDirectory() as directory:
        root, env = setup_case(directory)
        before = (root / "versions.env").read_bytes()
        result = run_check(root, env)
        assert result.returncode == 0, result.stderr
        assert "have updates available" in result.stdout
        assert (root / "versions.env").read_bytes() == before


def test_valid_update_crosses_fresh_runner_policy():
    with tempfile.TemporaryDirectory() as directory:
        root, env = setup_case(directory)
        base = Path(directory) / "trusted-versions.env"
        base.write_bytes((root / "versions.env").read_bytes())

        result = run_check(root, env, "--update")
        assert result.returncode == 0, result.stderr

        validation = subprocess.run(
            [
                "python3",
                str(root / "scripts" / "validate-monitor-update.py"),
                str(base),
                str(root / "versions.env"),
            ],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert validation.returncode == 0, validation.stderr
        assert "Validated release monitor changes" in validation.stdout


def test_detected_updates_are_published_in_release_notes():
    with tempfile.TemporaryDirectory() as directory:
        root, env = setup_case(directory)
        release_note_keys = {
            key for key, _ in VERSIONS_DIFF.COMPONENTS
        }
        assert MONITOR_POLICY.ALLOWED_UPDATE_KEYS <= release_note_keys
        shutil.copy2(
            SOURCE_ROOT / "scripts" / "generate-release-notes.sh",
            root / "scripts" / "generate-release-notes.sh",
        )
        shutil.copy2(
            SOURCE_ROOT / "scripts" / "versions_diff.py",
            root / "scripts" / "versions_diff.py",
        )

        def git(*arguments):
            result = subprocess.run(
                ["git", *arguments],
                cwd=root,
                capture_output=True,
                text=True,
            )
            assert result.returncode == 0, result.stderr
            return result

        git("init", "-b", "main")
        git("config", "user.name", "Integration Test")
        git("config", "user.email", "test@example.invalid")
        git("add", ".")
        git("commit", "-m", "previous release")
        git("tag", "v0.26.0-gb10.0")
        previous = parse_env(root / "versions.env")

        result = run_check(root, env, "--update")
        assert result.returncode == 0, result.stderr

        current = parse_env(root / "versions.env")
        changes = VERSIONS_DIFF.diff_env_dicts(previous, current)
        monitored_changes = {
            key: change
            for key, change in changes.items()
            if key in MONITOR_POLICY.ALLOWED_UPDATE_KEYS
        }
        assert monitored_changes
        assert set(monitored_changes) <= {
            key for key, _ in VERSIONS_DIFF.COMPONENTS
        }

        git("add", "versions.env")
        git("commit", "-m", "detected update")
        current_sha = git("rev-parse", "HEAD").stdout.strip()
        git("tag", "v0.26.0-gb10.1")

        release_env = env.copy()
        release_env.update({
            "TAG": "v0.26.0-gb10.1",
            "GITHUB_SHA": current_sha,
        })
        notes = subprocess.run(
            ["bash", str(root / "scripts" / "generate-release-notes.sh")],
            cwd=root,
            env=release_env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert notes.returncode == 0, notes.stderr
        for key, (old_value, new_value) in monitored_changes.items():
            label = VERSIONS_DIFF.COMPONENT_LABELS[key]
            assert f"**{label}**: {old_value} -> {new_value}" in notes.stdout

        scenario_values = {
            "CUDA_BASE_DIGEST": "sha256:" + "2" * 64,
            "FLASHINFER_REF": "v0.6.16.post3",
            "NCCL_REF": "v2.30.8-1",
            "VLLM_REF": "v0.27.0",
        }
        for scenario_number, key in enumerate(
            sorted(MONITOR_POLICY.ALLOWED_UPDATE_KEYS), start=2
        ):
            if key not in scenario_values:
                scenario_values[key] = "99.99.99"
            old_value = current[key]
            new_value = scenario_values[key]
            assert old_value != new_value
            versions = root / "versions.env"
            replace = f"{key}={old_value}"
            assert replace in versions.read_text()
            versions.write_text(
                versions.read_text().replace(
                    replace, f"{key}={new_value}", 1
                )
            )
            git("add", "versions.env")
            git("commit", "-m", f"change {key}")
            current_sha = git("rev-parse", "HEAD").stdout.strip()
            tag = f"v0.26.0-gb10.{scenario_number}"
            git("tag", tag)

            release_env.update({"TAG": tag, "GITHUB_SHA": current_sha})
            notes = subprocess.run(
                ["bash", str(root / "scripts" / "generate-release-notes.sh")],
                cwd=root,
                env=release_env,
                capture_output=True,
                text=True,
                timeout=30,
            )
            assert notes.returncode == 0, notes.stderr
            label = VERSIONS_DIFF.COMPONENT_LABELS[key]
            assert f"**{label}**: {old_value} -> {new_value}" in notes.stdout
            current[key] = new_value


def test_current_stack_reports_no_updates():
    with tempfile.TemporaryDirectory() as directory:
        root, env = setup_case(directory)
        env["FAKE_VLLM_TAG"] = "v0.26.0"
        env["FAKE_CURRENT_REQUIREMENTS"] = "1"
        result = run_check(root, env)
        assert result.returncode == 0, result.stderr
        assert "All components are current" in result.stdout


def test_x86_only_triton_requirement_is_rejected_for_arm64():
    with tempfile.TemporaryDirectory() as directory:
        root, env = setup_case(directory)
        env["FAKE_VLLM_TAG"] = "v0.26.0"
        env["FAKE_CURRENT_REQUIREMENTS"] = "1"
        env["FAKE_TRITON_REQUIREMENT"] = (
            'triton==3.6.0; platform_system == "Linux" and '
            'platform_machine == "x86_64"'
        )
        result = run_check(root, env, "--update")
        assert result.returncode != 0
        assert "must have one applicable exact Triton dependency" in result.stderr


def test_torch_wheel_selection_fails_closed():
    scenarios = {
        "missing arm64 wheel": (
            '<a href="torch-2.11.0%2Bcu130-cp312-cp312-manylinux_2_28_x86_64.whl" '
            'data-core-metadata="sha256:x">x86</a>'
        ),
        "missing PEP 658 metadata": (
            '<a href="torch-2.11.0%2Bcu130-cp312-cp312-manylinux_2_28_aarch64.whl">'
            "arm64</a>"
        ),
        "duplicate arm64 wheels": (
            '<a href="torch-2.11.0%2Bcu130-cp312-cp312-manylinux_2_28_aarch64.whl" '
            'data-core-metadata="sha256:a">first</a>\n'
            '<a href="mirror/torch-2.11.0%2Bcu130-cp312-cp312-manylinux_2_28_aarch64.whl" '
            'data-core-metadata="sha256:b">second</a>'
        ),
    }
    for reason, index_html in scenarios.items():
        with tempfile.TemporaryDirectory() as directory:
            root, env = setup_case(directory)
            env["FAKE_VLLM_TAG"] = "v0.26.0"
            env["FAKE_CURRENT_REQUIREMENTS"] = "1"
            env["FAKE_TORCH_INDEX_HTML"] = index_html
            result = run_check(root, env)
            assert result.returncode != 0, reason
            assert "Could not identify one Python 3.12 arm64 Torch wheel" in result.stderr


def test_torch_metadata_failures_are_fatal():
    scenarios = {
        "missing Triton dependency": "Metadata-Version: 2.4",
        "non-exact Triton dependency": "Requires-Dist: triton>=3.6.0",
        "wrong architecture": (
            'Requires-Dist: triton==3.6.0; platform_machine == "x86_64"'
        ),
        "excluded Python": (
            'Requires-Dist: triton==3.6.0; python_version >= "3.15"'
        ),
        "unsupported marker expression": (
            'Requires-Dist: triton==3.6.0; platform_system == "Linux" or '
            'platform_machine == "aarch64"'
        ),
        "conflicting applicable pins": (
            "Requires-Dist: triton==3.6.0\n"
            "Requires-Dist: triton==3.7.1"
        ),
    }
    for reason, metadata in scenarios.items():
        with tempfile.TemporaryDirectory() as directory:
            root, env = setup_case(directory)
            env["FAKE_VLLM_TAG"] = "v0.26.0"
            env["FAKE_CURRENT_REQUIREMENTS"] = "1"
            env["FAKE_TORCH_METADATA"] = metadata
            result = run_check(root, env)
            assert result.returncode != 0, reason
            assert "must have one applicable exact Triton dependency" in result.stderr


def test_torch_metadata_fetch_failure_is_fatal():
    with tempfile.TemporaryDirectory() as directory:
        root, env = setup_case(directory)
        env["FAKE_VLLM_TAG"] = "v0.26.0"
        env["FAKE_CURRENT_REQUIREMENTS"] = "1"
        env["FAKE_FAIL_TORCH_METADATA"] = "1"
        result = run_check(root, env)
        assert result.returncode != 0


def test_one_applicable_triton_pin_is_selected():
    with tempfile.TemporaryDirectory() as directory:
        root, env = setup_case(directory)
        env["FAKE_VLLM_TAG"] = "v0.26.0"
        env["FAKE_CURRENT_REQUIREMENTS"] = "1"
        env["FAKE_TORCH_METADATA"] = (
            'Requires-Dist: triton==99.0.0; platform_machine == "x86_64"\n'
            'Requires-Dist: triton==3.6.0; platform_machine == "aarch64"\n'
            'Requires-Dist: triton==3.6.0; platform_system == "Linux"'
        )
        result = run_check(root, env)
        assert result.returncode == 0, result.stderr
        assert "All components are current" in result.stdout


def test_configured_pytorch_index_variant_selects_matching_wheel():
    with tempfile.TemporaryDirectory() as directory:
        source_versions = (SOURCE_ROOT / "versions.env").read_text(encoding="utf-8")
        source_versions = source_versions.replace(
            "PYTORCH_INDEX_URL=https://download.pytorch.org/whl/cu130",
            "PYTORCH_INDEX_URL=https://download.pytorch.org/whl/cu131",
        )
        root, env = setup_case(directory, source_versions)
        env["FAKE_VLLM_TAG"] = "v0.26.0"
        env["FAKE_CURRENT_REQUIREMENTS"] = "1"
        env["FAKE_TORCH_INDEX_HTML"] = (
            '<a href="torch-2.11.0%2Bcu131-cp312-cp312-manylinux_2_28_aarch64.whl" '
            'data-core-metadata="sha256:a">arm64</a>'
        )
        result = run_check(root, env)
        assert result.returncode == 0, result.stderr
        assert "All components are current" in result.stdout


def test_standalone_triton_update_is_rejected():
    with tempfile.TemporaryDirectory() as directory:
        root, env = setup_case(directory)
        env["FAKE_VLLM_TAG"] = "v0.26.0"
        env["FAKE_CURRENT_REQUIREMENTS"] = "1"
        env["FAKE_TRITON_REQUIREMENT"] = (
            'triton==3.7.1; platform_system == "Linux" and '
            'platform_machine == "aarch64"'
        )
        before = (root / "versions.env").read_bytes()
        result = run_check(root, env, "--update")
        assert result.returncode != 0
        assert "Refusing to update Triton without a Torch update" in result.stderr
        assert (root / "versions.env").read_bytes() == before


def test_newer_prerelease_remains_the_dependency_target():
    with tempfile.TemporaryDirectory() as directory:
        root, env = setup_case(directory)
        env["FAKE_VLLM_TAG"] = "v0.26.0"
        versions = root / "versions.env"
        text = versions.read_text().replace("VLLM_REF=v0.26.0", "VLLM_REF=v0.27.0rc1")
        versions.write_text(text)
        result = run_check(root, env)
        assert result.returncode == 0, result.stderr
        assert "pinned to newer pre-release" in result.stdout
        assert "Fetching vLLM v0.27.0rc1 requirements" in result.stderr


def test_apt_snapshot_bump_only_updates_snapshot():
    with tempfile.TemporaryDirectory() as directory:
        root, env = setup_case(directory)
        env["FAKE_VLLM_TAG"] = "v0.26.0"
        env["FAKE_CURRENT_REQUIREMENTS"] = "1"
        sources = root / "locks" / "apt-sources.list"
        stale_date = datetime.now(timezone.utc) - timedelta(days=8)
        stale_stamp = stale_date.strftime("%Y%m%dT000000Z")
        stale_display = stale_date.strftime("%Y-%m-%dT00:00:00Z")
        stale = sources.read_text().replace("20260811T000000Z", stale_stamp)
        stale = stale.replace("2026-08-11T00:00:00Z", stale_display)
        sources.write_text(stale)
        versions_before = (root / "versions.env").read_bytes()

        result = run_check(root, env, "--bump-apt-snapshot")
        assert result.returncode == 0, result.stderr
        today = datetime.now(timezone.utc).strftime("%Y%m%dT000000Z")
        assert today in sources.read_text()
        assert "snapshot advanced to today" in result.stdout
        assert (root / "versions.env").read_bytes() == versions_before
        assert sources.stat().st_mode & 0o777 == 0o644


def test_requirement_fetch_failure_is_fatal():
    with tempfile.TemporaryDirectory() as directory:
        root, env = setup_case(directory)
        env["FAKE_FAIL_RAW"] = "1"
        result = run_check(root, env)
        assert result.returncode != 0
        assert "Could not fetch vLLM requirements" in result.stderr


def test_invalid_vllm_release_tag_is_fatal():
    with tempfile.TemporaryDirectory() as directory:
        root, env = setup_case(directory)
        env["FAKE_VLLM_TAG"] = "nightly"
        result = run_check(root, env)
        assert result.returncode != 0
        assert "Could not compare vLLM versions" in result.stderr


def test_vllm_release_tag_cannot_inject_python():
    with tempfile.TemporaryDirectory() as directory:
        root, env = setup_case(directory)
        marker = Path(directory) / "python-injection-marker"
        env["ATTACK_MARKER"] = str(marker)
        env["FAKE_VLLM_TAG"] = (
            "v0.26.1');__import__('pathlib').Path("
            "__import__('os').environ['ATTACK_MARKER']).write_text('owned');#"
        )
        before = (root / "versions.env").read_bytes()

        result = run_check(root, env, "--update")

        assert result.returncode != 0
        assert not marker.exists()
        assert (root / "versions.env").read_bytes() == before


def test_release_tag_cannot_inject_sed_commands():
    with tempfile.TemporaryDirectory() as directory:
        root, env = setup_case(directory)
        marker = Path(directory) / "sed-injection-marker"
        env["FAKE_NCCL_TAG"] = f"v2.30.8-1|w {marker}"
        before = (root / "versions.env").read_bytes()

        result = run_check(root, env, "--update")

        assert result.returncode != 0
        assert "Rejected unsafe NCCL_REF candidate" in result.stderr
        assert not marker.exists()
        assert (root / "versions.env").read_bytes() == before


def test_requirement_value_metacharacters_are_rejected():
    with tempfile.TemporaryDirectory() as directory:
        root, env = setup_case(directory)
        env["FAKE_REQUIREMENTS"] = """torch==9.9.0
torchvision==9.8.0
torchaudio==9.7.0
apache-tvm-ffi==9.6.0
tilelang==9.5.0|e
numba==9.4.0
flashinfer-python==9.3.0"""
        before = (root / "versions.env").read_bytes()

        result = run_check(root, env, "--update")

        assert result.returncode != 0
        assert "Rejected unsafe TILELANG_VERSION candidate" in result.stderr
        assert (root / "versions.env").read_bytes() == before


def test_upstream_failure_is_fatal():
    with tempfile.TemporaryDirectory() as directory:
        root, env = setup_case(directory)
        env["FAKE_FAIL_ALL"] = "1"
        result = run_check(root, env)
        assert result.returncode != 0


def test_mutating_modes_are_mutually_exclusive():
    with tempfile.TemporaryDirectory() as directory:
        root, env = setup_case(directory)
        result = run_check(root, env, "--update", "--bump-apt-snapshot")
        assert result.returncode != 0
        assert "Choose either" in result.stderr


def main():
    tests = [
        test_target_vllm_requirements_are_applied,
        test_repository_version_drift_does_not_change_fixture_results,
        test_every_monitored_repository_value_is_normalized,
        test_fixture_preserves_non_monitored_values_and_structure,
        test_dry_run_does_not_write_versions,
        test_valid_update_crosses_fresh_runner_policy,
        test_detected_updates_are_published_in_release_notes,
        test_current_stack_reports_no_updates,
        test_x86_only_triton_requirement_is_rejected_for_arm64,
        test_torch_wheel_selection_fails_closed,
        test_torch_metadata_failures_are_fatal,
        test_torch_metadata_fetch_failure_is_fatal,
        test_one_applicable_triton_pin_is_selected,
        test_configured_pytorch_index_variant_selects_matching_wheel,
        test_standalone_triton_update_is_rejected,
        test_newer_prerelease_remains_the_dependency_target,
        test_apt_snapshot_bump_only_updates_snapshot,
        test_requirement_fetch_failure_is_fatal,
        test_invalid_vllm_release_tag_is_fatal,
        test_vllm_release_tag_cannot_inject_python,
        test_release_tag_cannot_inject_sed_commands,
        test_requirement_value_metacharacters_are_rejected,
        test_upstream_failure_is_fatal,
        test_mutating_modes_are_mutually_exclusive,
    ]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"All {len(tests)} check-updates tests passed!")


if __name__ == "__main__":
    main()
