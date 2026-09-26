#!/usr/bin/env python3
"""Contract tests for the CI serve+bench verify harness under verify/."""

import json
import re
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
VERIFY = ROOT / "verify"
WORKFLOW = ROOT / ".github" / "workflows" / "build-image.yaml"


def read(path):
    return path.read_text(encoding="utf-8")


def test_verify_harness_files_exist():
    for name in (
        "docker-compose.yaml",
        "run-verify.sh",
        "README.md",
        ".env.example",
        "models.json",
        "gen-model-override.sh",
        "summarize-bench.py",
        "render-verify-report.py",
    ):
        assert (VERIFY / name).is_file(), f"missing verify/{name}"


def test_compose_serves_under_test_image_from_env():
    compose = read(VERIFY / "docker-compose.yaml")
    assert 'image: "${IMAGE:-ghcr.io/timothystewart6/vllm-gb10' in compose
    assert "pull_policy: missing" in compose
    assert "network_mode: host" in compose
    # The base compose keeps the common runtime flags/paths but must NOT bake
    # per-model serve flags into a hardcoded command. Those live in models.json
    # and reach the server via per-model compose overrides from
    # gen-model-override.sh (a dense model must not receive Lightning's
    # MoE/humming/mamba flags, so they cannot be hardcoded here).
    for lightning_flag in (
        "--quantization=modelopt_fp4",
        "--moe-backend=humming",
        "--linear-backend=humming",
        "--mamba-backend=flashinfer",
        "--reasoning-parser=nemotron_v3",
        "--tool-call-parser=qwen3_coder",
    ):
        assert lightning_flag not in compose, (
            f"base compose must not hardcode per-model flag {lightning_flag}; "
            f"it belongs in models.json"
        )
    # The common runtime skeleton (port, host) stays interpolated from env.
    assert "- --host" in compose and "- --port" in compose
    assert "deploy:" in compose and "reservations:" in compose and "capabilities: [gpu]" in compose
    # The container must mount the host model cache and the results bind.
    assert "${HF_CACHE:-/mnt/llm/models/huggingface}:/root/.cache/huggingface" in compose
    assert "${VERIFY_RESULT_DIR:-./results}:/results" in compose


def test_catalog_test_keys_match_runnable_suites():
    """Every catalog `tests` key must be a suite the driver + model-tests.py
    actually run. A key name that matches nothing (e.g. the old
    ``tool_calling`` vs the driver's ``tool``) silently skips the suite every
    run while the harness still reports green, so the release-notes matrix
    shows '-' forever for a test the user believes is being verified."""
    catalog = json.loads(read(VERIFY / "models.json"))
    tests_py = read(VERIFY / "model-tests.py")
    driver = read(VERIFY / "run-verify.sh")

    # model-tests.py exposes one suite function per runnable suite;
    # spec_decode is a driver probe, not a model-tests suite.
    runnable = {
        m.group(1) for m in re.finditer(r'def test_(\w+)\(', tests_py)
    } | {"spec_decode"}

    # The driver's model_test_enabled lookups must be a subset of runnable.
    lookups = set(re.findall(r'model_test_enabled[^\n]*?(\w+)\s*\)?"?', driver))
    lookups = {k for k in lookups if k in ("tool", "reasoning", "multimodal", "spec_decode")}
    assert lookups <= runnable, f"driver reads suites the harness cannot run: {lookups - runnable}"

    for entry in catalog["models"]:
        tests = entry.get("tests", {})
        assert tests, f"{entry['name']} must declare a tests map"
        for key in tests:
            assert key in runnable, (
                f"catalog tests key {key!r} for {entry['name']} does not match "
                f"any runnable suite; known: {sorted(runnable)}"
            )
        # The four optional suites must all be declared (missing = silently skipped).
        for suite in ("tool", "reasoning", "multimodal", "spec_decode"):
            assert suite in tests, (
                f"{entry['name']} tests map missing required suite {suite!r}"
            )


def test_catalog_is_json_and_has_flags():
    catalog = json.loads(read(VERIFY / "models.json"))
    assert "models" in catalog and catalog["models"], "catalog must be non-empty"
    for entry in catalog["models"]:
        assert entry["name"], "each model entry needs a name"
        assert entry["model"], "each model entry needs a model id"
        assert entry["revision"], "each model entry needs a pinned revision"
        assert "serve" in entry and entry["serve"], "each model entry needs serve config"
        # Flags may be empty for a dense model, but the key must be present.
        assert "flags" in entry["serve"]


def test_override_generator_emits_model_command():
    catalog = json.loads(read(VERIFY / "models.json"))
    first = catalog["models"][0]["name"]
    gen = VERIFY / "gen-model-override.sh"
    out = subprocess.run(["bash", str(gen), first], capture_output=True, text=True, check=True)
    yaml_text = out.stdout
    assert "services:" in yaml_text and "vllm-serve:" in yaml_text
    assert "command:" in yaml_text
    # The generated command must carry the catalog's model id and flags.
    assert catalog["models"][0]["model"] in yaml_text
    for flag in catalog["models"][0]["serve"].get("flags", []):
        assert flag in yaml_text


def test_override_generator_emits_chat_template_for_gemma():
    """Models that ship no chat_template (Gemma 4) must get --chat-template.

    The template filename comes from the catalog's serve.chat_template and is
    referenced at the stable in-container mount path that the base compose
    bind-mounts read-only from CHAT_TEMPLATE_DIR. Models whose tokenizer does
    define a template must NOT receive the flag.
    """
    catalog = json.loads(read(VERIFY / "models.json"))
    gen = VERIFY / "gen-model-override.sh"
    gemma = next(m for m in catalog["models"] if m["name"] == "gemma-4-12b")
    template_name = gemma["serve"].get("chat_template")
    assert template_name, "gemma-4-12b must reference a vendored chat template"

    gemma_out = subprocess.run(
        ["bash", str(gen), "gemma-4-12b"], capture_output=True, text=True, check=True
    ).stdout
    assert "--chat-template" in gemma_out
    assert f"/etc/vllm/chat-templates/{template_name}" in gemma_out

    # A model with no chat_template in the catalog must not get the flag.
    plain = next(m for m in catalog["models"] if "chat_template" not in m["serve"])
    plain_out = subprocess.run(
        ["bash", str(gen), plain["name"]], capture_output=True, text=True, check=True
    ).stdout
    assert "--chat-template" not in plain_out

    # The base compose must mount the template dir into the container at the
    # in-container path the override references.
    compose = read(VERIFY / "docker-compose.yaml")
    assert "CHAT_TEMPLATE_DIR" in compose
    assert "/etc/vllm/chat-templates" in compose
    assert ":ro" in compose


def test_driver_is_ci_safe_and_in_container_benchy():
    driver = read(VERIFY / "run-verify.sh")
    # No sudo on the ephemeral runner: the driver must never invoke sudo (the
    # word may appear in explanatory comments, but not as a command).
    assert not re.search(r"^\s*sudo\b", driver, re.MULTILINE), "driver must not invoke sudo"
    assert "require sudo" not in driver
    # llama-benchy runs in-container via a pinned version. The `uvx` tool
    # runner installs the exact pinned release rather than resolving "latest"
    # at runtime inside the privileged container, so the benchmark tool is a
    # reviewed, locked build input.
    assert "uvx" in driver
    assert '"llama-benchy@${BENCHY_VERSION}"' in driver
    assert 'BENCHY_VERSION="${BENCHY_VERSION:-' in driver
    # exec must target the compose SERVICE name, not the container_name.
    # `docker compose exec` resolves services by the compose service name and
    # errors "is not running" when given the container_name instead.
    assert "SERVICE=\"vllm-serve\"" in driver, "SERVICE must be the compose service name"
    assert "CONTAINER=\"vllm-gb10-verify\"" in driver
    assert "exec -T \"${SERVICE}\"" in driver
    assert "--save-result" in driver
    assert "docker_compose -f" in driver
    # Fail-closed driver: any failed stage exits non-zero.
    assert "set -euo pipefail" in driver
    assert 'fail "/health not ready" in driver or "fail \"/health not ready for" in driver'
    assert "VERIFY_PASS" in driver
    # Multi-model loop: must read the catalog and iterate per-model overrides.
    assert "for MODEL_NAME in ${MODEL_LIST}" in driver
    assert "gen-model-override.sh" in driver
    assert "OVERRIDE_FILE=" in driver
    assert "--env-file /dev/null up -d" in driver


def test_driver_stages_are_stable_and_deterministic():
    """Result files must be timestamped + model-tagged so concurrent and
    multi-model runs don't clobber."""
    driver = read(VERIFY / "run-verify.sh")
    assert 'BENCH_OUT="bench-${MODEL_NAME}-${STAMP}.json"' in driver
    assert 'CONC_OUT="bench-conc-${MODEL_NAME}-${STAMP}.json"' in driver
    assert 'MODEL_META="${RESULT_DIR}/meta-${MODEL_NAME}-${STAMP}.json"' in driver
    assert 'MATRIX_OUT="${RESULT_DIR}/matrix-${MODEL_NAME}-${STAMP}.json"' in driver
    assert "summary-${STAMP}.json" in driver
    assert "summarize-bench.py" in driver


def test_driver_runs_functional_tests():
    """The driver must run the deterministic functional suite (GB10_TEST_OK)
    and gate optional per-model suites (tool/reasoning/multimodal) from the
    catalog `tests` map."""
    driver = read(VERIFY / "run-verify.sh")
    for fragment in (
        "model-tests.py",
        "DETERMINISTIC_TOKEN",
        ".tests.$2 // false",
        "VLLM_BASE_URL",
        "VLLM_RED_SQUARE",
        "functional tests",
    ):
        assert fragment in driver, f"missing '{fragment}' in driver"

    # model-tests.py must normalize and require the deterministic token. The
    # token is parameterized via VLLM_DETERMINISTIC_TOKEN (the driver passes
    # DETERMINISTIC_TOKEN through to the suite) and defaults to GB10_TEST_OK.
    tests = read(VERIFY / "model-tests.py")
    assert 'os.environ.get("VLLM_DETERMINISTIC_TOKEN", "GB10_TEST_OK")' in tests
    assert "normalize" in tests
    assert "normalize(DETERMINISTIC_TOKEN)" in tests
    # Optional suites must be wired through the env contract.
    for suite in ("tool", "reasoning", "multimodal"):
        assert f"def test_{suite}" in tests, f"missing {suite} suite in model-tests.py"


def test_driver_bench_workload_and_lifecycle():
    """The bench workload spans prompt/generation lengths plus a separate
    concurrency run, and the driver verifies clean shutdown between models."""
    driver = read(VERIFY / "run-verify.sh")
    # pp/tg matrix default expands to the requested prompt/generation lengths.
    assert 'BENCH_PP="${BENCH_PP:-128 2048 8192 32768}"' in driver
    assert 'BENCH_TG="${BENCH_TG:-32 128}"' in driver
    # A separate concurrency workload at a fixed shape.
    assert 'BENCH_CONC="${BENCH_CONC:-1 4}"' in driver
    assert 'BENCH_CONC_PP="${BENCH_CONC_PP:-2048}"' in driver
    assert 'BENCH_CONC_TG="${BENCH_CONC_TG:-128}"' in driver
    # Prompt caching disabled during bench; warmup is on by default.
    assert "--no-cache" in driver
    assert "server process exited cleanly after stop" in driver
    # Spec-decode probe present.
    assert "spec-decode generation probe" in driver


def test_workflow_verify_job_wires_serve_and_bench():
    workflow = read(WORKFLOW)
    # The verify job must run the serve+bench harness against the freshly
    # built canonical image across the model catalog (no hardcoded single
    # model id in the step anymore - the catalog drives it).
    assert "run-verify.sh" in workflow
    assert "verify/" in workflow
    assert "CANONICAL_IMAGE" in workflow
    # Results must be preserved as a CI artifact.
    assert "Upload serve and bench results" in workflow
    assert "actions/upload-artifact" in workflow
    # Security-policy invariants must hold even after wiring the harness in.
    assert workflow.count("runs-on: [self-hosted, linux, ARM64, gb10]") == 3


def test_no_new_external_actions_introduced():
    workflow = read(WORKFLOW)
    uses = re.findall(r"^\s*uses:\s*([^#\s]+)@([^#\s]+)\s*$", workflow, re.MULTILINE)
    # Only actions already reviewed (reuses upload-artifact) may appear.
    for action, revision in uses:
        assert action.startswith(("actions/", "docker/", "softprops/", "peter-evans/")), (
            f"unreviewed external action {action}@{revision}"
        )


def test_gitignore_covers_generated_overrides():
    gitignore = read(ROOT / ".gitignore")
    assert "verify/results/" in gitignore
    assert "verify/generated/" in gitignore


def test_release_bundles_json_and_logs_but_excludes_fail():
    """The release step must archive the per-model verify JSONs and server
    logs into one tarball while excluding transient fail-*.json records.

    fail-*.json capture raw error output and belong on no public release. The
    test runs the exact tar command extracted from build-image.yaml against a
    fixture dir (with a mock fail file) and asserts the archive carries json +
    log members and zero fail members. A regression here would silently
    publish failure records - or, if the glob exclude is dropped/ordered
    wrong (macOS bsdtar only honors --exclude before the operands), leak
    error output into every release artifact.
    """
    workflow = read(WORKFLOW)
    block = re.search(
        r"- name: Bundle verify results.*?\n(.*?)(?=\n      - name: )",
        workflow,
        re.DOTALL,
    )
    assert block, "build-image.yaml lost the 'Bundle verify results' step"
    bundler = block.group(1)

    # The archive must carry the per-model JSONs plus the server logs, and
    # the transient fail-*.json records must be excluded.
    assert "results/*.json results/*.log" in bundler
    assert "results/fail-*.json" in bundler
    assert "--exclude" in bundler

    tar_line = next(
        line.strip() for line in bundler.splitlines()
        if line.strip().startswith("tar -czf")
    )
    # Write the archive into the fixture working dir (the workflow writes to
    # /tmp; the split args and glob expansion are all that matter here).
    command = tar_line.replace("/tmp/verify-results-${TAG}.tar.gz", "bundle.tar.gz")

    with tempfile.TemporaryDirectory() as tmpdir:
        results = Path(tmpdir) / "results"
        results.mkdir()
        for name in (
            "meta-qwen3-0.6b-20990101-000000.json",
            "bench-qwen3-0.6b-20990101-000000.json",
            "server-qwen3-0.6b-20990101-000000.log",
        ):
            (results / name).write_text("{}", encoding="utf-8")
        fail_secret = "sk-mock-bearer-token-not-real"
        (results / f"fail-mock-99999999-999999.json").write_text(
            json.dumps({"raw_error": fail_secret}), encoding="utf-8"
        )

        subprocess.run(
            ["bash", "-c", f'cd "{tmpdir}" && {command}'],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )

        archive = Path(tmpdir) / "bundle.tar.gz"
        assert archive.is_file(), "bundle step produced no archive"
        listing = subprocess.run(
            ["tar", "-tzf", str(archive)],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        members = listing.splitlines()
        json_members = [m for m in members if m.endswith(".json")]
        log_members = [m for m in members if m.endswith(".log")]
        assert len(json_members) == 2, f"expected 2 json members, got {json_members}"
        assert len(log_members) == 1, f"expected 1 log member, got {log_members}"
        assert not any("fail" in m for m in members), (
            f"fail-*.json leaked into release bundle: {[m for m in members if 'fail' in m]}"
        )

        raw = subprocess.run(
            ["tar", "-xOzf", str(archive)],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        assert fail_secret not in raw, "fail-*.json content leaked into release bundle"

    # The release `files` glob must keep the wildcard form. A literal
    # ${TAG} is not shell-expanded by action-gh-release (it globs the input
    # itself), so a resumed literal would match nothing and the release would
    # ship without the raw-data artifact.
    assert "files: /tmp/verify-results-*.tar.gz" in workflow


def test_summarizer_reads_native_concurrency_level():
    """The concurrency bench JSON carries each run's concurrency in a native
    top-level field. The summarizer must read that field (not only a '(cN)'
    shape annotation that this llama-benchy version does not emit), otherwise
    every concurrency row collapses to 1 and the report hides the conc>1 data.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location("summarize_bench", VERIFY / "summarize-bench.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    sample = {
        "benchmarks": [
            {
                "concurrency": 1,
                "prompt_size": 2048,
                "response_size": 128,
                "tg_throughput": {"mean": 112.68},
                "tg_req_throughput": {"mean": 112.68},
                "pp_throughput": {"mean": 50803.05},
                "ttfr": {"mean": 71.28},
            },
            {
                "concurrency": 4,
                "prompt_size": 2048,
                "response_size": 128,
                "tg_throughput": {"mean": 360.39},
                "tg_req_throughput": {"mean": 95.00},
                "pp_throughput": {"mean": 37266.42},
                "ttfr": {"mean": 180.29},
            },
        ]
    }
    rows = mod.conc_rows_for_document(sample, "qwen3-0.6b")
    concs = [r["concurrency"] for r in rows]
    assert concs == [1, 4], f"expected [1, 4], got {concs}"
    # Aggregate (total) throughput must come from tg_throughput, per-request
    # from tg_req_throughput.
    c4 = rows[1]
    assert c4["tg_tok_s"] == 360.39
    assert c4["tg_req_tok_s"] == 95.0


def test_render_verify_report_compact_output():
    """The release-notes compact report must be a tight table with one row per
    model (startup, pp/tg at p=2048, and the concurrency-4 aggregate) and no
    leaked per-suite detail. It must also degrade to an empty string (not an
    error) when asked to render a dir with no result files, so a release run
    without a verify artifact simply omits the section.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location("render_verify_report", VERIFY / "render-verify-report.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        stamp = "20990101-000000"
        def write_stamp(prefix, name, obj):
            (Path(tmpdir) / f"{prefix}-{name}-{stamp}.json").write_text(
                json.dumps(obj), encoding="utf-8")

        # Meta for one model (startup time + model id).
        write_stamp("meta", "qwen3-0.6b", {
            "model": "Qwen/Qwen3-0.6B",
            "name": "qwen3-0.6b",
            "startup_s": 272,
            "stamp": stamp,
        })
        # pp/tg benchmark rows: p=2048 with tg=32 and tg=128 plus a p=32768 row
        # (which must not be chosen as headline when p=2048 exists).
        pptg = {
            "benchmarks": [
                {"prompt_size": 2048, "response_size": 32,
                 "pp_throughput": {"mean": 50803.05}, "tg_throughput": {"mean": 112.68}},
                {"prompt_size": 2048, "response_size": 128,
                 "pp_throughput": {"mean": 50802.0}, "tg_throughput": {"mean": 112.5}},
                {"prompt_size": 32768, "response_size": 128,
                 "pp_throughput": {"mean": 0.0}, "tg_throughput": {"mean": 60.1}},
            ]
        }
        write_stamp("bench", "qwen3-0.6b", pptg)
        # Concurrency rows at 1 and 4.
        write_stamp("bench-conc", "qwen3-0.6b", {
            "benchmarks": [
                {"concurrency": 1, "prompt_size": 2048, "response_size": 128,
                 "total_throughput": {"mean": 112.68}},
                {"concurrency": 4, "prompt_size": 2048, "response_size": 128,
                 "total_throughput": {"mean": 360.39}},
            ]
        })
        # Per-model functional suite results (drives the compact test matrix).
        write_stamp("suite-results", "qwen3-0.6b", {
            "results": {
                "deterministic": "pass",
                "streaming": "pass",
                "tool": "skipped",
                "reasoning": "skipped",
                "multimodal": "skipped",
            }
        })
        # Consolidated per-model matrix record (startup/registry/spec-decode/
        # bench/lifecycle outcomes). qwen3-0.6b has no spec-decode; startup and
        # registry are 0/1 from the driver.
        write_stamp("matrix", "qwen3-0.6b", {
            "startup": 1,
            "registry": 1,
            "functional": "pass",
            "spec_decode": "skip",
            "bench_pptg": "pass",
            "bench_conc": "pass",
            "post_health": "pass",
            "server_log": "pass",
            "clean_stop": "pass",
        })

        out = mod.render(tmpdir)
        assert "## Model verification" in out
        assert "`Qwen/Qwen3-0.6B`" in out   # org/model id from meta, not the short name
        assert "`qwen3-0.6b`" not in out     # short catalog name must not leak into rows
        assert "272" in out          # startup seconds
        assert "50803" in out        # pp tok/s at p=2048 (first row)
        assert "112.7" in out        # tg tok/s at p=2048
        assert "360" in out          # concurrency-4 aggregate
        assert "60.1" not in out     # p=32768 row must not leak in
        # Table header must stay exactly in sync with the release notes layout.
        assert "| Model | Startup (s) | PP tok/s | TG tok/s | TG tok/s @ concurrency 4 |" in out
        # Full report-style test matrix must be present and mirror the report.
        assert "### Test matrix" in out
        assert "Speculative decoding" in out
        assert "| Test | Implemented by | Kind |" in out
        assert "Model startup (health)" in out
        assert "/v1/models" in out
        assert "Structured response validity" in out
        assert "NVFP4 execution" in out
        assert "Clean shutdown" in out
        # qwen3-0.6b: spec-decode skipped, deterministic/streaming passed.
        assert "| Model startup (health) | `run-verify.sh` [3] | direct | ✓ |" in out
        assert "| Speculative decoding | `run-verify.sh` [6] probe | direct | - |" in out

    # Empty results dir -> empty string, not an exception.
    with tempfile.TemporaryDirectory() as tmpdir:
        assert mod.render(tmpdir) == ""


def main():
    tests = [
        test_verify_harness_files_exist,
        test_compose_serves_under_test_image_from_env,
        test_catalog_is_json_and_has_flags,
        test_catalog_test_keys_match_runnable_suites,
        test_override_generator_emits_model_command,
        test_override_generator_emits_chat_template_for_gemma,
        test_driver_is_ci_safe_and_in_container_benchy,
        test_driver_stages_are_stable_and_deterministic,
        test_driver_runs_functional_tests,
        test_driver_bench_workload_and_lifecycle,
        test_workflow_verify_job_wires_serve_and_bench,
        test_no_new_external_actions_introduced,
        test_gitignore_covers_generated_overrides,
        test_release_bundles_json_and_logs_but_excludes_fail,
        test_summarizer_reads_native_concurrency_level,
        test_render_verify_report_compact_output,
    ]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"All {len(tests)} verify harness contract tests passed!")


if __name__ == "__main__":
    main()
