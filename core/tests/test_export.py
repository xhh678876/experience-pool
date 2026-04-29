"""Tests for the Parquet export (M6).

Sets EXP_LLM=mock so the full push pipeline runs offline. Then exports the
SQLite pool to a temp directory and asserts that:
  - Parquet partition files exist at the expected paths.
  - Row count matches what we pushed.
  - Schema matches what `export.SCHEMA` declares.
  - Discrete reward columns are constrained to {-1, 0, 1}.

The dataset/torch test is skipped if torch is not installed.
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

os.environ.setdefault("EXP_LLM", "mock")

import pyarrow.dataset as pads  # noqa: E402
import pyarrow.parquet as pq  # noqa: E402
import pytest  # noqa: E402


def _read_parquet_file(path: Path):
    """Read a single Parquet file directly (no partition discovery)."""
    return pq.ParquetFile(str(path)).read()


def _read_dataset(root: Path):
    """Read the full Hive-partitioned dataset, materializing partition columns."""
    return pads.dataset(
        str(root),
        format="parquet",
        partitioning=pads.HivePartitioning.discover(),
    ).to_table()

from exp_core.export import DEFAULT_PARTITION_BY, SCHEMA, export  # noqa: E402
from exp_core.pool import ExperiencePool, PoolConfig  # noqa: E402


def _trajectory(seed: str) -> list[dict]:
    return [
        {"role": "user", "content": f"please summarize this CSV {seed}"},
        {"role": "assistant", "content": "loaded csv, ran describe()"},
        {"role": "user", "content": f"now group by region {seed}"},
        {"role": "assistant", "content": f"applied groupby; here are totals {seed}"},
    ]


def _make_pool_with_data(tmp_path: Path) -> tuple[ExperiencePool, list[str]]:
    pool = ExperiencePool(PoolConfig(root=tmp_path))
    pool.register_agent("agent-a", "platform")
    ids: list[str] = []
    # Use distinct task types so partitioning produces multiple directories.
    seeds = [
        ("csv_analysis", "alpha"),
        ("csv_analysis", "beta"),
        ("code_review", "gamma"),
    ]
    for task, seed in seeds:
        out = pool.push(
            "agent-a",
            task,
            "claude-stub",
            _trajectory(seed),
            sensitivity="low",
        )
        ids.append(out["experience_id"])
    return pool, ids


def test_export_writes_partitioned_parquet(tmp_path):
    pool_root = tmp_path / "pool"
    pool, _ids = _make_pool_with_data(pool_root)

    out_dir = tmp_path / "out"
    result = export(out_dir, config=PoolConfig(root=pool_root))
    pool.close()

    # Three pushes -> at least one row per partition; 2 task types -> >= 2 partitions.
    assert result.row_count == 3
    assert len(result.partition_paths) >= 2

    today = date.today().isoformat()
    expected_paths = [
        out_dir / f"task_type=csv_analysis/date={today}/data.parquet",
        out_dir / f"task_type=code_review/date={today}/data.parquet",
    ]
    for p in expected_paths:
        assert p.exists(), f"missing partition file {p}"
    for p in result.partition_paths:
        assert p.exists()

    # Read everything back and check the schema + row counts.
    # The on-disk file does NOT contain the partition columns; those are
    # encoded in the directory path. The dataset reader merges them back.
    total_rows = 0
    discrete_cols = [f"r_{d}_discrete" for d in (
        "outcome", "intent", "execution", "orchestration", "expression"
    )]
    partition_col_names = {"task_type", "date"}
    for p in result.partition_paths:
        table = _read_parquet_file(p)
        total_rows += table.num_rows
        # Every non-partition SCHEMA field should be present in the file.
        for field in SCHEMA:
            if field.name in partition_col_names:
                continue
            assert field.name in table.schema.names, (
                f"missing column {field.name} in {p}"
            )
            assert table.schema.field(field.name).type.equals(field.type), (
                f"type mismatch for {field.name}: "
                f"{table.schema.field(field.name).type} vs {field.type}"
            )
        for col in discrete_cols:
            values = table.column(col).to_pylist()
            for v in values:
                assert v in {-1, 0, 1}, f"{col} has out-of-range value {v}"
    assert total_rows == 3

    # Reading the partitioned dataset must surface task_type and date.
    full = _read_dataset(out_dir)
    assert full.num_rows == 3
    assert "task_type" in full.schema.names
    assert "date" in full.schema.names
    task_types = set(full.column("task_type").to_pylist())
    assert task_types == {"csv_analysis", "code_review"}


def test_export_filters_by_task_type(tmp_path):
    pool_root = tmp_path / "pool"
    pool, _ids = _make_pool_with_data(pool_root)

    out_dir = tmp_path / "out_filtered"
    result = export(
        out_dir,
        task_type="csv_analysis",
        config=PoolConfig(root=pool_root),
    )
    pool.close()

    assert result.row_count == 2
    # All partition paths should be under the csv_analysis partition.
    for p in result.partition_paths:
        assert "task_type=csv_analysis" in p.as_posix()


def test_export_empty_pool_returns_zero(tmp_path):
    pool_root = tmp_path / "pool"
    pool = ExperiencePool(PoolConfig(root=pool_root))
    pool.close()
    out_dir = tmp_path / "empty_out"
    result = export(out_dir, config=PoolConfig(root=pool_root))
    assert result.row_count == 0
    assert result.partition_paths == []


def test_default_partition_by_constants():
    assert DEFAULT_PARTITION_BY == ("task_type", "date")


def test_dataset_iteration_uses_pyarrow(tmp_path):
    pool_root = tmp_path / "pool"
    pool, _ = _make_pool_with_data(pool_root)
    out_dir = tmp_path / "out"
    export(out_dir, config=PoolConfig(root=pool_root))
    pool.close()

    from exp_core.dataset import ExperienceDataset

    ds = ExperienceDataset(out_dir)
    rows = list(iter(ds))
    assert len(rows) == 3
    assert all("experience_id" in r for r in rows)

    filtered = ExperienceDataset(out_dir, task_type="csv_analysis")
    assert len(list(filtered)) == 2


def test_dataset_to_dataloader_requires_torch(tmp_path):
    pool_root = tmp_path / "pool"
    pool, _ = _make_pool_with_data(pool_root)
    out_dir = tmp_path / "out"
    export(out_dir, config=PoolConfig(root=pool_root))
    pool.close()

    pytest.importorskip("torch")
    from exp_core.dataset import ExperienceDataset

    ds = ExperienceDataset(out_dir)
    loader = ds.to_dataloader(batch_size=2)
    batches = list(loader)
    # 3 rows, batch_size=2 -> 2 batches.
    assert len(batches) == 2
    flat = [item for batch in batches for item in batch]
    assert len(flat) == 3
