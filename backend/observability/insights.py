"""Insights (PRD O10 to O12): is JAP working for me?

- funnel: per week, new jobs found, how many of those match my preferences now,
  applications started, sent (confirmed by the site) and failed
- sources: per ATS, postings found, matched, sent and failed, the success rate and the
  commonest failure reasons
- spend: this month by purpose and model, and the last six months with cost per sent
  application
Everything is counted from the tables JAP already keeps; nothing new is recorded for it.
Replies from employers are not known to JAP yet, so the funnel ends at Sent.
"""

from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from config import get_settings
from llm.spend import month_start
from schemas.status import FunnelWeek, Insights, MonthSpend, SourceRow, SpendRow

FUNNEL_SQL = """
WITH f AS (
    SELECT date_trunc('week', created_at AT TIME ZONE :tz)::date AS week,
           count(*) AS found, count(*) FILTER (WHERE matched IS NOT NULL) AS matched
    FROM listing WHERE created_at >= :since GROUP BY 1
), p AS (
    SELECT date_trunc('week', created_at AT TIME ZONE :tz)::date AS week, count(*) AS prepared
    FROM application WHERE created_at >= :since GROUP BY 1
), a AS (
    SELECT date_trunc('week', submitted_at AT TIME ZONE :tz)::date AS week, count(*) AS applied
    FROM application WHERE status = 'applied' AND submitted_at >= :since GROUP BY 1
), x AS (
    SELECT date_trunc('week', updated_at AT TIME ZONE :tz)::date AS week, count(*) AS failed
    FROM application WHERE status = 'failed' AND updated_at >= :since GROUP BY 1
)
SELECT week, coalesce(f.found, 0) AS found, coalesce(f.matched, 0) AS matched,
       coalesce(p.prepared, 0) AS prepared, coalesce(a.applied, 0) AS applied,
       coalesce(x.failed, 0) AS failed
FROM f FULL JOIN p USING (week) FULL JOIN a USING (week) FULL JOIN x USING (week)
"""

SOURCES_SQL = """
WITH p AS (
    SELECT source::text AS source, count(*) AS postings,
           count(DISTINCT listing_id) FILTER (
               WHERE listing_id IN (SELECT id FROM listing WHERE matched IS NOT NULL)
           ) AS matched
    FROM posting WHERE first_seen_at >= :since GROUP BY 1
), a AS (
    SELECT po.source::text AS source,
           count(*) FILTER (WHERE ap.status = 'applied') AS applied,
           count(*) FILTER (WHERE ap.status = 'failed') AS failed
    FROM application ap JOIN posting po ON po.id = ap.posting_id
    WHERE ap.created_at >= :since GROUP BY 1
)
SELECT source, coalesce(p.postings, 0) AS postings, coalesce(p.matched, 0) AS matched,
       coalesce(a.applied, 0) AS applied, coalesce(a.failed, 0) AS failed
FROM p FULL JOIN a USING (source) ORDER BY source
"""

FAILURES_SQL = """
SELECT po.source::text AS source, left(coalesce(ap.error, 'unknown'), 90) AS reason,
       count(*) AS n
FROM application ap JOIN posting po ON po.id = ap.posting_id
WHERE ap.status = 'failed' AND ap.created_at >= :since
GROUP BY 1, 2 ORDER BY 1, 3 DESC
"""

SPEND_SQL = """
SELECT purpose, model, count(*) AS calls, coalesce(sum(cost_usd), 0) AS cost
FROM llm_call WHERE created_at >= :since GROUP BY 1, 2 ORDER BY cost DESC, calls DESC
"""

MONTHS_SQL = """
WITH s AS (
    SELECT to_char(created_at AT TIME ZONE :tz, 'YYYY-MM') AS month, sum(cost_usd) AS cost
    FROM llm_call WHERE created_at >= :since GROUP BY 1
), a AS (
    SELECT to_char(submitted_at AT TIME ZONE :tz, 'YYYY-MM') AS month, count(*) AS applied
    FROM application WHERE status = 'applied' AND submitted_at >= :since GROUP BY 1
)
SELECT month, coalesce(s.cost, 0) AS cost, coalesce(a.applied, 0) AS applied
FROM s FULL JOIN a USING (month)
"""


def _monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _months_back(start: datetime, n: int) -> datetime:
    y, m = start.year, start.month - n
    while m <= 0:
        y, m = y - 1, m + 12
    return start.replace(year=y, month=m)


async def _rows(session: AsyncSession, sql: str, **params: Any) -> list[Any]:
    return list(await session.execute(text(sql), params))


async def insights(session: AsyncSession, weeks: int = 8, now: datetime | None = None) -> Insights:
    tz = get_settings().jap_tz
    now = now or datetime.now(UTC)
    first = _monday(now.astimezone(ZoneInfo(tz)).date()) - timedelta(weeks=weeks - 1)
    since = datetime.combine(first, datetime.min.time(), tzinfo=ZoneInfo(tz))

    by_week = {r.week: r for r in await _rows(session, FUNNEL_SQL, tz=tz, since=since)}
    funnel: list[FunnelWeek] = []
    for i in range(weeks):
        wk = first + timedelta(weeks=i)
        r = by_week.get(wk)
        funnel.append(
            FunnelWeek(
                week=wk.isoformat(),
                found=int(r.found) if r else 0,
                matched=int(r.matched) if r else 0,
                prepared=int(r.prepared) if r else 0,
                applied=int(r.applied) if r else 0,
                failed=int(r.failed) if r else 0,
            )
        )
    totals = FunnelWeek(
        week="total",
        found=sum(w.found for w in funnel),
        matched=sum(w.matched for w in funnel),
        prepared=sum(w.prepared for w in funnel),
        applied=sum(w.applied for w in funnel),
        failed=sum(w.failed for w in funnel),
    )

    reasons: dict[str, list[str]] = {}
    for r in await _rows(session, FAILURES_SQL, since=since):
        if len(reasons.setdefault(r.source, [])) < 3:
            reasons[r.source].append(f"{r.reason} ({r.n})")
    sources = []
    for r in await _rows(session, SOURCES_SQL, since=since):
        done = int(r.applied) + int(r.failed)
        sources.append(
            SourceRow(
                source=r.source,
                postings=int(r.postings),
                matched=int(r.matched),
                applied=int(r.applied),
                failed=int(r.failed),
                success_rate=round(int(r.applied) / done, 3) if done else None,
                top_failures=reasons.get(r.source, []),
            )
        )

    month0 = month_start(tz)
    spend = [
        SpendRow(purpose=r.purpose, model=r.model, calls=int(r.calls), cost_usd=float(r.cost))
        for r in await _rows(session, SPEND_SQL, since=month0)
    ]
    six = _months_back(month0, 5)
    by_month = {r.month: r for r in await _rows(session, MONTHS_SQL, tz=tz, since=six)}
    months = []
    for i in range(6):
        key = _months_back(month0, 5 - i).strftime("%Y-%m")
        r = by_month.get(key)
        cost = float(r.cost) if r else 0.0
        applied = int(r.applied) if r else 0
        months.append(
            MonthSpend(
                month=key,
                cost_usd=round(cost, 4),
                applied=applied,
                per_application_usd=round(cost / applied, 4) if applied else None,
            )
        )
    return Insights(
        weeks=weeks,
        funnel=funnel,
        totals=totals,
        sources=sources,
        spend_this_month=spend,
        months=months,
    )
