"""ping: a smoke test that the dev server can call the app.

Inngest refuses to sync an app with no functions, so this one keeps Phase 0 visible
in the Inngest UI. Send a system/ping event from the UI's Send event button to run it.
"""

import inngest

from functions.client import inngest_client


@inngest_client.create_function(
    fn_id="ping",
    trigger=inngest.TriggerEvent(event="system/ping"),
)
async def ping(ctx: inngest.Context) -> dict[str, str]:
    return {"reply": "pong"}
