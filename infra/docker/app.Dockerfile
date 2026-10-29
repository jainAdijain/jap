# Playwright's Python image ships Chromium and its system libraries.
# Keep this tag in step with the playwright pin in backend/pyproject.toml.
FROM mcr.microsoft.com/playwright/python:v1.64.1-noble

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_BREAK_SYSTEM_PACKAGES=1

WORKDIR /app

# Dependencies first so code changes don't reinstall them.
COPY backend/pyproject.toml ./
RUN pip install --no-cache-dir ".[dev]"

COPY backend/ ./

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
