"""LLM backend: shells out to `claude -p` for extractor + judge.

Two backends:
  - 'claude': real `claude -p --output-format json` subprocess. Parses .result
    field and tries to extract a JSON object. Slow + costs tokens.
  - 'mock'  : deterministic stub for tests. Selected when EXP_LLM=mock.

We keep the interface tiny: prompt -> dict. The extractor and judge each pass
their own prompt template.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from typing import Any

DEFAULT_MODEL = os.getenv("EXP_LLM_MODEL", "claude-haiku-4-5-20251001")
BACKEND = os.getenv("EXP_LLM", "claude")
TIMEOUT_SEC = int(os.getenv("EXP_LLM_TIMEOUT", "120"))


def call(prompt: str, *, system: str | None = None, model: str | None = None) -> str:
    """Returns raw text output from the LLM."""
    if BACKEND == "mock":
        return _mock(prompt)
    return _claude_cli(prompt, system=system, model=model or DEFAULT_MODEL)


def call_json(prompt: str, *, system: str | None = None, model: str | None = None) -> dict[str, Any]:
    """Calls the LLM and parses a JSON object out of the response."""
    raw = call(prompt, system=system, model=model)
    obj = _extract_json(raw)
    if obj is None:
        raise ValueError(f"could not parse JSON from LLM output: {raw[:500]!r}")
    return obj


def _claude_cli(prompt: str, *, system: str | None, model: str) -> str:
    args = ["claude", "-p", "--output-format", "json", "--model", model]
    if system:
        args += ["--append-system-prompt", system]
    proc = subprocess.run(
        args,
        input=prompt,
        capture_output=True,
        text=True,
        timeout=TIMEOUT_SEC,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"claude cli failed: {proc.stderr[:500]}")
    try:
        envelope = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"claude cli returned non-json: {proc.stdout[:500]}") from e
    if envelope.get("is_error"):
        raise RuntimeError(f"claude cli reported error: {envelope}")
    return envelope.get("result", "")


def _extract_json(text: str) -> dict[str, Any] | None:
    """Find the first {...} block and parse it. Tolerant of code fences."""
    # Strip ``` fences if present.
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    candidate = fence.group(1) if fence else text
    # Greedy match outermost braces.
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    blob = candidate[start : end + 1]
    try:
        return json.loads(blob)
    except json.JSONDecodeError:
        return None


def _mock(prompt: str) -> str:
    """Deterministic stub. Routed by keywords in the prompt."""
    if "business sensitivity" in prompt or "Categories you may use" in prompt:
        # Layer 3 sanitizer probe. Mock returns "not sensitive" by default.
        # Tests asserting human_review status do so via Layer 1 findings,
        # not by depending on the LLM.
        return json.dumps(
            {
                "is_sensitive": False,
                "categories": ["none"],
                "rationale": "mock layer3 judge",
            }
        )
    if "5-dimensional reward" in prompt or "判分" in prompt:
        return json.dumps(
            {
                "outcome": 0.7,
                "intent": 0.6,
                "execution": 0.5,
                "orchestration": 0.4,
                "expression": 0.6,
                "confidence": 0.8,
                "rationale": "mock judge",
            }
        )
    return json.dumps(
        {
            "intent_text": "mock intent for trajectory",
            "preconditions": "none",
            "script_steps": [
                {"step": "read input", "why": "understand task", "how": "scan turns"},
                {"step": "execute", "why": "deliver result", "how": "tool calls"},
            ],
            "tool_capabilities": ["text_processing"],
            "key_decisions": [],
            "pitfalls": [],
            "summary": "mock extraction",
            "confidence": 0.85,
        }
    )
