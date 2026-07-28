#!/usr/bin/env python3
"""Integration tests for the real PR-body and release-note shell scripts."""

import os
import shutil
import subprocess
import tempfile
from pathlib import Path


SOURCE_ROOT = Path(__file__).resolve().parent.parent
SHA = "0123456789abcdef0123456789abcdef01234567"


def run(command, cwd, env=None, check=True):
    result = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )
    if check and result.returncode != 0:
        raise AssertionError(
            f"Command failed ({result.returncode}): {command}\n{result.stderr}"
        )
    return result


def copy_runtime(repo):
    (repo / "scripts").mkdir()
    (repo / "locks").mkdir()
    for name in (
        "generate-pr-body.sh",
        "generate-release-notes.sh",
        "versions_env.py",
        "versions_diff.py",
    ):
        shutil.copy2(SOURCE_ROOT / "scripts" / name, repo / "scripts" / name)
    shutil.copy2(SOURCE_ROOT / "versions.env", repo / "versions.env")
    for source in (SOURCE_ROOT / "locks").iterdir():
        if source.is_file():
            shutil.copy2(source, repo / "locks" / source.name)


def init_repo(repo):
    run(["git", "init", "-b", "main"], repo)
    run(["git", "config", "user.name", "Integration Test"], repo)
    run(["git", "config", "user.email", "test@example.invalid"], repo)


def commit_all(repo, message):
    run(["git", "add", "."], repo)
    run(["git", "commit", "-m", message], repo)
    return run(["git", "rev-parse", "HEAD"], repo).stdout.strip()


def replace_env_value(path, key, value):
    lines = path.read_text().splitlines()
    updated = [f"{key}={value}" if line.startswith(f"{key}=") else line for line in lines]
    path.write_text("\n".join(updated) + "\n")


def test_pr_body_uses_worktree_and_fallback_base():
    with tempfile.TemporaryDirectory() as directory:
        repo = Path(directory) / "repo"
        repo.mkdir()
        copy_runtime(repo)
        init_repo(repo)
        baseline = commit_all(repo, "baseline")
        (repo / "README.test").write_text("second commit\n")
        commit_all(repo, "head")
        run(["git", "update-ref", "refs/remotes/origin/main", baseline], repo)

        no_changes = run(
            ["bash", str(repo / "scripts" / "generate-pr-body.sh")],
            Path(directory),
            check=False,
        )
        assert no_changes.returncode == 1
        assert "detected no changes" in no_changes.stdout

        replace_env_value(repo / "versions.env", "UV_VERSION", "99.0.0")
        with (repo / "versions.env").open("a") as stream:
            stream.write("BRAND_NEW_VAR=1.0.0\n")
        versions = repo / "versions.env"
        versions.write_text(
            "\n".join(
                line for line in versions.read_text().splitlines()
                if not line.startswith("ACCELERATE_VERSION=")
            ) + "\n"
        )
        (repo / "locks" / "python-runtime.txt").write_text("working tree lock\n")

        result = run(
            ["bash", str(repo / "scripts" / "generate-pr-body.sh")],
            Path(directory),
        )
        assert "**uv**:" in result.stdout
        assert "**BRAND_NEW_VAR**: (added) -> 1.0.0" in result.stdout
        assert "**Accelerate**:" in result.stdout
        assert "-> (removed)" in result.stdout
        assert "**python runtime lock**:" in result.stdout

        run(["git", "update-ref", "-d", "refs/remotes/origin/main"], repo)
        fallback = run(
            ["bash", str(repo / "scripts" / "generate-pr-body.sh")],
            Path(directory),
        )
        assert "**uv**:" in fallback.stdout
        assert "**python runtime lock**:" in fallback.stdout

        (repo / "locks" / "apt-packages.txt").unlink()
        missing_lock = run(
            ["bash", str(repo / "scripts" / "generate-pr-body.sh")],
            Path(directory),
        )
        assert "**apt packages**:" in missing_lock.stdout
        assert "-> (missing)" in missing_lock.stdout


def test_pr_body_supports_single_commit_shallow_checkout():
    with tempfile.TemporaryDirectory() as directory:
        repo = Path(directory) / "repo"
        repo.mkdir()
        copy_runtime(repo)
        init_repo(repo)
        commit_all(repo, "shallow baseline")

        missing_parent = run(
            ["git", "rev-parse", "--verify", "HEAD~1"],
            repo,
            check=False,
        )
        assert missing_parent.returncode != 0

        replace_env_value(repo / "versions.env", "UV_VERSION", "99.0.0")
        result = run(
            ["bash", str(repo / "scripts" / "generate-pr-body.sh")],
            Path(directory),
        )
        assert "**uv**:" in result.stdout
        assert "99.0.0" in result.stdout


def test_release_notes_compare_real_tags_and_lockfiles():
    with tempfile.TemporaryDirectory() as directory:
        repo = Path(directory) / "repo"
        repo.mkdir()
        copy_runtime(repo)
        init_repo(repo)

        replace_env_value(repo / "versions.env", "UV_VERSION", "0.0.1")
        (repo / "locks" / "python-runtime.txt").write_text("old lock\n")
        commit_all(repo, "previous release")
        run(["git", "tag", "v0.1.0-gb10.0"], repo)

        shutil.copy2(SOURCE_ROOT / "versions.env", repo / "versions.env")
        shutil.copy2(
            SOURCE_ROOT / "locks" / "python-runtime.txt",
            repo / "locks" / "python-runtime.txt",
        )
        current_sha = commit_all(repo, "current release")
        run(["git", "tag", "v0.2.0-gb10.0"], repo)

        env = os.environ.copy()
        env.update({"TAG": "v0.2.0-gb10.0", "GITHUB_SHA": current_sha})
        result = run(
            ["bash", str(repo / "scripts" / "generate-release-notes.sh")],
            Path(directory),
            env=env,
        )
        assert "Changed components (vs v0.1.0-gb10.0)" in result.stdout
        assert "**uv**: 0.0.1 ->" in result.stdout
        assert "**python runtime lock**:" in result.stdout
        assert f"commit/{current_sha}" in result.stdout


def main():
    tests = [
        test_pr_body_uses_worktree_and_fallback_base,
        test_pr_body_supports_single_commit_shallow_checkout,
        test_release_notes_compare_real_tags_and_lockfiles,
    ]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"All {len(tests)} script integration tests passed!")


if __name__ == "__main__":
    main()
