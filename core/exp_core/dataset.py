"""PyTorch-friendly Dataset for the exported Parquet experience pool.

The dataset uses :mod:`pyarrow.dataset` for partition discovery so we get
predicate pushdown on ``task_type`` and ``date`` partition keys without
loading every file. Torch is imported lazily inside :meth:`to_dataloader`
so that running :mod:`exp_core.export` and reading parquet directly does
not require torch.

Example::

    from exp_core.dataset import ExperienceDataset

    ds = ExperienceDataset("./data", task_type="csv_analysis")
    for row in ds:
        print(row["experience_id"], row["r_outcome_discrete"])

    loader = ds.to_dataloader(batch_size=32)  # requires torch installed
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterator

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.dataset as pads


def _to_date_str(v: str | date | datetime | None) -> str | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    return str(v)


class ExperienceDataset:
    """Iterable dataset over a partitioned Parquet export.

    Args:
        root: Directory produced by :func:`exp_core.export.export`.
        task_type: Optional partition filter on ``task_type``.
        since_date: Optional inclusive lower bound on the ``date`` partition.
        until_date: Optional inclusive upper bound on the ``date`` partition.
        columns: Optional subset of columns to load.
    """

    def __init__(
        self,
        root: str | Path,
        *,
        task_type: str | None = None,
        since_date: str | date | datetime | None = None,
        until_date: str | date | datetime | None = None,
        columns: list[str] | None = None,
    ) -> None:
        self.root = Path(root)
        self.task_type = task_type
        self.since_date = _to_date_str(since_date)
        self.until_date = _to_date_str(until_date)
        self.columns = columns
        self._dataset: pads.Dataset | None = None

    # ------------------------------------------------------------------
    # pyarrow.dataset glue
    # ------------------------------------------------------------------
    def _build_dataset(self) -> pads.Dataset:
        if self._dataset is not None:
            return self._dataset
        if not self.root.exists():
            # Empty stand-in dataset; iteration will yield nothing.
            self._dataset = pads.dataset([], schema=pa.schema([]))
            return self._dataset
        partitioning = pads.HivePartitioning.discover()
        self._dataset = pads.dataset(
            str(self.root),
            format="parquet",
            partitioning=partitioning,
        )
        return self._dataset

    def _filter(self) -> pc.Expression | None:
        ds = self._build_dataset()
        names = set(ds.schema.names)
        exprs: list[pc.Expression] = []
        if self.task_type and "task_type" in names:
            exprs.append(pc.field("task_type") == self.task_type)
        if "date" in names:
            if self.since_date:
                exprs.append(pc.field("date") >= self.since_date)
            if self.until_date:
                exprs.append(pc.field("date") <= self.until_date)
        if not exprs:
            return None
        out = exprs[0]
        for e in exprs[1:]:
            out = out & e
        return out

    def to_table(self) -> pa.Table:
        """Materialize the filtered dataset into a single Arrow table."""
        ds = self._build_dataset()
        if len(ds.schema.names) == 0:
            return pa.table({})
        return ds.to_table(columns=self.columns, filter=self._filter())

    # ------------------------------------------------------------------
    # Iteration
    # ------------------------------------------------------------------
    def __iter__(self) -> Iterator[dict[str, Any]]:
        table = self.to_table()
        for batch in table.to_batches():
            for row in batch.to_pylist():
                yield row

    def __len__(self) -> int:
        return self.to_table().num_rows

    # ------------------------------------------------------------------
    # Torch helper (lazy import)
    # ------------------------------------------------------------------
    def to_dataloader(self, batch_size: int = 32, **dataloader_kwargs: Any):
        """Wrap this dataset in a :class:`torch.utils.data.DataLoader`.

        Torch is only imported here, so :mod:`exp_core.export` itself does
        not require torch as a dependency.
        """
        try:
            import torch  # noqa: F401
            from torch.utils.data import DataLoader, Dataset as TorchDataset
        except ImportError as exc:  # pragma: no cover - exercised by skipif test
            raise ImportError(
                "torch is required for to_dataloader(); install it with "
                "`pip install torch`"
            ) from exc

        records = list(iter(self))

        class _MaterializedDataset(TorchDataset):
            def __len__(self) -> int:
                return len(records)

            def __getitem__(self, idx: int) -> dict[str, Any]:
                return records[idx]

        # Default to a collate_fn that returns the list of dicts unchanged so
        # downstream code can pick its own tensorization strategy.
        dataloader_kwargs.setdefault("collate_fn", lambda batch: batch)
        return DataLoader(_MaterializedDataset(), batch_size=batch_size, **dataloader_kwargs)


__all__ = ["ExperienceDataset"]
