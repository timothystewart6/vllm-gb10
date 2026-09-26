#!/usr/bin/env python3
"""Aggregate the vllm-gb10 CI verify multi-model results into one summary.

Reads every bench-*.json (pp/tg matrix), bench-conc-*.json (concurrency), and
meta-*.json (startup + post-registration memory) under the results dir, derives the model
name from each filename (bench-<model>-<stamp>.json,
bench-conc-<model>-<stamp>.json, meta-<model>-<stamp>.json), and writes:

  verify/results/summary-<stamp>.json    combined machine-readable summary
  verify/results/summary-<stamp>.md      human-readable report

The CI "Upload serve and bench results" step uploads verify/results/, so the
combined summary rides along with the raw per-model JSON. This script is run by
verify/run-verify.sh after the model loop drops the per-model files.

The pp/tg and concurrency counts are informational reference points, not a hard
CI gate (enough runs are needed first to know per-model variance before a
regression gate is meaningful).

Usage:
  python3 verify/summarize-bench.py [--markdown] <results-dir>
"""

import argparse
import json
import sys
from pathlib import Path

MIN_MARKDOWN = object()  # sentinel for "no value"


def model_from_stem(stem, prefix):
    """Recover the model name from a result filename.

    Files are <prefix>-<model>-<stamp>.json where stamp is %Y%m%d-%H%M%S
    (e.g. 20260101-000000). The model catalog names themselves contain dashes
    (nemotron-lightning, qwen3-0.6b), so strip the fixed-length stamp suffix
    first, then the prefix, leaving the model name.
    """
    import re
    # "<prefix>-<model>-<8digits>-<6digits>" - drop the last two components.
    stripped = re.sub(r"-\d{8}-\d{6}$", "", stem)
    return stripped[len(prefix):] if stripped.startswith(prefix) else stripped


def bench_rows_for_document(data, model_name):
    """Flatten a llama-benchy benchmark JSON into comparable rows for the pp/tg
    matrix. Model name comes from the filename, not the doc."""
    rows = []
    latency_ms = data.get("latency_ms")
    for b in data.get("benchmarks", []):
        pp = b.get("prompt_size")
        tg = b.get("response_size")
        rows.append(
            {
                "kind": "pptg",
                "model": model_name,
                "pp": pp,
                "tg": tg,
                "concurrency": 1,
                "latency_ms": round(latency_ms, 2) if latency_ms is not None else None,
                "pp_tok_s": round((b.get("pp_throughput") or {}).get("mean", 0), 1),
                "tg_tok_s": round((b.get("tg_throughput") or {}).get("mean", 0), 1),
                "ttfr_ms": round((b.get("ttfr") or {}).get("mean", 0), 1)
                if "ttfr" in b else None,
            }
        )
    return rows


def conc_rows_for_document(data, model_name):
    """Flatten the concurrency bench JSON. Each benchmark entry carries its
    concurrency level in a native `concurrency` field (preferred) or in the
    shape string (e.g. tg128 (c4)), so recover it and expose the aggregate
    (total) throughput for comparison."""
    rows = []
    for b in data.get("benchmarks", []):
        conc = b.get("concurrency")
        if conc is None:
            s = b.get("shape") or b.get("name") or ""
            import re
            m = re.search(r"\(c(\d+)\)", s)
            conc = int(m.group(1)) if m else 1
        pp = b.get("prompt_size")
        tg = b.get("response_size")
        # For concurrency > 1 the total throughput is the aggregate across all
        # clients (t/s (total)); the per-request number is t/s (req).
        agg = (b.get("total_throughput") or b.get("tg_throughput") or {}).get("mean", 0)
        per_req = (b.get("req_throughput") or b.get("tg_req_throughput") or {}).get("mean", 0)
        rows.append(
            {
                "kind": "concurrency",
                "model": model_name,
                "pp": pp,
                "tg": tg,
                "concurrency": conc,
                "latency_ms": None,
                "pp_tok_s": (b.get("pp_throughput") or {}).get("mean", 0),
                "tg_tok_s": agg or (b.get("tg_throughput") or {}).get("mean", 0),
                "tg_req_tok_s": per_req,
                "ttfr_ms": (b.get("ttfr") or {}).get("mean", 0)
                if "ttfr" in b else None,
            }
        )
    return rows


def meta_for_model(meta_path, model_name):
    """Pull startup + post-registration memory from a meta-<model>.json file."""
    try:
        data = json.loads(meta_path.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    return {
        "model": model_name,
        "startup_s": data.get("startup_s"),
        "startup_mem_mib": data.get("startup_mem_mib"),
        "image": data.get("image"),
        "revision": data.get("revision"),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("results_dir", nargs="?", default="verify/results")
    ap.add_argument("--markdown", action="store_true", help="emit markdown instead of JSON")
    args = ap.parse_args()

    results_dir = Path(args.results_dir)
    pptg_files = sorted(results_dir.glob("bench-*.json"))
    conc_files = sorted(results_dir.glob("bench-conc-*.json"))
    # bench-*.json also matches bench-conc-*.json, so exclude the latter.
    pptg_files = [p for p in pptg_files if "-conc-" not in p.name]
    meta_files = sorted(results_dir.glob("meta-*.json"))

    if not (pptg_files or conc_files or meta_files):
        print(f"error: no bench/meta json under {results_dir}", file=sys.stderr)
        return 2

    models = {}
    all_pptg = []
    all_conc = []

    for path in pptg_files:
        model_name = model_from_stem(path.stem, "bench-")
        models.setdefault(model_name, {})
        try:
            data = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError) as e:
            print(f"error: cannot read {path}: {e}", file=sys.stderr)
            return 2
        all_pptg.extend(bench_rows_for_document(data, model_name))

    for path in conc_files:
        model_name = model_from_stem(path.stem, "bench-conc-")
        models.setdefault(model_name, {})
        try:
            data = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError) as e:
            print(f"error: cannot read {path}: {e}", file=sys.stderr)
            return 2
        all_conc.extend(conc_rows_for_document(data, model_name))

    metas = {}
    for path in meta_files:
        model_name = model_from_stem(path.stem, "meta-")
        meta = meta_for_model(path, model_name)
        if meta is not None:
            metas[model_name] = meta

    combined = {
        "models": {
            m: {
                "pptg": [r for r in all_pptg if r["model"] == m],
                "concurrency": [r for r in all_conc if r["model"] == m],
            }
            for m in models
        },
        "meta": metas,
    }

    if args.markdown:
        _md(combined, models, metas)
        return 0

    print(json.dumps(combined, indent=2))


def _md(combined, models, metas):
    print("# serve + bench summary")
    print()
    print("## model meta")
    print()
    print("| model | startup s | startup mem MiB | image |")
    print("|---|---|---|---|")
    for m in sorted(models):
        meta = metas.get(m) or {}
        print(
            f"| {m} | {meta.get('startup_s', '-')} | "
            f"{meta.get('startup_mem_mib', '-')} | {meta.get('image', '-')} |"
        )
    print()
    print("## prompt processing / generation (pp/tg)")
    print()
    print("| model | pp | tg | latency ms | pp tok/s | tg tok/s | ttfr ms |")
    print("|---|---|---|---|---|---|---|")
    for m in sorted(models):
        for r in combined["models"][m]["pptg"]:
            latency = f"{r['latency_ms']:.2f}" if r.get("latency_ms") is not None else "-"
            ttfr = f"{r['ttfr_ms']:.1f}" if r.get("ttfr_ms") is not None else "-"
            print(
                f"| {r['model']} | {r['pp']} | {r['tg']} | {latency} | "
                f"{r['pp_tok_s']:.1f} | {r['tg_tok_s']:.1f} | {ttfr} |"
            )
    print()
    print("## concurrency (pp/tg aggregate)")
    print()
    print("| model | pp | tg | concurrency | tg tok/s (total) | tg tok/s (req) |")
    print("|---|---|---|---|---|---|")
    for m in sorted(models):
        for r in combined["models"][m]["concurrency"]:
            print(
                f"| {r['model']} | {r['pp']} | {r['tg']} | {r['concurrency']} | "
                f"{r['tg_tok_s']:.1f} | {r['tg_req_tok_s']:.1f} |"
            )


if __name__ == "__main__":
    raise SystemExit(main())
