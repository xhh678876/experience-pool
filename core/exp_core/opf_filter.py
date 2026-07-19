"""OpenAI Privacy Filter integration (Layer 1.5).

Sits between the deterministic regex layer (Layer 1) and the heuristic /
LLM layers (Layer 2/3). Detects 8 PII span categories that regex cannot
generalize to:

    account_number, private_address, private_email, private_person,
    private_phone, private_url, private_date, secret

Design notes:
  * Lazy module import. If `opf` package is not installed (or model weights
    aren't downloaded), this module degrades to a no-op so push paths
    never hard-fail on a missing dependency.
  * The model is loaded once per process (cached in module state).
  * `redact_text(...)` is the only public entry point; it returns the
    cleaned text plus a per-category hit dict, matching the Layer 1 shape.
  * Replacement format `<PRIVATE_FILTER:{label}>` is intentionally
    distinguishable from the regex placeholders (`<SECRET>`, `<EMAIL>`,
    ...) so audits can attribute who caught what.

Environment variables:
    EXP_OPF_DISABLED       set to "1" to hard-disable OPF even if installed
    OPF_CHECKPOINT         path to model checkpoint (default ~/.opf/privacy_filter)
    OPF_DEVICE             "cpu" | "cuda" | "mps" (default cpu)
    OPF_OPERATING_POINT    "high_recall" | "high_precision" | "balanced" (default balanced)

License: integrating openai/privacy-filter (Apache 2.0).
"""

from __future__ import annotations

import importlib
import logging
import os
import threading
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# Severity assignment for OPF labels. `secret` is the only one that should
# escalate the experience to human review; the rest are PII flags that
# Layer 2/3 already gate on.
_HIGH_SEVERITY_LABELS = frozenset({"secret"})

_OPF_LOCK = threading.Lock()
_OPF_API: Any = None
_OPF_TRIED = False
_OPF_LOAD_ERROR: str | None = None


@dataclass(frozen=True)
class OpfResult:
    """Outcome of an OPF pass over one text string."""

    text: str
    hits: dict[str, int]
    triggered_high: bool
    used: bool  # False when OPF was unavailable; caller may fall through


def is_enabled() -> bool:
    """True iff OPF should be attempted. Cheap; no model load."""
    return os.environ.get("EXP_OPF_DISABLED") != "1"


def _load_api() -> Any:
    """Lazy-load the OPF runtime. Returns None on any failure.

    The opf package exposes a high-level `OPF` class in `opf._api` that
    handles checkpoint resolution + Viterbi decoder construction. We
    initialize it once with output_mode='typed' so the result includes
    span metadata (start/end/label) we can splice back into the text.
    """
    global _OPF_API, _OPF_TRIED, _OPF_LOAD_ERROR
    if _OPF_API is not None or _OPF_TRIED:
        return _OPF_API
    with _OPF_LOCK:
        if _OPF_API is not None or _OPF_TRIED:
            return _OPF_API
        _OPF_TRIED = True
        if not is_enabled():
            _OPF_LOAD_ERROR = "EXP_OPF_DISABLED=1"
            return None
        try:
            # Import the parent first. A stale ``opf._api`` entry can remain
            # in sys.modules after the package itself becomes unavailable;
            # importing the child directly would incorrectly reuse it.
            importlib.import_module("opf")
            OPF = importlib.import_module("opf._api").OPF
        except Exception as exc:  # noqa: BLE001
            _OPF_LOAD_ERROR = f"opf package not importable: {exc!s}"
            logger.info("OPF disabled: %s", _OPF_LOAD_ERROR)
            return None
        ckpt = os.environ.get(
            "OPF_CHECKPOINT", os.path.expanduser("~/.opf/privacy_filter")
        )
        device = os.environ.get("OPF_DEVICE", "cpu")
        try:
            _OPF_API = OPF(
                model=ckpt,
                device=device,  # type: ignore[arg-type]
                output_mode="typed",
            )
        except Exception as exc:  # noqa: BLE001
            _OPF_LOAD_ERROR = f"opf load failed: {exc!s}"
            logger.warning("OPF load failed (%s); falling through", _OPF_LOAD_ERROR)
            return None
        logger.info("OPF loaded from %s on %s", ckpt, device)
        return _OPF_API


def status() -> dict[str, Any]:
    """Operator-facing status dict for /healthz or /admin/usage endpoints."""
    return {
        "enabled": is_enabled(),
        "loaded": _OPF_API is not None,
        "tried": _OPF_TRIED,
        "load_error": _OPF_LOAD_ERROR,
    }


def _redact_via_api(api: Any, text: str) -> tuple[str, dict[str, int]]:
    """Apply the OPF model to one text string. Replaces detected spans
    with `<PRIVATE_FILTER:{label}>`. Returns (clean_text, hit_counts).

    The opf.OPF.redact() method returns a RedactionResult with `.spans`
    (list of DetectedSpan with start/end/label) when output_mode='typed'.
    Older opf builds return spans directly — we accept both shapes.
    """
    result = api.redact(text)
    # Walk to a span list whether `result` is a RedactionResult or a list.
    spans = getattr(result, "spans", None)
    if spans is None:
        spans = getattr(result, "predicted_spans", None)
    if spans is None and isinstance(result, (list, tuple)):
        spans = result
    if spans is None and hasattr(result, "redacted_text"):
        # output_mode='redacted' returned just the cleaned text — treat as
        # a black-box: we don't know span counts, so return the cleaned
        # text and a single 'private' hit for visibility.
        cleaned = getattr(result, "redacted_text") or text
        return cleaned, {"private": 1} if cleaned != text else {}
    hits: dict[str, int] = {}
    if not spans:
        return text, hits
    # Materialize spans into start/end/label tuples; sort descending by
    # `start` so we can splice in place without remapping offsets.
    indexed: list[tuple[int, int, str]] = []
    for s in spans:
        start = getattr(s, "start", None)
        end = getattr(s, "end", None)
        label = (
            getattr(s, "label", None)
            or getattr(s, "category", None)
            or getattr(s, "tag", None)
            or "private"
        )
        if start is None or end is None:
            continue
        indexed.append((int(start), int(end), str(label)))
    if not indexed:
        return text, hits
    indexed.sort(key=lambda x: x[0], reverse=True)
    out = text
    for start, end, label in indexed:
        out = out[:start] + f"<PRIVATE_FILTER:{label}>" + out[end:]
        hits[label] = hits.get(label, 0) + 1
    return out, hits


def redact_text(text: str) -> OpfResult:
    """Run OPF on a single string. Always safe — returns OpfResult(used=False)
    if OPF is not available, leaving the input untouched."""
    if not text:
        return OpfResult(text=text, hits={}, triggered_high=False, used=False)
    api = _load_api()
    if api is None:
        return OpfResult(text=text, hits={}, triggered_high=False, used=False)
    try:
        clean, hits = _redact_via_api(api, text)
    except Exception as exc:  # noqa: BLE001
        # Don't fail the whole push if OPF crashes on one input.
        logger.warning("OPF redact failed: %s", exc)
        return OpfResult(text=text, hits={}, triggered_high=False, used=False)
    triggered_high = any(label in _HIGH_SEVERITY_LABELS for label in hits)
    return OpfResult(text=clean, hits=hits, triggered_high=triggered_high, used=True)


def redact_node(node: Any, hits: dict[str, int]) -> tuple[Any, bool]:
    """Recursively apply OPF to every string inside a dict/list tree.

    Mutates `hits` in place. Returns (cleaned_node, triggered_high_anywhere).
    Skips structural keys to avoid corrupting routing identifiers.
    """
    triggered = False

    SKIP_KEYS = {
        "id", "type", "role", "tool_use_id", "tool_call_id",
        "name", "subtype", "model", "stop_reason", "stop_sequence",
        "usage", "index",
    }

    def _walk(n: Any) -> Any:
        nonlocal triggered
        if isinstance(n, str):
            res = redact_text(n)
            for k, v in res.hits.items():
                hits[k] = hits.get(k, 0) + v
            triggered = triggered or res.triggered_high
            return res.text
        if isinstance(n, list):
            return [_walk(x) for x in n]
        if isinstance(n, dict):
            return {
                k: (v if k in SKIP_KEYS or not isinstance(v, (str, list, dict)) else _walk(v))
                for k, v in n.items()
            }
        return n

    return _walk(node), triggered
