#!/usr/bin/env python3
"""Tests for check-updates.sh using deterministic mocked upstream APIs."""

import os
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path


SOURCE_ROOT = Path(__file__).resolve().parent.parent

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
    if "/v0.26.0/" in url and os.environ.get("FAKE_CURRENT_REQUIREMENTS") != "1":
        print("""torch==9.9.0
torchvision==9.8.0
torchaudio==9.7.0
apache-tvm-ffi==9.6.0
tilelang==9.5.0
numba==9.4.0
flashinfer-python==9.3.0""")
    else:
        print("""torch==2.11.0
torchvision==0.26.0
torchaudio==2.11.0
apache-tvm-ffi==0.1.10
tilelang==0.1.9
numba==0.65.0
flashinfer-python==0.6.14""")
elif "vllm-project/vllm/releases/latest" in url:
    print(json.dumps({"tag_name": os.environ.get("FAKE_VLLM_TAG", "v0.26.0")}))
elif "NVIDIA/nccl/releases/latest" in url:
    print(json.dumps({"tag_name": "v2.30.7-1"}))
elif "astral-sh/uv/releases/latest" in url:
    print(json.dumps({"tag_name": "0.11.32"}))
elif "flashinfer-ai/flashinfer/releases/latest" in url:
    print(json.dumps({"tag_name": "v0.6.13"}))
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


def setup_case(directory):
    root = Path(directory) / "repo"
    (root / "scripts").mkdir(parents=True)
    (root / "locks").mkdir()
    shutil.copy2(SOURCE_ROOT / "scripts" / "check-updates.sh", root / "scripts")
    shutil.copy2(SOURCE_ROOT / "versions.env", root / "versions.env")
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
        assert values["FLASHINFER_REF"] == "v9.3.0"
        assert values["TORCH_VERSION"] == "9.9.0"
        assert values["TORCHVISION_VERSION"] == "9.8.0"
        assert values["TORCHAUDIO_VERSION"] == "9.7.0"
        assert values["TVM_FFI_VERSION"] == "9.6.0"
        assert values["TILELANG_VERSION"] == "9.5.0"
        assert values["NUMBA_VERSION"] == "9.4.0"
        assert (root / "versions.env").stat().st_mode & 0o777 == 0o644
        assert "7 component(s) updated in versions.env" in result.stdout


def test_dry_run_does_not_write_versions():
    with tempfile.TemporaryDirectory() as directory:
        root, env = setup_case(directory)
        before = (root / "versions.env").read_bytes()
        result = run_check(root, env)
        assert result.returncode == 0, result.stderr
        assert "have updates available" in result.stdout
        assert (root / "versions.env").read_bytes() == before


def test_current_stack_reports_no_updates():
    with tempfile.TemporaryDirectory() as directory:
        root, env = setup_case(directory)
        env["FAKE_VLLM_TAG"] = "v0.26.0"
        env["FAKE_CURRENT_REQUIREMENTS"] = "1"
        result = run_check(root, env)
        assert result.returncode == 0, result.stderr
        assert "All components are current" in result.stdout


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
        stale = sources.read_text().replace("20260714T000000Z", "20200101T000000Z")
        stale = stale.replace("2026-07-14T00:00:00Z", "2020-01-01T00:00:00Z")
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
        test_dry_run_does_not_write_versions,
        test_current_stack_reports_no_updates,
        test_newer_prerelease_remains_the_dependency_target,
        test_apt_snapshot_bump_only_updates_snapshot,
        test_requirement_fetch_failure_is_fatal,
        test_invalid_vllm_release_tag_is_fatal,
        test_upstream_failure_is_fatal,
        test_mutating_modes_are_mutually_exclusive,
    ]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"All {len(tests)} check-updates tests passed!")


if __name__ == "__main__":
    main()
