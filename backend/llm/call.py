"""llm.call(): the one way to reach a chat model.

Redact every input, guard the outgoing body, stop at the monthly spend cap, call
OpenRouter, log the call (a hash of the input, never the text), validate the JSON
against the output model (one retry), and return it. Restoring placeholders in the
output is left to the caller, which knows which fields may carry them.
"""

import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from config import get_settings
from database.models import LlmCall
from llm import client
from llm.redact import PersonalDataBlocked, Sensitive, guard, redact
from llm.spend import month_spend

PROMPTS = Path(__file__).parent / "prompts"
Out = TypeVar("Out", bound=BaseModel)
Slot = Literal["extract", "write"]


class SpendCapReached(Exception):
    """This month's model spend has reached JAP_MONTHLY_COST_CAP_USD."""


@dataclass
class Prompt:
    version: str
    system: str
    user: str


def load_prompt(purpose: str, version: str = "v1") -> Prompt:
    text = (PROMPTS / f"{purpose}.{version}.md").read_text()
    system, user = text.split("\nUSER\n", 1)
    return Prompt(
        version=version, system=system.removeprefix("SYSTEM\n").strip(), user=user.strip()
    )


def _schema(model: type[BaseModel]) -> dict[str, Any]:
    schema = model.model_json_schema()
    schema["additionalProperties"] = False
    schema["required"] = list(schema.get("properties", {}))
    return schema


@dataclass
class CallResult(Generic[Out]):
    output: Out
    model_input: str  # the redacted user message, exactly as sent
    links: dict[str, str]


async def call(
    session: AsyncSession,
    purpose: str,
    inputs: dict[str, str],
    output: type[Out],
    *,
    sensitive: Sensitive,
    slot: Slot = "extract",
    temperature: float = 0.0,
    generic_redact: frozenset[str] = frozenset(),
) -> CallResult[Out]:
    settings = get_settings()
    model = settings.jap_model_extract if slot == "extract" else settings.jap_model_write
    prompt = load_prompt(purpose)

    links: dict[str, str] = {}
    redacted: dict[str, str] = {}
    for key, value in inputs.items():
        r = redact(value, sensitive, generic=key in generic_redact)
        redacted[key] = r.text
        links.update(r.links)
    user = prompt.user.format(**redacted)
    body = client.build_body(model, prompt.system, user, _schema(output), temperature)
    body_text = json.dumps(body, ensure_ascii=False)
    input_sha = hashlib.sha256(body_text.encode()).hexdigest()

    def log(**fields: Any) -> LlmCall:
        row = LlmCall(
            purpose=purpose,
            model=model,
            prompt_version=prompt.version,
            input_sha256=input_sha,
            **fields,
        )
        session.add(row)
        return row

    try:
        guard(body_text, sensitive)
    except PersonalDataBlocked as exc:
        log(blocked=True, error=str(exc))
        await session.commit()
        raise

    spend = await month_spend(session)
    if spend.reached:
        log(error="Spend cap reached")
        await session.commit()
        raise SpendCapReached(
            f"Spend cap reached: ${spend.month_usd:.2f} of ${spend.cap_usd:.2f} this month"
        )

    last_error: Exception | None = None
    for _attempt in range(2):  # one retry on invalid output
        started = time.monotonic()
        try:
            result = await client.chat(settings.openrouter_api_key, body)
        except client.ModelCallError as exc:
            log(error=str(exc), latency_ms=int((time.monotonic() - started) * 1000))
            await session.commit()
            raise
        log(
            tokens_in=result.tokens_in,
            tokens_out=result.tokens_out,
            cost_usd=result.cost_usd,
            latency_ms=int((time.monotonic() - started) * 1000),
        )
        await session.commit()
        try:
            parsed = output.model_validate_json(result.content)
            return CallResult(output=parsed, model_input=user, links=links)
        except ValidationError as exc:
            last_error = exc
    raise client.ModelCallError(f"Model output did not match the schema: {last_error}")
