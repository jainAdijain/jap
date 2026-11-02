"""Starts FastAPI and registers the Inngest endpoint at /api/inngest."""

import inngest.fast_api
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api import (
    answers,
    applications,
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
from config import get_settings
from functions import ALL_FUNCTIONS, inngest_client
from observability.http import RequestLog
from observability.logs import setup_logging
from observability.sentry import setup_sentry

settings = get_settings()
setup_logging()
setup_sentry()

app = FastAPI(title="JAP", version="0.14.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID"],
)
app.add_middleware(RequestLog)  # outermost of ours: times CORS and the error handlers too
errors.install(app)
for module in (
    health, profile, resumes, boards, answers, runs, postings, listings, duplicates, applications,
    ops, insights,
):  # fmt: skip
    app.include_router(module.router, prefix="/api")

inngest.fast_api.serve(app, inngest_client, ALL_FUNCTIONS, serve_path="/api/inngest")
