#!/usr/bin/env python3
"""Per-model functional tests for the vllm-gb10 CI verify matrix.

Runs against a live OpenAI-compatible vLLM endpoint and asserts the behaviors
that prove the model's architecture/runtime paths work end to end. This is not
a quality benchmark; each test targets one deliverable:

  - deterministic text generation (load + tokenize + chat template + decode +
    OpenAI endpoint all working together),
  - streaming generation,
  - malformed-response / NaN-Inf detection,
  - (optional) tool calling,
  - (optional) reasoning parsing,
  - (optional) multimodal image input.

Uses only the Python standard library so it runs on the runner host (which has
python3) and inside the vllm-gb10 image without installing anything.

Every assertion failure here is a hard CI failure. The script reports every
suite result, then exits non-zero if any check failed.

Env contract:
  VLLM_BASE_URL    base URL, e.g. http://127.0.0.1:8010/v1
  VLLM_MODEL       served model id to request
  VLLM_MODEL_NAME  catalog name (for result file naming)
  VLLM_RESULT_DIR  host-resolved /results dir for JSON outputs
  VLLM_STAMP       run timestamp (from the driver)
  VLLM_RED_SQUARE  path to the red-square.png fixture (multimodal tests)
  VLLM_TESTS       comma list of extra suites to run: tool,reasoning,multimodal
  VLLM_TEMP        sampling temperature (0.0 = greedy)
  VLLM_MAX_TOKENS  max_tokens for normal completions
  VLLM_DETERMINISTIC_TOKEN  the token the deterministic test requires after
                        normalization (defaults to GB10_TEST_OK)
"""

import base64
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

BASE = os.environ.get("VLLM_BASE_URL", "").rstrip("/")
MODEL = os.environ.get("VLLM_MODEL", "")
NAME = os.environ.get("VLLM_MODEL_NAME", MODEL)
RESULT_DIR = os.environ.get("VLLM_RESULT_DIR", ".")
STAMP = os.environ.get("VLLM_STAMP", "")
RED_SQUARE = os.environ.get("VLLM_RED_SQUARE", "")
TESTS = {t.strip() for t in os.environ.get("VLLM_TESTS", "").split(",") if t.strip()}
TEMP = float(os.environ.get("VLLM_TEMP", "0.0"))
MAX_TOKENS = int(os.environ.get("VLLM_MAX_TOKENS", "4096"))
DETERMINISTIC_TOKEN = os.environ.get("VLLM_DETERMINISTIC_TOKEN", "GB10_TEST_OK")

PASSED = 0
FAILED = []
# The suite currently executing, set by main() before each suite runs. check()
# attributes a failing check to this suite so record_suite_results() can mark
# the right suite "fail" instead of string-matching labels (which never
# matched the suite names). Reset to None outside a suite.
CURRENT_SUITE = None
# Per-suite failures: suite name -> set of failed check labels.
SUITE_FAILED = {}


def result_path(label: str) -> str:
    return str(Path(RESULT_DIR) / f"{label}-{NAME}-{STAMP}.json")


def record(label: str, obj) -> None:
    Path(result_path(label)).write_text(
        json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def check(label: str, ok: bool, detail: str = "", suite: str = None) -> None:
    global PASSED
    if suite is None:
        suite = CURRENT_SUITE
    if ok:
        PASSED += 1
        print(f"  PASS {label}")
    else:
        FAILED.append((label, detail))
        if suite:
            SUITE_FAILED.setdefault(suite, set()).add(label)
        print(f"  FAIL {label}: {detail}", file=sys.stderr)


def normalize(text: str) -> str:
    """Collapse whitespace and strip known reasoning/tool wrapper tokens for a
    stable compare. Keeps letters/digits lowercase; drops punctuation and case.

    With a reasoning parser enabled vLLM already separates the thinking block
    into the ``reasoning`` field and leaves ``content`` as the clean final
    answer (see vLLM docs: features/reasoning_outputs), so by default the
    deterministic/multimodal content needs no stripping. These token removals
    are only defense-in-depth against a model that still embeds markers in
    ``content``.
    """
    for token in ("<|think|>", "<|/think|>", "<|im_start|>", "<|im_end|>",
                  "```", "<|thinking|>", "<|/thinking|>", "<|response|>"):
        text = text.replace(token, " ")
    text = " ".join(text.split())
    return "".join(ch for ch in text if ch.isalnum()).lower()


def _post(payload, *, stream=False, timeout=600):
    req = urllib.request.Request(
        f"{BASE}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
        return resp.status, None, resp
    except urllib.error.HTTPError as e:
        return e.code, None, e
    except urllib.error.URLError as e:
        return -1, f"connection error: {e.reason}", None


def chat(messages, *, stream=False, max_tokens=None, tools=None, images=None):
    payload = {
        "model": MODEL,
        "messages": messages,
        "temperature": TEMP,
        "stream": stream,
        "max_tokens": max_tokens or MAX_TOKENS,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    if images is not None:
        payload["messages"] = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": messages[-1]["content"]},
                    {"type": "image_url",
                     "image_url": {"url": f"data:image/png;base64,{images}"}},
                ],
            }
        ]
    return _post(payload, stream=stream)


def _read_body(resp, err):
    try:
        data = resp.read()
    except Exception:
        return {}, err or "unreadable body"
    try:
        return json.loads(data), err
    except Exception:
        return {"_raw": data.decode("utf-8", "replace")[:2000]}, "body not json"


def test_deterministic() -> None:
    print("  -- deterministic generation --")
    prompt = "Reply with exactly this text and nothing else\n\n" + DETERMINISTIC_TOKEN
    # Reasoning models spend part of the budget on the thinking trace. Give
    # them enough room to finish thinking AND emit the final answer, so the
    # probe asserts on real generated content instead of on an echoed trace.
    budget = 1024 if "reasoning" in TESTS else 128
    code, err, resp = chat(
        [{"role": "user", "content": prompt}], max_tokens=budget)
    if err:
        check("http 200", False, err)
        return
    body, parse_err = _read_body(resp, err)
    if parse_err:
        check("http 200 + valid json", False, f"http={code} {parse_err}")
        return
    check("http 200", code == 200, f"http={code}")
    if code != 200:
        record("fail-deterministic", body)
        return
    msg = (body.get("choices") or [{}])[0].get("message", {})
    content = msg.get("content") or ""
    reasoning = msg.get("reasoning") or msg.get("reasoning_content") or ""
    norm_content = normalize(content)
    norm_reason = normalize(reasoning)
    # The prompt embeds DETERMINISTIC_TOKEN, so a model that merely echoes its
    # reasoning trace satisfies a naive containment check without producing the
    # requested answer. Treat only the final `content` as the generated answer:
    # it must contain the token (plain models use exact equality as the strict
    # round-trip proof). For reasoning models whose documented behavior is a
    # thinking-only reply (content empty), fall back to reasoning, but only
    # when the reasoning actually carries the token beyond the prompt echo.
    if "reasoning" in TESTS:
        if norm_content:
            ok = normalize(DETERMINISTIC_TOKEN) in norm_content
            label = f"{DETERMINISTIC_TOKEN} present in content after normalize"
        else:
            echo = normalize(prompt) in norm_reason
            ok = normalize(DETERMINISTIC_TOKEN) in norm_reason and not echo
            label = ("reasoning carries {} (content empty, not a prompt echo)"
                     ).format(DETERMINISTIC_TOKEN)
            check("content empty", True, "reasoning-only reply (documented)")
    else:
        ok = norm_content == normalize(DETERMINISTIC_TOKEN)
        label = f"contains {DETERMINISTIC_TOKEN} after normalize (exact)"
    check(label, ok,
          f"normalized={norm_content!r} raw={content!r} reasoning={norm_reason!r}")
    usage = body.get("usage") or {}
    check("completion_tokens > 0", (usage.get("completion_tokens") or 0) > 0,
          f"usage={usage}")
    bad = any((isinstance(v, float) and (v != v or v in (float("inf"), float("-inf"))))
              for v in usage.values())
    check("no NaN/Inf in usage", not bad, f"usage={usage}")
    record("deterministic", body)


def test_streaming() -> None:
    print("  -- streaming generation --")
    prompt = "Count from 1 through 20, separated by spaces."
    code, err, resp = chat(
        [{"role": "user", "content": prompt}], stream=True, max_tokens=512)
    if err:
        check("http 200", False, err)
        return
    chunks = []
    text = ""
    reasoning = ""
    try:
        for raw in resp:
            line = raw.decode("utf-8", "replace").rstrip("\n")
            if not line or not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                chunks.append(data)
                break
            chunk = json.loads(data)
            chunks.append(chunk)
            delta = (chunk.get("choices") or [{}])[0].get("delta", {})
            text += delta.get("content") or ""
            # A reasoning parser streams the thinking text through
            # delta.reasoning while delta.content only carries the final
            # answer. A model that spends its token budget thinking would hand
            # back a valid stream with empty content, so accumulate the
            # reasoning field (and the legacy reasoning_content shim) too when
            # checking that the stream produced output. This matches the
            # reasoning-content contract documented in docs/model-verification.md.
            reasoning += (
                delta.get("reasoning")
                or delta.get("reasoning_content")
                or ""
            )
    except Exception as e:  # noqa: BLE001
        check("stream completes without exception", False, f"exc={e!r}")
        return
    check("http 200", code == 200, f"http={code}")
    check("more than one chunk", len(chunks) > 1, f"chunks={len(chunks)}")
    check("stream finished with [DONE]", "[DONE]" in chunks,
          f"last={chunks[-1]!r}" if chunks else "no chunks")
    check("generated content received", len((text + reasoning).strip()) > 0,
          f"len={len(text)} content_len={len(reasoning)}")
    record("streaming", {"chunks": len(chunks), "text": text, "reasoning": reasoning})


def test_tool() -> None:
    print("  -- tool calling --")
    tools = [{
        "type": "function",
        "function": {
            "name": "get_temperature",
            "description": "Get the current temperature for a city.",
            "parameters": {
                "type": "object",
                "properties": {"city": {"type": "string",
                                        "description": "City name"}},
                "required": ["city"],
            },
        },
    }]
    code, err, resp = chat(
        [{"role": "user", "content": "What's the temperature in Minneapolis?"}],
        tools=tools, max_tokens=1024)
    if err:
        check("http 200", False, err)
        return
    body, parse_err = _read_body(resp, err)
    if parse_err:
        check("http 200 + valid json", False, f"http={code} {parse_err}")
        return
    check("http 200", code == 200, f"http={code}")
    if code != 200:
        record("fail-tool", body)
        return
    msg = (body.get("choices") or [{}])[0].get("message", {})
    tcs = msg.get("tool_calls") or []
    check("parsed tool_call present",
          any(tc.get("function", {}).get("name") == "get_temperature" for tc in tcs),
          f"tool_calls={tcs}")
    args = ""
    for tc in tcs:
        if tc.get("function", {}).get("name") == "get_temperature":
            args = tc["function"].get("arguments") or ""
    city = ""
    try:
        city = json.loads(args).get("city", "")
    except Exception:
        city = ""
    check("tool arguments parse + city=Minneapolis",
          city == "Minneapolis", f"args={args!r} city={city!r}")
    record("tool", body)


def test_reasoning() -> None:
    print("  -- reasoning --")
    code, err, resp = chat(
        [{"role": "user", "content": "What is 17 * 19?"}], max_tokens=MAX_TOKENS)
    if err:
        check("http 200", False, err)
        return
    body, parse_err = _read_body(resp, err)
    if parse_err:
        check("http 200 + valid json", False, f"http={code} {parse_err}")
        return
    check("http 200", code == 200, f"http={code}")
    if code != 200:
        record("fail-reasoning", body)
        return
    msg = (body.get("choices") or [{}])[0].get("message", {})
    content = msg.get("content") or ""
    reasoning = msg.get("reasoning") or msg.get("reasoning_content") or ""
    finish = (body.get("choices") or [{}])[0].get("finish_reason")
    check("final answer produced (content non-empty)", len(content.strip()) > 0,
          f"content={content!r}")
    check("reasoning parsed", len(reasoning.strip()) > 0, f"reasoning={reasoning!r}")
    check("finish_reason present", bool(finish), f"finish={finish!r}")
    record("reasoning", body)


def test_multimodal() -> None:
    print("  -- multimodal --")
    if not RED_SQUARE or not Path(RED_SQUARE).is_file():
        check("image fixture present", False, f"missing {RED_SQUARE}")
        return
    b64 = base64.b64encode(Path(RED_SQUARE).read_bytes()).decode()
    code, err, resp = chat(
        [{"role": "user",
          "content": "What color is the square? Reply with only the color."}],
        images=b64, max_tokens=64)
    if err:
        check("http 200", False, err)
        return
    body, parse_err = _read_body(resp, err)
    if parse_err:
        check("http 200 + valid json", False, f"http={code} {parse_err}")
        return
    check("http 200", code == 200, f"http={code}")
    if code != 200:
        record("fail-multimodal", body)
        return
    msg = (body.get("choices") or [{}])[0].get("message", {})
    content = msg.get("content") or ""
    candidate = content
    # A reasoning model (e.g. gemma4) can describe the color in its reasoning
    # trace ("The square is red") with empty content. For those models require
    # the color to appear somewhere (containment) rather than equal "red"
    # exactly. For plain models content must equal "red" exactly.
    if "reasoning" in TESTS:
        candidate = candidate + " " + (msg.get("reasoning") or msg.get("reasoning_content") or "")
        ok = "red" in normalize(candidate)
        label = "color is red after normalize"
    else:
        ok = normalize(candidate) == "red"
        label = "color is red after normalize"
    check(label, ok,
          f"normalized={normalize(candidate)!r} raw={content!r} reasoning={msg.get('reasoning','')!r}")
    record("multimodal", body)


SUITES = {
    "deterministic": test_deterministic,
    "streaming": test_streaming,
    "tool": test_tool,
    "reasoning": test_reasoning,
    "multimodal": test_multimodal,
}


def record_suite_results(enabled: set, suite_failed: dict, failed: list) -> None:
    """Write one consolidated per-model suite-summary JSON.

    The renderer (and therefore the release notes) reads this file to show the
    test matrix: which suites were enabled for this model and whether each one
    passed. ``suite_failed`` maps each suite name to the set of failed check
    labels attributed to it; ``failed`` is the full (label, detail) list.

    A suite is recorded ``pass`` when it ran and recorded no failing check,
    ``fail`` when it ran and at least one check failed, and ``skipped`` when the
    catalog did not select it for this model. The always-on deterministic and
    streaming suites are included too. Each suite's failures are tracked by the
    suite the failing check ran under (see check()), not by string-matching
    check labels against suite names, which never matched.
    """
    summary = {}
    for name in ("deterministic", "streaming", "tool", "reasoning", "multimodal"):
        if name not in enabled:
            summary[name] = "skipped"
        elif suite_failed.get(name):
            summary[name] = "fail"
        else:
            summary[name] = "pass"
    record("suite-results", {
        "model": MODEL,
        "name": NAME,
        "stamp": STAMP,
        "enabled": sorted(enabled),
        "results": summary,
    })


def main() -> int:
    global CURRENT_SUITE
    if not BASE or not MODEL:
        print("ERROR: VLLM_BASE_URL and VLLM_MODEL are required", file=sys.stderr)
        return 2
    print(f"model: {NAME} ({MODEL})")
    print(f"tests: deterministic, streaming (+ {sorted(TESTS) or 'none'})")

    enabled = {"deterministic", "streaming"} | TESTS
    for name in ("deterministic", "streaming", "tool", "reasoning", "multimodal"):
        if name in enabled and name in SUITES:
            CURRENT_SUITE = name
            SUITES[name]()
    CURRENT_SUITE = None

    record_suite_results(enabled, SUITE_FAILED, FAILED)

    print(f"\n{len(FAILED)} failures, {PASSED} passes")
    if FAILED:
        for label, detail in FAILED:
            print(f"  FAILED {label}: {detail}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
