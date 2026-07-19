"""Shared test-process limits for native numerical runtimes."""

from __future__ import annotations

import os


# CI and notebook containers often expose the host CPU count while granting a
# much smaller CPU quota. Arrow/Torch/BLAS otherwise create dozens of workers,
# making tiny export tests slower and far more memory hungry.
for variable in (
    "ARROW_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
):
    os.environ.setdefault(variable, "1")
