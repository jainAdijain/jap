"""Starts FastAPI and registers the Inngest endpoint at /api/inngest."""

import inngest.fast_api
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api import answers, boards, errors, health, profile, resumes
from config import get_settings
from functions import ALL_FUNCTIONS, inngest_client

settings = get_settings()

app = FastAPI(title="JAP", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)
errors.install(app)
for module in (health, profile, resumes, boards, answers):
    app.include_router(module.router, prefix="/api")

inngest.fast_api.serve(app, inngest_client, ALL_FUNCTIONS, serve_path="/api/inngest")
