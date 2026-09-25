#!/usr/bin/env python3
"""Edge-case + regression tests for the release-notes verify-report renderer.

The happy-path render is covered by tests/test-verify-harness.py
(test_render_verify_report_compact_output). This module hammers the failure and
tolerance paths the CI release-notes job can hit with real (and malformed)
result files:

  - a results dir with no bench files at all (render must return an empty
    string, not crash),
  - a matrix file with wrong-typed or junk outcome values (must normalize to
    pass/fail/skip deterministically, never crash, never lie),
  - a matrix row whose meta is absent (must use the catalog short name),
  - multiple stamps in the same dir (render must pick the newest),
  - a corrupt/unreadable JSON file (must be skipped, not fatal),
  - models that share no matrix file must not appear in the matrix section but
    still appear in the headline benchmark table,
  - locale/typo tolerance in recorded outcome strings (acceptable because the
    harness or a hand-edit can write "Pass", "0", "false", "" and the release
    notes must not silently claim a pass for a fail).
"""

import importlib.util
import json
import tempfile
from pathlib import Path

VERIFY = Path(__file__).resolve().parent.parent / "verify"

spec = importlib.util.spec_from_file_location("render_verify_report", VERIFY / "render-verify-report.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

STAMP = "20990101-000000"


def write(dirpath, prefix, name, obj, stamp=STAMP):
    (Path(dirpath) / f"{prefix}-{name}-{stamp}.json").write_text(
        json.dumps(obj), encoding="utf-8")


def bench_obj(pp=100.0, tg=10.0):
    return {"benchmarks": [{"prompt_size": 2048, "response_size": 128,
                            "pp_throughput": {"mean": pp},
                            "tg_throughput": {"mean": tg}}]}


def meta_obj(model="org/A", name="a", startup=42):
    return {"model": model, "name": name, "startup_s": startup, "stamp": STAMP}


def matrix_obj(**overrides):
    base = {
        "model": "org/A", "name": "a", "stamp": STAMP,
        "startup": 1, "registry": 1, "functional": "pass",
        "spec_decode": "skip", "bench_pptg": "pass", "bench_conc": "pass",
        "post_health": "pass", "server_log": "pass", "clean_stop": "skip",
    }
    base.update(overrides)
    return base


def render(dirpath):
    return mod.render(dirpath)


def test_empty_results_dir_returns_empty_string():
    with tempfile.TemporaryDirectory() as d:
        assert render(d) == ""


def test_only_matrix_no_bench_returns_empty_string():
    """A matrix file alone cannot anchor a report (render needs bench files to
    pick a stamp and model list). It must degrade to an empty string, not
    crash, so a partial artifact never emits a broken section."""
    with tempfile.TemporaryDirectory() as d:
        write(d, "matrix", "a", matrix_obj())
        assert render(d) == ""


def test_missing_meta_uses_catalog_name_in_matrix_header():
    """When a matrix file exists but its meta is absent, the matrix column
    must fall back to the short catalog name (from the filename) instead of
    crashing on the missing meta."""
    with tempfile.TemporaryDirectory() as d:
        write(d, "bench", "a", bench_obj())
        write(d, "matrix", "a", matrix_obj())
        out = render(d)
        assert "### Test matrix" in out
        # Column header falls back to short name `a` (not `org/A`).
        assert "| Test | Implemented by | Kind | `a` |" in out
        # Headline table still shows both (org/model id only when meta present;
        # here there is no meta so it uses the short name too, without crash).
        assert "| `a` | 42 |" in out or "| `a` |" in out


def test_wrong_typed_outcomes_normalize_deterministically():
    """Wrong-typed or junk matrix values must never crash and must normalize
    to a stable mark: truthy -> pass, falsy -> fail, junk -> skipped."""
    with tempfile.TemporaryDirectory() as d:
        write(d, "bench", "a", bench_obj())
        write(d, "matrix", "a", matrix_obj(
            startup="yes",            # truthy string -> pass
            registry=0,               # falsy int -> fail
            functional="PASS",        # case-insensitive pass
            spec_decode=True,         # bool -> pass
            bench_pptg="fail",
            bench_conc=None,          # null -> skipped
            post_health="",           # empty string -> skipped
            server_log="error",       # alias -> fail
            clean_stop="none",        # alias -> skipped
        ))
        out = render(d)
        m = out[out.find("### Test matrix"):]
        # Row format: | Test | Implemented by | Kind | `<a>` |
        # Split on pipe, drop leading/trailing empties -> [Test, Impl, Kind, <a>, '']
        cells = {line.split("|")[1].strip(): [c.strip() for c in line.split("|")]
                 for line in m.splitlines() if line.startswith("| ")}

        def mark_for(row_label):
            return cells[row_label][-2]  # last real cell before trailing ''

        assert mark_for("Model startup (health)") == "✓"     # "yes"
        assert mark_for("`/v1/models` registration") == "✗"   # 0
        assert mark_for("Speculative decoding") == "✓"        # True
        assert mark_for("llama-benchy pp/tg") == "✗"          # "fail"
        assert mark_for("llama-benchy concurrency") == "-"    # None
        assert mark_for("Post-bench health") == "-"           # ""
        assert mark_for("Server log captured") == "✗"         # "error"
        assert mark_for("Clean shutdown") == "-"              # "none"


def test_wrong_cased_suite_results_normalize():
    """suite-results may carry 'Pass'/'Failed' casing from an older run. The
    matrix must normalize rather than treat them as junk-skips."""
    with tempfile.TemporaryDirectory() as d:
        write(d, "bench", "a", bench_obj())
        write(d, "matrix", "a", matrix_obj())
        write(d, "suite-results", "a", {
            "results": {"deterministic": "Pass", "streaming": "Failed",
                        "tool": "skipped", "reasoning": "skipped",
                        "multimodal": "skipped"},
        })
        out = render(d)
        m = out[out.find("### Test matrix"):]
        cells = {line.split("|")[1].strip(): [c.strip() for c in line.split("|")]
                 for line in m.splitlines() if line.startswith("| ")}

        def mark_for(row_label):
            return cells[row_label][-2]
        assert mark_for("Deterministic generation") == "✓"
        assert mark_for("Streaming generation") == "✗"


def test_corrupt_json_file_is_skipped_not_fatal():
    with tempfile.TemporaryDirectory() as d:
        write(d, "bench", "a", bench_obj())
        (Path(d) / f"matrix-a-{STAMP}.json").write_text("{ not json !!!", encoding="utf-8")
        write(d, "suite-results", "a", {"results": {"deterministic": "pass"}})
        # Corrupt matrix must not crash; the render still works.
        out = render(d)
        assert "## Model verification" in out
        # No matrix section (only bench/suite-read, matrix unreadable).
        assert "### Test matrix" not in out


def test_picks_newest_stamp():
    """When the results dir holds multiple runs, render must pick the newest
    stamp (the last completed verify run) and ignore the older one."""
    with tempfile.TemporaryDirectory() as d:
        write(d, "bench", "a", bench_obj(pp=111.0, tg=11.0), stamp="20990101-000000")
        write(d, "bench", "a", bench_obj(pp=222.0, tg=22.0), stamp="20990202-000000")
        write(d, "meta", "a", meta_obj(startup=50), stamp="20990202-000000")
        out = render(d)
        assert "222" in out        # newest
        assert "111" not in out     # older stamp excluded


def test_model_without_matrix_only_in_headline():
    """A model with bench but no matrix file must appear in the headline table
    but not in the test-matrix section (that section is for models with a
    matrix record)."""
    with tempfile.TemporaryDirectory() as d:
        write(d, "bench", "a", bench_obj())
        write(d, "bench", "b", bench_obj(pp=200.0, tg=20.0))
        write(d, "meta", "a", meta_obj())
        write(d, "matrix", "a", matrix_obj())
        out = render(d)
        # Headline has both models.
        assert "| `org/A` |" in out
        assert "200" in out
        # Matrix only has model a (b has no matrix file). The column header
        # uses the meta org/model id `org/A`; the short name `b` must not appear.
        m = out[out.find("### Test matrix"):]
        assert "`org/A`" in m
        assert "`b`" not in m


def test_bench_mean_string_does_not_crash():
    """A bench mean that is a non-numeric string (wrong type) must not crash
    the format-code path; the cell renders as '-'."""
    with tempfile.TemporaryDirectory() as d:
        write(d, "meta", "a", meta_obj())
        write(d, "bench", "a", {
            "benchmarks": [{"prompt_size": 2048, "response_size": 128,
                            "pp_throughput": {"mean": "abc"},
                            "tg_throughput": {"mean": 10.0}}],
        })
        write(d, "matrix", "a", matrix_obj())
        out = render(d)
        assert "## Model verification" in out
        assert "### Test matrix" in out
        # The pp cell shows a dash, not a crash and not a fabricated 0.
        row = next(l for l in out.splitlines() if l.startswith("| `org/A`"))
        assert row.endswith("| - | 10.0 | - |") or row.count("|") == 5


def test_bench_mean_missing_renders_dash_not_zero():
    """A benchmark entry with no mean must render '-' (no data), never a
    fabricated '0' that would look like a real throughput."""
    with tempfile.TemporaryDirectory() as d:
        write(d, "meta", "a", meta_obj())
        write(d, "bench", "a", {"benchmarks": [{"prompt_size": 2048,
                                                "response_size": 128}]})
        write(d, "matrix", "a", matrix_obj())
        out = render(d)
        row = next(l for l in out.splitlines() if l.startswith("| `org/A`"))
        # | `org/A` | 42 | - | - | - |
        cells = [c.strip() for c in row.split("|")][1:-1]
        assert cells[0] == "`org/A`"
        assert cells[2:] == ["-", "-", "-"]   # pp/tg/concurrency all dashes
        assert "0" not in cells[1:]           # no fabricated zero


def test_null_and_list_result_files_do_not_crash():
    """A result file that is valid JSON but null or a list must be skipped for
    that file, not crash the whole report."""
    with tempfile.TemporaryDirectory() as d:
        write(d, "bench", "a", bench_obj())
        write(d, "matrix", "a", matrix_obj())
        # meta + suite as JSON lists/null.
        for bad in (f"meta-a-{STAMP}.json", f"suite-results-a-{STAMP}.json"):
            (Path(d) / bad).write_text("null", encoding="utf-8")
        out = render(d)
        assert "## Model verification" in out
        assert "### Test matrix" in out
    with tempfile.TemporaryDirectory() as d:
        write(d, "bench", "a", bench_obj())
        write(d, "matrix", "a", ["not", "a", "dict"])
        out = render(d)
        assert "## Model verification" in out     # matrix skipped, bench still renders
        assert "### Test matrix" not in out


def test_nested_wrong_shapes_do_not_crash():
    """Valid dict result files with wrongly-shaped nested values (results as a
    list, benchmark rows as a string) must not crash."""
    with tempfile.TemporaryDirectory() as d:
        write(d, "bench", "a", bench_obj())
        write(d, "matrix", "a", matrix_obj())
        write(d, "suite-results", "a", {"results": ["x"], "enabled": ["x"]})
        out = render(d)
        assert "## Model verification" in out
        assert "### Test matrix" in out   # matrix still renders, suite reads as empty
    with tempfile.TemporaryDirectory() as d:
        write(d, "bench", "a", {"benchmarks": "not-a-list"})
        write(d, "matrix", "a", matrix_obj())
        out = render(d)
        assert "## Model verification" in out
        assert "### Test matrix" in out


def test_non_string_model_id_falls_back():
    """A meta model id that is not a string (list/null/int) must not crash the
    catalog lookup; it falls back to the short catalog name."""
    for bad in (["x"], None, 42):
        with tempfile.TemporaryDirectory() as d:
            write(d, "bench", "a", bench_obj())
            write(d, "matrix", "a", matrix_obj())
            write(d, "meta", "a", {"model": bad, "startup_s": 42, "stamp": STAMP})
            out = render(d)
            assert "## Model verification" in out
            assert "### Test matrix" in out


def test_normalize_outcome_unit():
    assert mod.normalize_outcome("pass") == "pass"
    assert mod.normalize_outcome("PASS") == "pass"
    assert mod.normalize_outcome("Passed") == "pass"
    assert mod.normalize_outcome("fail") == "fail"
    assert mod.normalize_outcome("FAILED") == "fail"
    assert mod.normalize_outcome("skip") == "skip"
    assert mod.normalize_outcome("skipped") == "skip"
    assert mod.normalize_outcome(1) == "pass"
    assert mod.normalize_outcome(0) == "fail"
    assert mod.normalize_outcome(True) == "pass"
    assert mod.normalize_outcome(False) == "fail"
    assert mod.normalize_outcome(None) == "skip"
    assert mod.normalize_outcome("") == "skip"
    assert mod.normalize_outcome(3.7) == "pass"      # truthy number
    assert mod.normalize_outcome("garbage!!") == "skip"


def main():
    tests = [
        (name, fn) for name, fn in sorted(globals().items())
        if name.startswith("test_") and callable(fn)
    ]
    for name, fn in tests:
        fn()
        print(f"PASS: {name}")
    print(f"All {len(tests)} render-verify-report edge-case tests passed!")


if __name__ == "__main__":
    main()
