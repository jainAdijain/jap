"""Inngest functions, one file each (run_pipeline.py, fetch_board.py, apply_listing.py, ...).

ping is a smoke test; the pipeline functions are added phase by phase.
"""

from typing import Any

import inngest

from functions.client import inngest_client
from functions.parse_resume import parse_resume
from functions.ping import ping

ALL_FUNCTIONS: list[inngest.Function[Any]] = [ping, parse_resume]

__all__ = ["ALL_FUNCTIONS", "inngest_client"]
