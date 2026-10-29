"""Starts FastAPI and registers the Inngest endpoint at /api/inngest."""

import inngest.fast_api
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api import health
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
app.include_router(health.router, prefix="/api")

inngest.fast_api.serve(app, inngest_client, ALL_FUNCTIONS, serve_path="/api/inngest")
