"""llm.call(): the one way to reach a chat model.

Redact every input, guard the outgoing body, stop at the monthly spend cap, call
OpenRouter, log the call (a hash of the input, never the text), validate the JSON
against the output model (one retry), and return it. The run and application in the log
context (observability/context.py) are written on the llm_call row for the timeline (O8).
From PRD 1.33 (O19) the row also says which posting the call belongs to, who served it,
which attempt it was and how it ended, and the same goes to the trace service when one is
set (observability/trace.py): numbers and names only, never the prompt or the reply.
A reply that wraps the JSON in a code fence or a sentence is accepted: not every provider
behind a model id honours response_format. Restoring placeholders in the output is left
to the caller, which knows which fields may carry them.
"""

import hashlib
import json
import re
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
from observability import context, trace

PROMPTS = Path(__file__).parent / "prompts"
Out = TypeVar("Out", bound=BaseModel)
Slot = Literal["extract", "write"]

# Output caps per purpose, in tokens: room for the JSON the purpose asks for and some
# slack, never the model's whole output limit (llm/client.py build_body).
MAX_TOKENS = {
    "answer": 1_500,
    "check": 200,
    "claim_check": 2_000,
    "cover_letter": 2_000,
    "extract": 4_000,
    "resume_parse": 6_000,
    "same_job": 500,
}
DEFAULT_MAX_TOKENS = 4_000


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


def _json_text(content: str) -> str:
    """The JSON object in a reply, without a ```json fence or words around it."""
    text = content.strip()
    fenced = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL | re.IGNORECASE)
    if fenced:
        text = fenced.group(1)
    start, end = text.find("{"), text.rfind("}")
    return text[start : end + 1] if 0 <= start < end else text


def _slug(provider: str | None) -> str | None:
    """A provider's name as OpenRouter gives it ("Google AI Studio"), as a short identifier."""
    return re.sub(r"[^A-Za-z0-9]+", "-", provider).strip("-").lower()[:40] if provider else None


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
    version: str = "v1",
    max_tokens: int | None = None,
) -> CallResult[Out]:
    settings = get_settings()
    model = settings.jap_model_extract if slot == "extract" else settings.jap_model_write
    prompt = load_prompt(purpose, version)

    links: dict[str, str] = {}
    redacted: dict[str, str] = {}
    for key, value in inputs.items():
        r = redact(value, sensitive, generic=key in generic_redact)
        redacted[key] = r.text
        links.update(r.links)
    user = prompt.user.format(**redacted)
    cap = max_tokens or MAX_TOKENS.get(purpose, DEFAULT_MAX_TOKENS)
    body = client.build_body(model, prompt.system, user, _schema(output), temperature, cap)
    body_text = json.dumps(body, ensure_ascii=False)
    input_sha = hashlib.sha256(body_text.encode()).hexdigest()

    def log(**fields: Any) -> LlmCall:
        row = LlmCall(
            purpose=purpose,
            model=model,
            prompt_version=prompt.version,
            input_sha256=input_sha,
            run_id=context.uuid_of("run_id"),
            application_id=context.uuid_of("application_id"),
            posting_id=context.uuid_of("posting_id"),
            **fields,
        )
        session.add(row)
        return row

    def traced(row: LlmCall, started: float, served: client.ChatResult | None = None) -> None:
        """O19: the call as it ended, for the trace service. One application's calls are
        grouped, else one run's."""
        trace.record(
            f"llm.{purpose}",
            "llm",
            started,
            started + (row.latency_ms or 0) / 1000,
            ok=row.outcome == "valid",
            mode="api",
            harness="none",
            purpose=purpose,
            prompt_version=prompt.version,
            model=model,
            model_served=served.model if served else None,
            provider=_slug(row.provider),
            max_tokens=cap,
            attempt=row.attempt,
            outcome=row.outcome,
            tokens_in=row.tokens_in,
            tokens_out=row.tokens_out,
            tokens_cached=row.tokens_cached,
            tokens_reasoning=row.tokens_reasoning,
            cost_usd=row.cost_usd,
            latency_ms=row.latency_ms,
            run_id=row.run_id,
            application_id=row.application_id,
            posting_id=row.posting_id,
            thread_id=row.application_id or row.run_id,
        )

    try:
        guard(body_text, sensitive)
    except PersonalDataBlocked as exc:
        traced(log(blocked=True, error=str(exc), outcome="blocked"), time.time())
        await session.commit()
        raise

    spend = await month_spend(session)
    if spend.reached:
        traced(log(error="Spend cap reached", outcome="spend_cap"), time.time())
        await session.commit()
        raise SpendCapReached(
            f"Spend cap reached: ${spend.month_usd:.2f} of ${spend.cap_usd:.2f} this month"
        )

    last_error: ValidationError | None = None
    for attempt in (1, 2):  # one retry on invalid output
        started, clock = time.monotonic(), time.time()
        try:
            result = await client.chat(settings.openrouter_api_key, body)
        except client.ModelCallError as exc:
            row = log(
                error=str(exc),
                latency_ms=int((time.monotonic() - started) * 1000),
                attempt=attempt,
                outcome="error",
            )
            traced(row, clock)
            await session.commit()
            raise
        row = log(
            tokens_in=result.tokens_in,
            tokens_out=result.tokens_out,
            cost_usd=result.cost_usd,
            latency_ms=int((time.monotonic() - started) * 1000),
            provider=result.provider,
            tokens_cached=result.tokens_cached,
            tokens_reasoning=result.tokens_reasoning,
            attempt=attempt,
            outcome="valid",
        )
        try:
            parsed = output.model_validate_json(_json_text(result.content))
        except ValidationError as exc:
            last_error = exc
            row.error = "Output did not match the schema"
            row.outcome = "retried" if attempt == 1 else "error"
            traced(row, clock, result)
            await session.commit()
            continue
        traced(row, clock, result)
        await session.commit()
        return CallResult(output=parsed, model_input=user, links=links)
    assert last_error is not None
    first = last_error.errors()[0]
    raise client.ModelCallError(
        f"{model} answered twice without the JSON asked for ({first['msg']})"
    )
