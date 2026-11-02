"""Logging, observability and analytics (PRD 1.19, O1 to O15).

- context.py: the ids a log line belongs to.
- scrub.py: redaction for log lines and Sentry.
- logs.py: the JSON line format, setup, and the event() helper.
- writer.py: log_event and route_stat rows, written off the request path.
- http.py and inngest_mw.py: one line per request and per Inngest step.
- sentry.py: errors to Sentry's free plan when SENTRY_DSN is set.
- status.py, alerts.py, insights.py, timeline.py: what the Status and Insights pages show,
  and the health alert emails.
"""
