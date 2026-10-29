"""Inngest functions, one file each (run_pipeline.py, fetch_board.py, apply_listing.py, ...).

Phase 0 has only ping, so the app syncs with the dev server; the pipeline functions
are added from Phase 1 on.
"""

from typing import Any

import inngest

from functions.client import inngest_client
from functions.ping import ping

ALL_FUNCTIONS: list[inngest.Function[Any]] = [ping]

__all__ = ["ALL_FUNCTIONS", "inngest_client"]
