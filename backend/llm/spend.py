"""This month's model spend, measured against JAP_MONTHLY_COST_CAP_USD."""

from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from config import get_settings
from database.models import LlmCall
from schemas.profile import Spend


def month_start(tz: str) -> datetime:
    now = datetime.now(ZoneInfo(tz))
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


async def month_spend(session: AsyncSession) -> Spend:
    settings = get_settings()
    total = await session.scalar(
        select(func.coalesce(func.sum(LlmCall.cost_usd), 0)).where(
            LlmCall.created_at >= month_start(settings.jap_tz)
        )
    )
    spent = float(total or 0)
    cap = settings.jap_monthly_cost_cap_usd
    return Spend(month_usd=round(spent, 4), cap_usd=cap, reached=spent >= cap)
