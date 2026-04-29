"""Prompt templates for extractor and judge.

Two extractor prompts (A/B) for double-run stability check.
One judge prompt; called multiple times with temperature variation deferred to
the model itself (the claude CLI doesn't expose temperature).
"""

from __future__ import annotations

import json
from typing import Any

EXTRACTOR_SYSTEM = (
    "You are an experience extractor for an internal agent experience pool. "
    "Given a multi-turn agent trajectory, distill it into a model-agnostic "
    "experience card. Output STRICT JSON only, no prose."
)

EXTRACTOR_PROMPT_A = """Extract this trajectory into an experience card.

Trajectory (JSON):
{trajectory_json}

Return JSON with these exact keys:
{{
  "intent_text": "<one sentence describing what the user wanted, model-agnostic>",
  "preconditions": "<when this approach applies>",
  "script_steps": [
    {{"step": "<short imperative>", "why": "<reason>", "how": "<concrete how-to>"}},
    ...
  ],
  "tool_capabilities": ["<abstract capability needed, e.g. 'file_read', 'http_fetch'>"],
  "key_decisions": ["<decision and reason>"],
  "pitfalls": ["<antipattern to avoid>"],
  "summary": "<2-3 sentence digest>",
  "confidence": <float 0..1, your confidence in this extraction>
}}

Output ONLY the JSON object."""

EXTRACTOR_PROMPT_B = """Read this agent trajectory and produce a reusable
playbook another model could follow for similar tasks.

Trajectory:
{trajectory_json}

Respond with JSON:
{{
  "intent_text": "...",
  "preconditions": "...",
  "script_steps": [{{"step": "...", "why": "...", "how": "..."}}],
  "tool_capabilities": ["..."],
  "key_decisions": ["..."],
  "pitfalls": ["..."],
  "summary": "...",
  "confidence": 0.0
}}

JSON only."""

JUDGE_SYSTEM = (
    "You are a strict judge evaluating an agent trajectory on five dimensions. "
    "Output STRICT JSON only."
)

JUDGE_PROMPT = """Score the trajectory below on a 5-dimensional reward.

Each dimension is a float in [-1.0, 1.0]:
  outcome       : was the user's goal completed?
  intent        : was the user's intent correctly understood?
  execution     : was the path efficient (no redundant retries, dead-ends)?
  orchestration : was tool/subtask sequencing sensible?
  expression    : was the final response clear and informative?

Trajectory:
{trajectory_json}

Extracted card (for context):
{card_json}

Return JSON:
{{
  "outcome": <float>,
  "intent": <float>,
  "execution": <float>,
  "orchestration": <float>,
  "expression": <float>,
  "confidence": <float 0..1, your confidence in this rating>,
  "rationale": "<one short sentence>"
}}

JSON only."""


def render_extractor(prompt_id: str, trajectory: list[dict[str, Any]]) -> tuple[str, str]:
    template = EXTRACTOR_PROMPT_A if prompt_id == "A" else EXTRACTOR_PROMPT_B
    return EXTRACTOR_SYSTEM, template.format(trajectory_json=json.dumps(trajectory, ensure_ascii=False))


def render_judge(trajectory: list[dict[str, Any]], card: dict[str, Any]) -> tuple[str, str]:
    return JUDGE_SYSTEM, JUDGE_PROMPT.format(
        trajectory_json=json.dumps(trajectory, ensure_ascii=False),
        card_json=json.dumps(card, ensure_ascii=False),
    )
