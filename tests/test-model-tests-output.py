#!/usr/bin/env python3
"""Unit tests for the per-model functional test suite output in verify/model-tests.py.

These cover the "model testing output" the release-notes matrix and the
verify harness consume:

  - normalize(): the stable-compare function every content assertion relies on,
  - check(): pass/fail accounting, per-suite attribution of failures,
  - record_suite_results(): the consolidated suite-results-*.json that feeds the
    matrix (an enabled suite that recorded any failing check must be "fail",
    not "pass"; disabled suites must be "skipped").

No network is needed: all functions under test operate on pure data or the
module's own in-memory counters. The HTTP test functions are deliberately not
imported/called (they would need a live endpoint).
"""

import importlib.util
import json
import tempfile
import os
from pathlib import Path

VERIFY = Path(__file__).resolve().parent.parent / "verify"

spec = importlib.util.spec_from_file_location("model_tests", VERIFY / "model-tests.py")
mt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mt)


def reset_state():
    mt.PASSED = 0
    mt.FAILED = []
    mt.CURRENT_SUITE = None
    mt.SUITE_FAILED = {}


def test_normalize_collapses_and_strips():
    assert mt.normalize("GB10_TEST_OK") == "gb10testok"
    assert mt.normalize("  GB10  TEST_OK  ") == "gb10testok"
    assert mt.normalize("gB10_tEsT_oK") == "gb10testok"
    # reasoning/tool wrapper tokens are removed before comparing.
    assert mt.normalize("<|think|>GB10_TEST_OK<|/think|>") == "gb10testok"
    assert mt.normalize("<|im_start|>GB10_TEST_OK<|im_end|>") == "gb10testok"
    # punctuation dropped; alnum kept lowercase.
    assert mt.normalize("GB10.TEST-OK") == "gb10testok"
    assert mt.normalize("```gb10testok```") == "gb10testok"
    # multispace collapsed.
    assert mt.normalize("gb10\t\ttest\nok") == "gb10testok"


def test_normalize_keeps_unrelated_text_distinct():
    """A model that echoes the prompt must not false-positive on a sub-string
    of some other token (the compare must be on the whole normalized token)."""
    assert mt.normalize("gB10_TEST_O") != "gb10testok"
    assert mt.normalize("gb10testnotok") != "gb10testok"


def _run_deterministic(content, reasoning="", reasoning_suite=False):
    """Run mt.test_deterministic against a canned message and return FAILED."""
    reset_state()
    body = {
        "choices": [{"message": {"content": content, "reasoning": reasoning}}],
        "usage": {"completion_tokens": 3},
    }
    mt.chat = lambda *a, **k: (200, None, _FakeResp(body))
    mt.TESTS = {"streaming"} | ({"reasoning"} if reasoning_suite else set())
    with tempfile.TemporaryDirectory() as d:
        mt.RESULT_DIR = d
        mt.NAME = "qwen3-0.6b"
        mt.STAMP = "20990101-000000"
        mt.CURRENT_SUITE = "deterministic"
        mt.test_deterministic()
        mt.CURRENT_SUITE = None
    return list(mt.FAILED)


class _FakeResp:
    """Minimal file-like wrapper so _read_body can read() the canned body."""

    def __init__(self, body):
        self._json = json.dumps(body).encode()

    def read(self, *a, **k):
        return self._json


def test_deterministic_rejects_reasoning_prompt_echo():
    """The prompt-echo loophole must be closed: a reasoning model that spends
    the whole budget in `reasoning` echoing the prompt (content empty) must
    FAIL the deterministic check, not pass it by containing the token."""
    failed = _run_deterministic("", reasoning="Reply with exactly this text and nothing else\n\nGB10_TEST_OK",
                                reasoning_suite=True)
    assert any("not a prompt echo" in label for label, _ in failed), (
        f"expected reasoning-echo failure, got failures={failed}"
    )


def test_deterministic_passes_content_answer_for_reasoning():
    """A reasoning model that emits the token in final `content` must pass,
    even when a reasoning trace is also present."""
    failed = _run_deterministic("GB10_TEST_OK", reasoning="Let me think about this.",
                                reasoning_suite=True)
    assert failed == [], f"expected pass, got failures={failed}"


def test_deterministic_rejects_prompt_echo_in_content_for_plain():
    """A plain model cannot pass by echoing the prompt verbatim in content."""
    failed = _run_deterministic("Reply with exactly this text and nothing else\n\nGB10_TEST_OK",
                                reasoning_suite=False)
    assert failed, "plain model prompt echo must fail"
    assert any("exact" in label for label, _ in failed)


def test_deterministic_rejects_wrong_token_in_content_for_plain():
    """A plain model returning some other text must fail the exact match."""
    failed = _run_deterministic("I cannot comply", reasoning_suite=False)
    assert failed, "plain model wrong output must fail"


def test_check_counts_pass_and_fail():
    reset_state()
    mt.check("a", True)
    mt.check("b", False, "detail")
    mt.check("c", False)
    assert mt.PASSED == 1
    assert len(mt.FAILED) == 2
    assert mt.FAILED[0] == ("b", "detail")


def test_check_attributes_failure_to_current_suite():
    reset_state()
    mt.CURRENT_SUITE = "deterministic"
    mt.check("http 200", True)
    mt.check("no NaN/Inf in usage", False, "usage={x: nan}")
    mt.CURRENT_SUITE = "tool"
    mt.check("parsed tool_call present", False)
    mt.CURRENT_SUITE = None
    mt.check("orphan label failure", False)   # outside a suite -> not attributed
    assert mt.SUITE_FAILED.get("deterministic") == {"no NaN/Inf in usage"}
    assert mt.SUITE_FAILED.get("tool") == {"parsed tool_call present"}
    assert "orphan label failure" not in {l for s in mt.SUITE_FAILED.values() for l in s}


def test_record_suite_results_fail_for_failed_enabled_suite():
    """The core regression: a suite that ran and failed any check must be
    recorded 'fail', not 'pass'. The old implementation compared check labels
    against suite names (which never matched), so a failing deterministic run
    was wrongly recorded as 'pass'."""
    reset_state()
    with tempfile.TemporaryDirectory() as d:
        mt.RESULT_DIR = d
        mt.NAME = "qwen3-0.6b"
        mt.STAMP = "20990101-000000"
        mt.CURRENT_SUITE = "deterministic"
        mt.check("normalize token", True)
        mt.check("no NaN/Inf in usage", False)   # fails -> deterministic must be fail
        mt.CURRENT_SUITE = "tool"
        mt.check("parsed tool_call present", True)  # passes
        mt.CURRENT_SUITE = None
        mt.record_suite_results(
            {"deterministic", "streaming", "tool"},
            mt.SUITE_FAILED, mt.FAILED,
        )
        data = json.loads(
            (Path(d) / f"suite-results-{mt.NAME}-{mt.STAMP}.json").read_text()
        )
        assert data["results"]["deterministic"] == "fail"
        assert data["results"]["tool"] == "pass"
        assert data["results"]["streaming"] == "pass"     # enabled + no failures
        assert data["results"]["reasoning"] == "skipped"  # not enabled
        assert data["results"]["multimodal"] == "skipped"
        assert data["enabled"] == ["deterministic", "streaming", "tool"]
        assert data["stamp"] == mt.STAMP


def test_record_suite_results_skipped_for_disabled():
    reset_state()
    with tempfile.TemporaryDirectory() as d:
        mt.RESULT_DIR = d
        mt.NAME = "gemma-4-12b"
        mt.STAMP = "20990102-000000"
        mt.record_suite_results({"deterministic", "streaming"}, {}, [])
        data = json.loads(
            (Path(d) / f"suite-results-{mt.NAME}-{mt.STAMP}.json").read_text()
        )
        assert data["results"]["deterministic"] == "pass"
        assert data["results"]["tool"] == "skipped"
        assert data["results"]["reasoning"] == "skipped"
        assert data["results"]["multimodal"] == "skipped"


def test_record_suite_results_handles_empty_run():
    reset_state()
    with tempfile.TemporaryDirectory() as d:
        mt.RESULT_DIR = d
        mt.NAME = "x"
        mt.STAMP = "20990103-000000"
        mt.record_suite_results(set(), {}, [])
        data = json.loads(
            (Path(d) / f"suite-results-{mt.NAME}-{mt.STAMP}.json").read_text()
        )
        assert set(data["results"].values()) == {"skipped"}


def main():
    tests = [
        (name, fn) for name, fn in sorted(globals().items())
        if name.startswith("test_") and callable(fn)
    ]
    for name, fn in tests:
        fn()
        print(f"PASS: {name}")
    print(f"All {len(tests)} model-tests output unit tests passed!")


if __name__ == "__main__":
    main()
