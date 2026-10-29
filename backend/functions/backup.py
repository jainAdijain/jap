"""daily-backup (Phase 8): a fresh pg_dump in data/backups/ every day.

An hourly cron in JAP_TZ dumps when today has no backup yet, so the backup still happens on
a day the Mac was asleep at the usual hour. backup/requested (Back up now on the Status
card) dumps at once.
"""

from typing import Any

import inngest

from config import get_settings
from functions.client import inngest_client
from pipeline import backup


async def backup_step(force: bool) -> dict[str, Any]:
    if not force and backup.has_today():
        return {"skipped": "today's backup exists"}
    return (await backup.run_backup()).as_dict()  # BackupFailed is retried, then next hour


@inngest_client.create_function(
    fn_id="daily-backup",
    trigger=[
        inngest.TriggerCron(cron=f"TZ={get_settings().jap_tz} 15 * * * *"),
        inngest.TriggerEvent(event="backup/requested"),
    ],
    concurrency=[inngest.Concurrency(limit=1)],
    retries=2,
)
async def daily_backup(ctx: inngest.Context) -> dict[str, Any]:
    force = ctx.event.name == "backup/requested"
    return await ctx.step.run("pg-dump", backup_step, force)
