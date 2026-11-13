"""Starts FastAPI and registers the Inngest endpoint at /api/inngest (internal only, PRD S8)."""

import inngest.fast_api
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api import (
    answers,
    applications,
    auth,
    boards,
    duplicates,
    errors,
    health,
    insights,
    listings,
    ops,
    postings,
    profile,
    resumes,
    runs,
)
from auth import guards
from auth.deps import current_user
from config import get_settings
from functions import ALL_FUNCTIONS, inngest_client
from observability.http import RequestLog
from observability.logs import setup_logging
from observability.sentry import setup_sentry

settings = get_settings()
setup_logging()
setup_sentry()

app = FastAPI(title="JAP", version="0.17.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID"],
)
app.add_middleware(RequestLog)  # outermost of ours: times CORS and the error handlers too
errors.install(app)
guards.install(app)
# Open without a session: /api/health (service status only) and the sign-in routes (PRD S6).
app.include_router(health.router, prefix="/api")
app.include_router(auth.router, prefix="/api")
# Everything else needs a signed-in session (PRD S6, S7).
for module in (
    profile, resumes, boards, answers, runs, postings, listings, duplicates, applications, ops,
    insights,
):  # fmt: skip
    app.include_router(module.router, prefix="/api", dependencies=[Depends(current_user)])

inngest.fast_api.serve(app, inngest_client, ALL_FUNCTIONS, serve_path="/api/inngest")
