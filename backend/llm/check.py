"""make llm-check: one tiny call to each OpenRouter model, through llm.call().

Sends no personal data. Prints the reply time and cost of each, so you can see both
model ids answer and how fast the writer is.
"""

import asyncio
import sys
import time

from pydantic import BaseModel

from config import get_settings
from database.session import get_sessionmaker
from llm import Sensitive, call
from llm.client import ModelCallError


class Pong(BaseModel):
    ok: bool
    reply: str


async def main() -> int:
    settings = get_settings()
    failed = False
    async with get_sessionmaker()() as session:
        for slot, model in (
            ("extract", settings.jap_model_extract),
            ("write", settings.jap_model_write),
        ):
            started = time.monotonic()
            try:
                result = await call(
                    session,
                    "check",
                    {"nonce": str(int(started))},
                    Pong,
                    sensitive=Sensitive(),
                    slot=slot,  # type: ignore[arg-type]
                )
                print(f"OK   {model}: {result.output.reply!r} in {time.monotonic() - started:.1f}s")
            except ModelCallError as exc:
                failed = True
                print(f"FAIL {model}: {exc}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
