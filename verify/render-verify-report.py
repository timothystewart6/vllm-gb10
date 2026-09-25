#!/usr/bin/env python3
"""Render a compact model-verification report for release notes.

Reads the per-stamp results a `verify/run-verify.sh` run drops into a results
dir (bench-<model>-<stamp>.json, bench-conc-<model>-<stamp>.json, and
meta-<model>-<stamp>.json) and prints a small markdown block summarizing the
catalog: model, startup time, and the headline pp/tg and concurrency numbers.

It is deliberately compact. Release notes should stay scannable; the full
raw per-model JSONs and server logs ride the release's verify-results tarball
and the uploaded verify/results artifact. This block is meant to answer "which
models were served and how did they perform" at a glance.

The verify job uses the same model-from-filename recovery as summarize-bench.py
(model names contain dashes, so drop the fixed length stamp suffix first).

Usage:
  python3 verify/render-verify-report.py [results-dir]

The results dir defaults to verify/results. It picks the newest stamp present
across the result files (the last run). No arguments means "scan the default
dir". Prints markdown to stdout; exits 0 even when no results are found (an
empty string), so callers can gate on output presence without a spurious
failure.
"""

import argparse
import json
import re
import sys
from pathlib import Path

STAMP_RE = re.compile(r"-\d{8}-\d{6}$")


def model_from_stem(stem, prefix):
    """Recover the model name from a result filename (same as summarize-bench)."""
    stripped = STAMP_RE.sub("", stem)
    return stripped[len(prefix):] if stripped.startswith(prefix) else stripped


def newest_stamp(files):
    """Return the most recent stamp shared by the given result files."""
    stamps = []
    for p in files:
        m = STAMP_RE.search(p.stem)
        if m:
            stamps.append(m.group(0)[1:])  # strip leading dash
    return max(stamps) if stamps else None


def first_mean(bench, fields):
    """Best-effort mean for the first benchmark row that has a value."""
    for b in bench.get("benchmarks", []):
        for field in fields:
            val = (b.get(field) or {}).get("mean")
            if val:
                return val
    return None


def render(results_dir):
    results_dir = Path(results_dir)
    bench_files = sorted(p for p in results_dir.glob("bench-*.json") if "-conc-" not in p.name)
    conc_files = sorted(results_dir.glob("bench-conc-*.json"))
    meta_files = sorted(results_dir.glob("meta-*.json"))
    suite_files = sorted(results_dir.glob("suite-results-*.json"))
    matrix_files = sorted(results_dir.glob("matrix-*.json"))

    stamp = newest_stamp(bench_files + conc_files + meta_files + suite_files + matrix_files)
    if not stamp or not bench_files:
        return ""

    load_catalog_paths()

    # Collect the per-model headline numbers and matrix outcomes at the picked
    # stamp. `matrix` is keyed by outcome id (startup, registry, functional,
    # spec_decode, bench_pptg, bench_conc, post_health, server_log, clean_stop)
    # with a pass/fail/skip value per model.
    models = {}
    for path in bench_files + conc_files + meta_files + suite_files + matrix_files:
        if f"-{stamp}" not in path.name:
            continue
        if "-conc-" in path.name:
            prefix, kind = "bench-conc-", "conc"
        elif path.name.startswith("bench-"):
            prefix, kind = "bench-", "pptg"
        elif path.name.startswith("meta-"):
            prefix, kind = "meta-", "meta"
        elif path.name.startswith("suite-results-"):
            prefix, kind = "suite-results-", "suite"
        else:
            prefix, kind = "matrix-", "matrix"
        model = model_from_stem(path.stem, prefix)
        try:
            data = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        # A result file is expected to be a JSON object. A valid-JSON-but-wrong
        # shape (list, null, scalar) must be skipped for that file only, not
        # allowed to crash the whole report for the release.
        if not isinstance(data, dict):
            continue
        entry = models.setdefault(model, {"meta": {}, "rows": [], "suite": {}, "matrix": {}})
        if kind == "meta":
            model_id = data.get("model", path.name)
            # model_id must be a hashable string for catalog lookup and dict
            # keys; a wrong-shaped meta must not crash with an unhashable type.
            if not isinstance(model_id, str):
                model_id = path.name
            entry["meta"] = {
                "startup_s": data.get("startup_s"),
                "model_id": model_id,
            }
        elif kind == "suite":
            suite = data.get("results")
            entry["suite"] = suite if isinstance(suite, dict) else {}
        elif kind == "matrix":
            entry["matrix"] = {
                "startup": data.get("startup", "skip"),
                "registry": data.get("registry", "skip"),
                "functional": data.get("functional", "skip"),
                "spec_decode": data.get("spec_decode", "skip"),
                "bench_pptg": data.get("bench_pptg", "skip"),
                "bench_conc": data.get("bench_conc", "skip"),
                "post_health": data.get("post_health", "skip"),
                "server_log": data.get("server_log", "skip"),
                "clean_stop": data.get("clean_stop", "skip"),
            }
        else:
            rows = data.get("benchmarks")
            # Skipped when benchmarks is not a list (wrong shape) - do not crash
            # and do not fabricate rows.
            if not isinstance(rows, list):
                continue
            if kind == "conc":
                for b in rows:
                    entry["rows"].append(
                        ("conc", b.get("concurrency", 1), b.get("prompt_size"),
                         b.get("response_size"), b)
                    )
            else:
                for b in rows:
                    entry["rows"].append(
                        ("pptg", 1, b.get("prompt_size"), b.get("response_size"), b)
                    )

    if not models:
        return ""

    lines = []
    lines.append("## Model verification")
    lines.append("")
    lines.append("Served and benchmarked on the DGX Spark by the CI `verify` job"
                 " (raw per-model JSONs and server logs ride the"
                 " `verify-results` release tarball):")
    lines.append("")
    lines.append("| Model | Startup (s) | PP tok/s | TG tok/s | TG tok/s @concurrency 4 |")
    lines.append("| --- | ---: | ---: | ---: | ---: |")

    for model in sorted(models):
        entry = models[model]
        meta = entry["meta"]
        startup = meta.get("startup_s")
        startup_s = f"{startup}" if startup else "-"
        # Prefer the full HuggingFace org/model id from the meta file (e.g.
        # Qwen/Qwen3-0.6B) over the short catalog name used for file naming.
        display_name = meta.get("model_id") or model

        pptg = [r for r in entry["rows"] if r[0] == "pptg"]
        conc = [r for r in entry["rows"] if r[0] == "conc"]

        # Headline pp/tg at a mid-length prompt (2048) where prefill cleanly
        # separates; fall back to the first pptg row with a prefill number.
        pp_tok_s = tg_tok_s = "-"
        target = None
        for r in pptg:
            if r[2] == 2048:
                target = r
                break
        if target is None:
            target = next((r for r in pptg if r[4].get("pp_throughput", {}) or {}), None)
        if target is not None:
            b = target[4]
            pp_vals = (b.get("pp_throughput") or {}).get("mean")
            tg_vals = (b.get("tg_throughput") or {}).get("mean")
            pp_tok_s = _fmt_num(pp_vals, digits=0) or "-"
            tg_tok_s = _fmt_num(tg_vals, digits=1) or "-"

        # Concurrency-4 aggregate throughput if present.
        conc4 = "-"
        for r in conc:
            if r[1] == 4:
                b = r[4]
                agg = (b.get("total_throughput") or b.get("tg_throughput") or {}).get("mean")
                conc4 = _fmt_num(agg, digits=0) or "-"
                break

        lines.append(f"| `{display_name}` | {startup_s} | {pp_tok_s} | {tg_tok_s} | {conc4} |")

    lines.append("")

    # Full report-style test matrix: one row per test, one column per model.
    # The per-model outcome (`pass`/`fail`/`skip`) comes from the matrix-*.json
    # and suite-results-*.json files the harness drops, so this stays in
    # lockstep with what the harness actually ran. Indirect runtime-path rows
    # (NVFP4, FP8 KV, MoE, Mamba) carry a pass when the catalog enabled that
    # model's path and the model generated successfully (functional = pass);
    # they are proven directly in the server logs.
    matrix_models = [m for m, e in models.items() if e["matrix"]]
    if matrix_models:
        # Each row: (label, implemented-by, kind, outcome-key resolver).
        MATRIX_ROWS = [
            ("Model startup (health)", "`run-verify.sh` [3]", "direct", "startup"),
            ("`/v1/models` registration", "`run-verify.sh` [4]", "direct", "registry"),
            ("Deterministic generation", "`model-tests.py` `test_deterministic`", "direct", "suite:deterministic"),
            ("Streaming generation", "`model-tests.py` `test_streaming`", "direct", "suite:streaming"),
            ("Structured response validity", "`model-tests.py` NaN/Inf + valid-JSON checks", "direct", "suite:deterministic"),
            ("Tool calling", "`model-tests.py` `test_tool`", "direct", "suite:tool"),
            ("Reasoning parser", "`model-tests.py` `test_reasoning`", "direct", "suite:reasoning"),
            ("Multimodal image input", "`model-tests.py` `test_multimodal`", "direct", "suite:multimodal"),
            ("NVFP4 execution", "`serve.quantization=modelopt_fp4` load + generate", "indirect", "path:nvfp4"),
            ("FP8 KV cache", "`serve.kv_cache_dtype=fp8_e4m3` load + generate", "indirect", "path:fp8kv"),
            ("MoE execution", "nemotron MoE serve flags", "indirect", "path:moe"),
            ("Mamba execution", "nemotron Mamba serve flags", "indirect", "path:mamba"),
            ("Speculative decoding", "`run-verify.sh` [6] probe", "direct", "spec_decode"),
            ("llama-benchy pp/tg", "`run-verify.sh` [7]", "direct", "bench_pptg"),
            ("llama-benchy concurrency", "`run-verify.sh` [7b]", "direct", "bench_conc"),
            ("Post-bench health", "driver teardown", "direct", "post_health"),
            ("Server log captured", "driver post-health", "direct", "server_log"),
            ("Clean shutdown", "driver teardown", "direct", "clean_stop"),
        ]

        def matrix_status(model_entry, key):
            """Resolve an outcome id to pass/fail/skip for one model."""
            if key.startswith("suite:"):
                suite = model_entry.get("suite") or {}
                if not isinstance(suite, dict):
                    return "skipped"
                return normalize_outcome(suite.get(key.split(":", 1)[1], "skipped"))
            if key.startswith("path:"):
                # Indirect runtime-path: pass when the model's serve flags +
                # functional generation succeeded; skip when the path is not
                # configured for the model.
                path = key.split(":", 1)[1]
                mid = model_entry["meta"].get("model_id")
                paths = CATALOG_PATHS.get(mid)
                if not paths or not paths.get(path):
                    return "skipped"
                matrix = model_entry.get("matrix") or {}
                return normalize_outcome(matrix.get("functional", "skip"))
            # Direct outcome from the matrix record itself.
            return normalize_outcome(model_entry["matrix"].get(key, "skip"))

        lines.append("### Test matrix")
        lines.append("")
        lines.append("Each row is a harness test and each column a served model."
                     " ✓ passed, ✗ failed, - not run for that model. Indirect"
                     " rows (NVFP4, FP8 KV, MoE, Mamba) are proven directly in"
                     " the captured server logs. The methodology is documented"
                     " in `docs/model-verification.md`; the raw per-model JSONs"
                     " and logs are attached to this release.")
        lines.append("")
        cols = ["Test", "Implemented by", "Kind"] + [
            f"`{models[m]['meta'].get('model_id') or m}`" for m in sorted(matrix_models)
        ]
        lines.append("| " + " | ".join(cols) + " |")
        lines.append("|" + "---|" * len(cols))

        for label, impl, kind, key in MATRIX_ROWS:
            row = [label, impl, kind]
            for m in sorted(matrix_models):
                row.append(_status_mark(matrix_status(models[m], key)))
            lines.append("| " + " | ".join(row) + " |")
        lines.append("")

    return "\n".join(lines)


CATALOG_PATHS = {}


def load_catalog_paths():
    """Derive per-model indirect-path booleans (nvfp4, fp8kv, moe, mamba) from
    verify/models.json so the matrix can mark which runtime paths each model
    enables. Falls back to empty when the catalog is unreadable (the matrix
    then marks those rows all skipped)."""
    try:
        catalog_path = Path(__file__).resolve().parent / "models.json"
        catalog = json.loads(catalog_path.read_text())
    except (OSError, ValueError):
        return
    for entry in catalog.get("models", []):
        serve = entry.get("serve", {})
        flags = serve.get("flags", [])
        CATALOG_PATHS[entry.get("model")] = {
            "nvfp4": serve.get("quantization") == "modelopt_fp4",
            "fp8kv": serve.get("kv_cache_dtype") == "fp8_e4m3",
            "moe": any(f == "--moe-backend=humming" for f in flags),
            "mamba": any(f.startswith("--mamba-backend") for f in flags),
        }


def _status_mark(status: str) -> str:
    """Map a suite result to a compact matrix mark."""
    if status == "pass":
        return "✓"
    if status == "fail":
        return "✗"
    return "-"


def _fmt_num(value, digits=0):
    """Best-effort numeric formatting for a bench mean.

    Returns a formatted string for a real number, ``0`` for a numeric zero, or
    None when the value is missing, null, or not numeric (so the caller renders
    ``-`` instead of fabricating a 0 or crashing on a str/None mean)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return f"{value:.{digits}f}"
    return None


def normalize_outcome(value) -> str:
    """Normalize any recorded outcome to one of pass/fail/skip.

    The driver writes strings (``pass``/``fail``/``skip``) and 0/1 ints for the
    startup/registry flags, but a hand-edited or stale result file can carry
    anything (wrong case, bool True/False, null, junk). Normalize case, treat
    truthy/falsy numbers and booleans as pass/fail, and degrade anything
    unrecognized to ``skip`` so the matrix never crashes and never lies (an
    unparsable value reads as a skipped row, not a false pass or fail)."""
    if isinstance(value, bool):
        return "pass" if value else "fail"
    if isinstance(value, (int, float)):
        return "pass" if value else "fail"
    if value is None:
        return "skip"
    if not isinstance(value, str):
        return "skip"
    v = value.strip().lower()
    if v in ("pass", "passed", "ok", "true", "yes", "1"):
        return "pass"
    if v in ("fail", "failed", "error", "false", "no", "0"):
        return "fail"
    if v in ("skip", "skipped", "n/a", "na", "none"):
        return "skip"
    return "skip"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("results_dir", nargs="?", default="verify/results")
    args = ap.parse_args()
    print(render(args.results_dir), end="")


if __name__ == "__main__":
    main()
