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


if __name__ == "__main__":
    # `make form-check`: start a job from the machine, without going through the signed-in API.
    import asyncio
    import sys

    if len(sys.argv) != 2:
        raise SystemExit("usage: python -m functions.events <event name>")
    asyncio.run(send(sys.argv[1]))
    print(f"Sent {sys.argv[1]}.")
