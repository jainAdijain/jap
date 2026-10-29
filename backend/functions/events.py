"""Sending Inngest events from API routes. Routes save, send an event and return."""

from typing import Any

import inngest

from functions.client import inngest_client


class EventNotSent(Exception):
    pass


async def send(name: str, data: dict[str, Any] | None = None) -> None:
    try:
        await inngest_client.send(inngest.Event(name=name, data=data or {}))
    except Exception as exc:  # noqa: BLE001 - surface any failure the same way
        raise EventNotSent(f"Inngest did not take the {name} event: {exc}") from exc
