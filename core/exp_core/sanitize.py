"""Three-layer trajectory sanitizer.

Layer 1 (always): deterministic regex + Luhn rules driven by sanitize_rules.yaml.
                  Cheap, fast, applies to every push.
Layer 2 (gated):  heuristic PII detector. Flags but does NOT replace - the
                  decision to redact stays with reviewers.
                  Skipped when sensitivity == 'low' AND Layer 1 found nothing.
Layer 3 (gated):  LLM business-sensitivity check. Returns structured
                  judgement: is_sensitive, categories, rationale.
                  Run when sensitivity == 'high' OR Layer 2 surfaced anything.

Public entry point: sanitize_trajectory(trajectory, sensitivity=...) -> SanitizationResult.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from . import llm, opf_filter

DEFAULT_RULES_PATH = Path(__file__).parent / "sanitize_rules.yaml"

# Categories the LLM is allowed to return in Layer 3.
LLM_SENSITIVITY_CATEGORIES = (
    "internal_strategy",
    "unreleased_product",
    "financial_nonpublic",
    "legal_privileged",
    "personnel",
    "none",
)

# Phone numbers: regex is permissive. Only treat as a phone if the digit
# count falls into a plausible range. Avoids matching e.g. "12345" inside
# a numeric column.
PHONE_DIGIT_MIN = 10
PHONE_DIGIT_MAX = 15

# Layer 2 heuristic patterns.
# A name candidate looks like "Firstname Lastname" preceded by a person-mention
# verb. We deliberately keep recall low; this is a flag, not a redaction.
_PERSON_MENTION_RE = re.compile(
    r"(?<!\w)(?:Mr|Mrs|Ms|Dr|Prof|name(?:d)?\s+is|told|met|called|"
    r"contacted|emailed|spoke\s+with)\s*\.?\s+([A-Z][a-z]{1,20}\s+[A-Z][a-z]{1,20})"
)
# Address keywords (English). Looks for "<digits> <Word(s)> Street/Road/Ave/...".
_ADDRESS_RE = re.compile(
    r"\b\d{1,5}\s+(?:[A-Z][a-z]+\s+){1,4}"
    r"(?:Street|St|Road|Rd|Avenue|Ave|Boulevard|Blvd|Lane|Ln|Drive|Dr|Way|Court|Ct)\b"
)
# Date-of-birth-shaped: 1900-2025 + month/day, or DD/MM/YYYY.
_DOB_RE = re.compile(
    r"\b(?:"
    r"(?:19|20)\d{2}[-/](?:0?[1-9]|1[0-2])[-/](?:0?[1-9]|[12]\d|3[01])"
    r"|(?:0?[1-9]|[12]\d|3[01])[/-](?:0?[1-9]|1[0-2])[/-](?:19|20)\d{2}"
    r")\b"
)
# 13-19 contiguous digits with optional dashes/spaces - a "credit-card-shape"
# leftover Layer 1 might have missed (e.g. printed without grouping of 4).
_CARD_LIKE_RE = re.compile(r"\b(?:\d[ -]?){13,19}\b")


@dataclass(frozen=True)
class Layer2Finding:
    start: int
    end: int
    category: str
    snippet: str


@dataclass(frozen=True)
class Layer3Finding:
    is_sensitive: bool
    categories: tuple[str, ...]
    rationale: str


@dataclass(frozen=True)
class SanitizationResult:
    sanitized: list[dict[str, Any]]
    redactions: dict[str, int]
    triggered_human_review: bool
    layer2_findings: tuple[Layer2Finding, ...] = field(default_factory=tuple)
    layer3: Layer3Finding | None = None
    changed: bool = False
    status: str = "done"  # 'done' | 'flagged' | 'human_review' | 'quarantined'
    # Layer 1.5 (OPF) attribution. Empty dict if model unavailable.
    opf_hits: dict[str, int] = field(default_factory=dict)
    opf_used: bool = False

    def to_metadata(self) -> dict[str, Any]:
        """Serialize for storage in audit log / disk side-files."""
        return {
            "redactions": dict(self.redactions),
            "triggered_human_review": self.triggered_human_review,
            "layer2_findings": [
                {
                    "category": f.category,
                    "snippet": f.snippet,
                    "start": f.start,
                    "end": f.end,
                }
                for f in self.layer2_findings
            ],
            "layer3": (
                {
                    "is_sensitive": self.layer3.is_sensitive,
                    "categories": list(self.layer3.categories),
                    "rationale": self.layer3.rationale,
                }
                if self.layer3
                else None
            ),
            "opf": {
                "used": self.opf_used,
                "hits": dict(self.opf_hits),
            },
            "changed": self.changed,
            "status": self.status,
        }


# ---------------------------------------------------------------------------
# Layer 1: deterministic rules
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CompiledRule:
    name: str
    severity: str
    placeholder: str
    pattern: re.Pattern[str] | None = None
    match_kind: str | None = None
    min_digits: int | None = None


@dataclass(frozen=True)
class RuleSet:
    rules: tuple[CompiledRule, ...]
    internal_domains: tuple[str, ...]
    employee_id_prefixes: tuple[str, ...]


def load_rules(path: Path | str | None = None) -> RuleSet:
    """Load and compile rules from YAML. Pure function for testability."""
    rules_path = Path(path) if path else DEFAULT_RULES_PATH
    with rules_path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}

    compiled: list[CompiledRule] = []
    for raw in data.get("rules", []):
        name = raw["name"]
        severity = raw.get("severity", "medium")
        placeholder = raw.get("placeholder", "<REDACTED>")
        if "pattern" in raw:
            compiled.append(
                CompiledRule(
                    name=name,
                    severity=severity,
                    placeholder=placeholder,
                    pattern=re.compile(raw["pattern"]),
                    min_digits=raw.get("min_digits"),
                )
            )
        elif "match" in raw:
            compiled.append(
                CompiledRule(
                    name=name,
                    severity=severity,
                    placeholder=placeholder,
                    match_kind=raw["match"],
                )
            )
        else:
            raise ValueError(f"rule {name} has neither pattern nor match")

    return RuleSet(
        rules=tuple(compiled),
        internal_domains=tuple(data.get("internal_domains", []) or []),
        employee_id_prefixes=tuple(data.get("employee_id_prefixes", []) or []),
    )


def _luhn_valid(digits: str) -> bool:
    """Luhn checksum. `digits` must be 13-19 numeric chars only."""
    if not (13 <= len(digits) <= 19) or not digits.isdigit():
        return False
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def _redact_credit_cards(text: str, placeholder: str) -> tuple[str, int]:
    """Find all 13-19 digit sequences (with optional spaces/dashes) and
    replace those that pass Luhn validation."""
    count = 0

    def repl(m: re.Match[str]) -> str:
        nonlocal count
        raw = m.group(0)
        digits = re.sub(r"[ -]", "", raw)
        if _luhn_valid(digits):
            count += 1
            return placeholder
        return raw

    new_text = _CARD_LIKE_RE.sub(repl, text)
    return new_text, count


def _redact_phones(text: str, rule: CompiledRule) -> tuple[str, int]:
    """Apply phone regex but require min_digits actual digits to fire.
    The default phone regex is permissive, so we filter post-match."""
    assert rule.pattern is not None
    count = 0
    min_digits = rule.min_digits or PHONE_DIGIT_MIN

    def repl(m: re.Match[str]) -> str:
        nonlocal count
        raw = m.group(0)
        digit_count = sum(1 for c in raw if c.isdigit())
        if digit_count < min_digits or digit_count > PHONE_DIGIT_MAX:
            return raw
        count += 1
        return rule.placeholder

    return rule.pattern.sub(repl, text), count


def _redact_internal_domains(
    text: str, domains: tuple[str, ...]
) -> tuple[str, int]:
    if not domains:
        return text, 0
    # Match each domain as a hostname-like suffix: optional subdomain segments,
    # then the configured suffix, optional path/port - we only rewrite the host part.
    count = 0
    new_text = text
    for dom in domains:
        # Anchor on word boundary on the left, allow subdomains.
        rx = re.compile(
            r"\b(?:[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?\.)*"
            + re.escape(dom)
            + r"\b",
            re.IGNORECASE,
        )

        def repl(_m: re.Match[str]) -> str:
            nonlocal count
            count += 1
            return "<INTERNAL_HOST>"

        new_text = rx.sub(repl, new_text)
    return new_text, count


def _redact_employee_ids(
    text: str, prefixes: tuple[str, ...]
) -> tuple[str, int]:
    if not prefixes:
        return text, 0
    count = 0
    rx = re.compile(
        r"\b(?:" + "|".join(re.escape(p) for p in prefixes) + r")[-_]?\d{4,8}\b",
        re.IGNORECASE,
    )

    def repl(_m: re.Match[str]) -> str:
        nonlocal count
        count += 1
        return "<EMP_ID>"

    return rx.sub(repl, text), count


# Rules whose category names should bump the result to high severity.
# Kept in sync with the `severity: high` entries in sanitize_rules.yaml.
_HIGH_SEVERITY_CATEGORIES = {
    # Keys / private data
    "pem_private_key", "ssh_pubkey", "ssh_rsa", "gcp_sa_key",
    "aws_access_key", "jwt", "bearer_token",
    # Vendor secret tokens
    "anthropic_key", "openai_key", "openai_proj_key", "xai_key", "groq_key",
    "google_api_key", "hf_token", "mimo_token",
    "stripe_secret", "stripe_publishable",
    "github_token", "gitlab_token", "npm_token",
    "vercel_token", "supabase_token", "cloudflare_token",
    "sentry_dsn", "slack_token",
    # Generic / structural
    "generic_api_key", "url_with_credentials", "db_uri",
    # PII high
    "credit_card", "idcard_cn",
}


def layer1_text(
    text: str, rules: RuleSet
) -> tuple[str, dict[str, int], bool]:
    """Apply all Layer 1 rules to a single string.

    Returns (sanitized_text, per_category_counts, triggered_human_review).
    """
    if not text:
        return text, {}, False

    counts: dict[str, int] = {}
    triggered_high = False
    out = text

    for rule in rules.rules:
        if rule.match_kind == "luhn":
            out, n = _redact_credit_cards(out, rule.placeholder)
        elif rule.name == "phone_intl":
            out, n = _redact_phones(out, rule)
        elif rule.pattern is not None:
            new_out, n = rule.pattern.subn(rule.placeholder, out)
            out = new_out
        else:
            continue
        if n:
            counts[rule.name] = counts.get(rule.name, 0) + n
            if rule.severity == "high" or rule.name in _HIGH_SEVERITY_CATEGORIES:
                triggered_high = True

    out, n = _redact_internal_domains(out, rules.internal_domains)
    if n:
        counts["internal_host"] = n

    out, n = _redact_employee_ids(out, rules.employee_id_prefixes)
    if n:
        counts["employee_id"] = n

    return out, counts, triggered_high


# Keys that are pure identifiers / structural — sanitizing them would
# corrupt routing (tool_use_id, role, etc.). Skip during recursion.
_SKIP_KEYS_RECURSIVE = frozenset({
    "id", "type", "role", "tool_use_id", "tool_call_id",
    "name", "subtype", "model", "stop_reason", "stop_sequence",
    "usage", "index",
})


def layer1_trajectory(
    trajectory: list[dict[str, Any]], rules: RuleSet
) -> tuple[list[dict[str, Any]], dict[str, int], bool]:
    """Apply Layer 1 rules to every string in every turn (immutable).

    Recursively walks dicts and lists so that secrets nested inside
    `tool_calls.arguments`, `tool_result.content`, Anthropic block lists,
    etc. all get scrubbed. Skips structural identifier keys so message
    routing stays intact.
    """
    total_counts: dict[str, int] = {}
    triggered = False

    def _walk(node: Any) -> Any:
        nonlocal triggered
        if isinstance(node, str):
            sanitized, counts, hi = layer1_text(node, rules)
            for cat, c in counts.items():
                total_counts[cat] = total_counts.get(cat, 0) + c
            triggered = triggered or hi
            return sanitized
        if isinstance(node, list):
            return [_walk(x) for x in node]
        if isinstance(node, dict):
            out: dict[str, Any] = {}
            for k, v in node.items():
                if k in _SKIP_KEYS_RECURSIVE or not isinstance(v, (str, list, dict)):
                    out[k] = v
                else:
                    out[k] = _walk(v)
            return out
        return node

    new_traj = [_walk(turn) if isinstance(turn, dict) else turn for turn in trajectory]
    return new_traj, total_counts, triggered


# ---------------------------------------------------------------------------
# Layer 2: heuristic PII detector (flag-only)
# ---------------------------------------------------------------------------


def _scan_text_layer2(text: str, offset: int = 0) -> list[Layer2Finding]:
    findings: list[Layer2Finding] = []
    for m in _PERSON_MENTION_RE.finditer(text):
        # Group 1 is the actual name.
        s, e = m.span(1)
        findings.append(
            Layer2Finding(
                start=offset + s, end=offset + e, category="name", snippet=m.group(1)
            )
        )
    for m in _ADDRESS_RE.finditer(text):
        findings.append(
            Layer2Finding(
                start=offset + m.start(),
                end=offset + m.end(),
                category="address",
                snippet=m.group(0),
            )
        )
    for m in _DOB_RE.finditer(text):
        findings.append(
            Layer2Finding(
                start=offset + m.start(),
                end=offset + m.end(),
                category="dob",
                snippet=m.group(0),
            )
        )
    for m in _CARD_LIKE_RE.finditer(text):
        digits = re.sub(r"[ -]", "", m.group(0))
        if _luhn_valid(digits):
            findings.append(
                Layer2Finding(
                    start=offset + m.start(),
                    end=offset + m.end(),
                    category="card_like",
                    snippet=m.group(0),
                )
            )
    return findings


def layer2_trajectory(trajectory: list[dict[str, Any]]) -> list[Layer2Finding]:
    """Run heuristic PII detection across all string fields."""
    findings: list[Layer2Finding] = []
    cursor = 0
    for turn in trajectory:
        for v in turn.values():
            if isinstance(v, str):
                findings.extend(_scan_text_layer2(v, offset=cursor))
                cursor += len(v) + 1
    return findings


# ---------------------------------------------------------------------------
# Layer 3: LLM business-sensitivity check
# ---------------------------------------------------------------------------


_LAYER3_SYSTEM = (
    "You are a corporate sensitivity reviewer. You decide whether an agent "
    "trajectory contains business-sensitive content that should not be "
    "shared in a multi-agent experience pool. Output STRICT JSON only, no prose."
)

_LAYER3_PROMPT = """Inspect this agent trajectory for business sensitivity.

Trajectory:
{trajectory_json}

Categories you may use:
  internal_strategy        - confidential roadmap, OKRs, M&A, competitive plans
  unreleased_product       - features/products not yet announced
  financial_nonpublic      - revenue, forecasts, hiring numbers pre-disclosure
  legal_privileged         - attorney-client communication, active litigation
  personnel                - hiring/firing decisions, performance reviews
  none                     - no business-sensitive content

Return JSON with these exact keys:
{{
  "is_sensitive": <true|false>,
  "categories": ["<one of the categories above>", ...],
  "rationale": "<one short sentence>"
}}

JSON only."""


def layer3_llm(trajectory: list[dict[str, Any]]) -> Layer3Finding:
    """Call the LLM. Returns a structured judgement.

    Defensive: if the LLM is unreachable or returns garbage, we fall back
    to is_sensitive=False but tag the rationale so a human review can spot it.
    """
    prompt = _LAYER3_PROMPT.format(
        trajectory_json=json.dumps(trajectory, ensure_ascii=False)
    )
    try:
        raw = llm.call_json(prompt, system=_LAYER3_SYSTEM)
    except Exception as exc:  # noqa: BLE001
        return Layer3Finding(
            is_sensitive=False,
            categories=(),
            rationale=f"layer3_unreachable: {exc!s}",
        )

    is_sensitive = bool(raw.get("is_sensitive", False))
    cats_raw = raw.get("categories") or []
    if not isinstance(cats_raw, list):
        cats_raw = []
    categories = tuple(
        str(c) for c in cats_raw if str(c) in LLM_SENSITIVITY_CATEGORIES
    )
    rationale = str(raw.get("rationale", "")) or ""
    return Layer3Finding(
        is_sensitive=is_sensitive,
        categories=categories,
        rationale=rationale,
    )


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def _layer1_5_opf(
    trajectory: list[dict[str, Any]], counts: dict[str, int]
) -> tuple[list[dict[str, Any]], dict[str, int], bool, bool]:
    """Layer 1.5: OpenAI Privacy Filter pass.

    Runs after Layer 1 regex on already-sanitized text — this means OPF
    only sees what regex didn't catch, reducing false positives on tokens
    that already became `<SECRET>` placeholders. Returns (new_trajectory,
    opf_hits, opf_triggered_high, opf_used).
    """
    opf_hits: dict[str, int] = {}
    new_traj: list[dict[str, Any]] = []
    opf_used = False
    triggered_high = False
    for turn in trajectory:
        cleaned_turn, hi = opf_filter.redact_node(turn, opf_hits)
        new_traj.append(cleaned_turn)
        triggered_high = triggered_high or hi
    if opf_hits:
        opf_used = True
        # Roll OPF hits into the master redactions counter so audit logs
        # see one unified per-category dict. Prefix with "opf_" so we can
        # distinguish them from Layer 1 categories.
        for label, n in opf_hits.items():
            counts[f"opf_{label}"] = counts.get(f"opf_{label}", 0) + n
    else:
        # Even when there were zero hits, record whether the model ran so
        # operators can verify OPF is actually live in production.
        opf_used = opf_filter.status().get("loaded", False)
    return new_traj, opf_hits, triggered_high, opf_used


def sanitize_trajectory(
    trajectory: list[dict[str, Any]],
    *,
    sensitivity: str = "medium",
    rules: RuleSet | None = None,
    use_opf: bool = True,
) -> SanitizationResult:
    """Run the four-layer pipeline.

    Layer 1   — always: deterministic regex (driven by sanitize_rules.yaml).
    Layer 1.5 — when `use_opf=True` AND opf_filter is loadable: OpenAI
                Privacy Filter for context-aware PII spans (8 categories).
                The 'secret' category escalates to human review.
    Layer 2   — unless (sensitivity=='low' AND nothing fired): heuristic
                name/address/DOB/credit-card-shape detection (flag-only).
    Layer 3   — when sensitivity=='high' OR Layer 2 surfaced anything:
                LLM business-sensitivity check.
    """
    rules = rules or load_rules()

    sanitized, counts, layer1_high = layer1_trajectory(trajectory, rules)
    layer1_changed = bool(counts)

    # Layer 1.5 — OPF context-aware PII pass.
    opf_hits: dict[str, int] = {}
    opf_high = False
    opf_used = False
    if use_opf and opf_filter.is_enabled():
        sanitized, opf_hits, opf_high, opf_used = _layer1_5_opf(sanitized, counts)

    # Layer 2 gate.
    skip_layer2 = sensitivity == "low" and not layer1_changed and not opf_hits
    findings: tuple[Layer2Finding, ...] = ()
    if not skip_layer2:
        findings = tuple(layer2_trajectory(sanitized))

    # Layer 3 gate.
    layer3: Layer3Finding | None = None
    if sensitivity == "high" or findings:
        layer3 = layer3_llm(sanitized)

    triggered_human_review = (
        layer1_high
        or opf_high
        or (layer3 is not None and layer3.is_sensitive)
    )

    if triggered_human_review:
        status = "human_review"
    elif layer1_changed or findings or opf_hits:
        status = "flagged"
    else:
        status = "done"

    return SanitizationResult(
        sanitized=sanitized,
        redactions=counts,
        triggered_human_review=triggered_human_review,
        layer2_findings=findings,
        layer3=layer3,
        changed=layer1_changed or bool(opf_hits),
        status=status,
        opf_hits=opf_hits,
        opf_used=opf_used,
    )
