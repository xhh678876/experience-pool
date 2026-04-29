"""Parquet export for offline training (M6).

Reads from the SQLite pool and produces partitioned Parquet files suitable for
PyTorch / TensorFlow / Spark training pipelines. The schema matches what the
ranking and judge models would see during online inference, plus the discrete
sign-of-float reward columns needed for classification heads.

Layout produced::

    <root>/task_type=<X>/date=<YYYY-MM-DD>/data.parquet

Each row corresponds to a single experience joined with its latest reward,
the count of Q updates, the parent edges, and the current Q values.

Only stdlib + pyarrow are used. Pandas is intentionally NOT a dependency.
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import pyarrow as pa
import pyarrow.parquet as pq

from .pool import PoolConfig

DIMS = ("outcome", "intent", "execution", "orchestration", "expression")

# Default partitioning columns. Date is derived from created_at (UTC, YYYY-MM-DD).
DEFAULT_PARTITION_BY: tuple[str, ...] = ("task_type", "date")


@dataclass(frozen=True)
class ExportResult:
    """Summary returned from :func:`export`."""

    row_count: int
    partition_paths: list[Path]


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCRIPT_STEP_TYPE = pa.struct([
    ("step", pa.string()),
    ("why", pa.string()),
    ("how", pa.string()),
])


def _build_schema() -> pa.Schema:
    fields: list[pa.Field] = [
        pa.field("experience_id", pa.string()),
        pa.field("agent_id", pa.string()),
        pa.field("task_type", pa.string()),
        pa.field("source_model", pa.string()),
        pa.field("created_at", pa.timestamp("us", tz="UTC")),
        pa.field("intent_text", pa.string()),
        pa.field("preconditions", pa.string()),
        pa.field("script_steps", pa.list_(_SCRIPT_STEP_TYPE)),
        pa.field("tool_capabilities", pa.list_(pa.string())),
        pa.field("key_decisions", pa.list_(pa.string())),
        pa.field("pitfalls", pa.list_(pa.string())),
        pa.field("summary", pa.string()),
    ]
    for d in DIMS:
        fields.append(pa.field(f"r_{d}", pa.float32()))
    for d in DIMS:
        fields.append(pa.field(f"r_{d}_discrete", pa.int8()))
    fields.append(pa.field("confidence", pa.float32()))
    for d in DIMS:
        fields.append(pa.field(f"q_{d}", pa.float32()))
    fields.extend([
        pa.field("q_update_count", pa.int32()),
        pa.field("reuse_count", pa.int32()),
        pa.field("visit_count", pa.int32()),
        pa.field("parent_ids", pa.list_(pa.string())),
        pa.field("tags", pa.list_(pa.string())),
        pa.field("sensitivity", pa.string()),
        pa.field("acl", pa.string()),
    ])
    return pa.schema(fields)


SCHEMA: pa.Schema = _build_schema()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_created_at(raw: str | None) -> datetime | None:
    if not raw:
        return None
    # SQLite emits 'YYYY-MM-DD HH:MM:SS' from datetime('now') (UTC).
    # Normalize to a tz-aware UTC datetime.
    try:
        dt = datetime.fromisoformat(raw.replace(" ", "T"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _date_str(dt: datetime | None) -> str:
    if dt is None:
        return "unknown"
    return dt.strftime("%Y-%m-%d")


def _sign(x: float | None) -> int:
    if x is None:
        return 0
    if x > 0:
        return 1
    if x < 0:
        return -1
    return 0


def _safe_json_list(raw: Any) -> list[Any]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return raw
    try:
        v = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    return v if isinstance(v, list) else []


def _normalize_steps(raw: Any) -> list[dict[str, str]]:
    items = _safe_json_list(raw)
    out: list[dict[str, str]] = []
    for item in items:
        if isinstance(item, dict):
            out.append({
                "step": str(item.get("step", "") or ""),
                "why": str(item.get("why", "") or ""),
                "how": str(item.get("how", "") or ""),
            })
        else:
            out.append({"step": str(item), "why": "", "how": ""})
    return out


def _normalize_str_list(raw: Any) -> list[str]:
    return [str(x) for x in _safe_json_list(raw)]


def _open_conn(config: PoolConfig) -> sqlite3.Connection:
    """Open a read-only-ish connection using the same config as the pool."""
    conn = sqlite3.connect(config.db_path)
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# Row builder
# ---------------------------------------------------------------------------

def _fetch_rows(
    conn: sqlite3.Connection,
    *,
    since_date: str | None,
    until_date: str | None,
    task_type: str | None,
) -> list[dict[str, Any]]:
    """Join experiences + latest reward + q_update count + parents into dicts."""
    where_clauses: list[str] = []
    params: list[Any] = []
    if since_date:
        where_clauses.append("date(e.created_at) >= date(?)")
        params.append(since_date)
    if until_date:
        where_clauses.append("date(e.created_at) <= date(?)")
        params.append(until_date)
    if task_type:
        where_clauses.append("e.task_type = ?")
        params.append(task_type)
    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    # Latest reward per experience: pick the max created_at row.
    # Q update count: SELECT COUNT(*) FROM q_updates ... grouped.
    # Parent ids: aggregated from experience_edges.
    sql = f"""
        SELECT
            e.experience_id, e.agent_id, e.task_type, e.source_model, e.created_at,
            e.intent_text, e.preconditions, e.script_steps, e.tool_capabilities,
            e.key_decisions, e.pitfalls, e.summary,
            e.q_outcome, e.q_intent, e.q_execution, e.q_orchestration, e.q_expression,
            e.q_update_count, e.reuse_count, e.visit_count,
            e.tags, e.sensitivity, e.acl,
            r.r_outcome, r.r_intent, r.r_execution, r.r_orchestration, r.r_expression,
            r.confidence,
            (SELECT COUNT(*) FROM q_updates qu WHERE qu.experience_id = e.experience_id)
                AS q_update_total
        FROM experiences e
        LEFT JOIN rewards r ON r.reward_id = (
            SELECT reward_id FROM rewards
            WHERE experience_id = e.experience_id
            ORDER BY created_at DESC, reward_id DESC
            LIMIT 1
        )
        {where_sql}
        ORDER BY e.created_at ASC
    """
    cur = conn.execute(sql, params)
    rows = [dict(r) for r in cur.fetchall()]

    # Fetch parent edges in one shot.
    parent_map: dict[str, list[str]] = defaultdict(list)
    cur = conn.execute("SELECT child_id, parent_id FROM experience_edges")
    for r in cur.fetchall():
        parent_map[r["child_id"]].append(r["parent_id"])
    for row in rows:
        row["parent_ids"] = parent_map.get(row["experience_id"], [])
    return rows


def _row_to_record(row: dict[str, Any]) -> dict[str, Any]:
    """Convert a SQLite-shaped row dict to the export record shape."""
    created_at = _parse_created_at(row.get("created_at"))
    rec: dict[str, Any] = {
        "experience_id": row["experience_id"],
        "agent_id": row["agent_id"],
        "task_type": row["task_type"],
        "source_model": row["source_model"],
        "created_at": created_at,
        "intent_text": row.get("intent_text") or "",
        "preconditions": row.get("preconditions") or "",
        "script_steps": _normalize_steps(row.get("script_steps")),
        "tool_capabilities": _normalize_str_list(row.get("tool_capabilities")),
        "key_decisions": _normalize_str_list(row.get("key_decisions")),
        "pitfalls": _normalize_str_list(row.get("pitfalls")),
        "summary": row.get("summary") or "",
        "confidence": float(row["confidence"]) if row.get("confidence") is not None else 0.0,
        "q_update_count": int(row.get("q_update_total") or row.get("q_update_count") or 0),
        "reuse_count": int(row.get("reuse_count") or 0),
        "visit_count": int(row.get("visit_count") or 0),
        "parent_ids": list(row.get("parent_ids") or []),
        "tags": _normalize_str_list(row.get("tags")),
        "sensitivity": row.get("sensitivity") or "medium",
        "acl": row.get("acl") or "private",
    }
    for d in DIMS:
        r_val = row.get(f"r_{d}")
        f = float(r_val) if r_val is not None else 0.0
        rec[f"r_{d}"] = f
        rec[f"r_{d}_discrete"] = _sign(f if r_val is not None else None)
        q_val = row.get(f"q_{d}")
        rec[f"q_{d}"] = float(q_val) if q_val is not None else 0.0
    return rec


# ---------------------------------------------------------------------------
# Partitioning + write
# ---------------------------------------------------------------------------

def _partition_key(rec: dict[str, Any], partition_by: Iterable[str]) -> tuple[str, ...]:
    parts: list[str] = []
    for key in partition_by:
        if key == "date":
            parts.append(_date_str(rec.get("created_at")))
        else:
            v = rec.get(key)
            parts.append(str(v) if v is not None else "unknown")
    return tuple(parts)


def _partition_dir(root: Path, partition_by: Iterable[str], parts: tuple[str, ...]) -> Path:
    p = root
    for key, val in zip(partition_by, parts, strict=True):
        # Hive-style partitioning: key=value
        p = p / f"{key}={val}"
    return p


def _records_to_table(records: list[dict[str, Any]]) -> pa.Table:
    # Build columnar dict in schema field order.
    columns: dict[str, list[Any]] = {field.name: [] for field in SCHEMA}
    for rec in records:
        for field in SCHEMA:
            columns[field.name].append(rec.get(field.name))
    return pa.table(columns, schema=SCHEMA)


def export(
    root_path: str | Path,
    *,
    since_date: str | None = None,
    until_date: str | None = None,
    task_type: str | None = None,
    partition_by: Iterable[str] = DEFAULT_PARTITION_BY,
    config: PoolConfig | None = None,
) -> ExportResult:
    """Export experiences to a partitioned Parquet dataset.

    Args:
        root_path: Output directory (will be created).
        since_date: Optional inclusive lower bound on ``created_at`` (YYYY-MM-DD).
        until_date: Optional inclusive upper bound on ``created_at`` (YYYY-MM-DD).
        task_type: Optional task_type filter.
        partition_by: Partition columns. Defaults to ``("task_type", "date")``.
        config: Override the pool config (otherwise reads ``~/.experience-pool``).

    Returns:
        :class:`ExportResult` with the row count and list of partition paths.
    """
    cfg = config or PoolConfig()
    root = Path(root_path)
    root.mkdir(parents=True, exist_ok=True)
    partition_cols = tuple(partition_by)

    conn = _open_conn(cfg)
    try:
        rows = _fetch_rows(
            conn,
            since_date=since_date,
            until_date=until_date,
            task_type=task_type,
        )
    finally:
        conn.close()

    if not rows:
        return ExportResult(row_count=0, partition_paths=[])

    records = [_row_to_record(r) for r in rows]
    grouped: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    for rec in records:
        grouped[_partition_key(rec, partition_cols)].append(rec)

    partition_paths: list[Path] = []
    for parts, recs in grouped.items():
        part_dir = _partition_dir(root, partition_cols, parts)
        part_dir.mkdir(parents=True, exist_ok=True)
        table = _records_to_table(recs)
        # Hive-style partition columns live in the path; drop them from the
        # parquet payload so pyarrow's dataset reader doesn't see two copies
        # (one as string in the file, one as dictionary from the path).
        for col in partition_cols:
            if col in table.schema.names:
                table = table.drop([col])
        out_path = part_dir / "data.parquet"
        pq.write_table(table, out_path, compression="snappy")
        partition_paths.append(out_path)

    return ExportResult(row_count=len(records), partition_paths=partition_paths)


__all__ = ["export", "ExportResult", "SCHEMA", "DEFAULT_PARTITION_BY"]
