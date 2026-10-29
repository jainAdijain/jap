"""Inngest functions, one file each (run_pipeline.py, fetch_board.py, apply_listing.py, ...).

ping is a smoke test; the pipeline functions are added phase by phase.
"""

from typing import Any

import inngest

from functions.client import inngest_client
from functions.enrich_posting import enrich_posting
from functions.fetch_board import fetch_board
from functions.parse_resume import parse_resume
from functions.ping import ping
from functions.run_pipeline import run_pipeline
from functions.triggers import daily_schedule, profile_saved

ALL_FUNCTIONS: list[inngest.Function[Any]] = [
    ping,
    parse_resume,
    run_pipeline,
    fetch_board,
    enrich_posting,
    profile_saved,
    daily_schedule,
]

__all__ = ["ALL_FUNCTIONS", "inngest_client"]
