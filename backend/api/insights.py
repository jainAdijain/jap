"""GET /api/insights (PRD O10 to O12): the job funnel by week, results by ATS, and AI spend."""

from fastapi import APIRouter, Query

from database.session import Session
from observability.insights import insights as build
from schemas.status import Insights

router = APIRouter(tags=["insights"])


@router.get("/insights", response_model=Insights)
async def insights(session: Session, weeks: int = Query(8, ge=1, le=52)) -> Insights:
    return await build(session, weeks)
