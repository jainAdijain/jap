"""Work mode and remote regions from what the ATS states (JAP-10, v0.19.1).

A remote job was saved with no work mode and no remote regions whenever extraction did not
run (the OpenRouter key out of credit), even though the ATS said remote: Ashby isRemote or
workplaceType, Lever workplaceType, or a Greenhouse "Remote" location entry. Its Location
condition then failed for anyone matching on Remote regions.

Listings now read these from the stored payload every time they are refreshed
(pipeline/dedup.py work_place). A listing is only refreshed when one of its postings
changes, so this migration works them out once for every stored listing, with the same code,
and then filters the listings again as a profile save would (as 0010 does). It only adds: a
work mode the ATS does not state, or regions set by hand or by the model, are left as they
are, so the three listings patched by hand on 18 Nov keep their values.

Revision ID: 0011_stated_work_mode
Revises: 0010_skills_match
Create Date: 2026-11-18
"""

from collections import defaultdict
from collections.abc import Sequence
from types import SimpleNamespace
from typing import Any

import sqlalchemy as sa
from alembic import op

from pipeline.dedup import work_place
from pipeline.matching import Criteria, refilter_sql

revision: str = "0011_stated_work_mode"
down_revision: str | None = "0010_skills_match"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT p.id, p.listing_id, p.source::text AS source, p.raw_payload, p.locations,"
            " p.extracted, p.state::text AS state, l.primary_posting_id"
            " FROM posting p JOIN listing l ON l.id = p.listing_id"
        )
    ).mappings()
    by_listing: dict[Any, list[SimpleNamespace]] = defaultdict(list)
    primary_of: dict[Any, Any] = {}
    for r in rows:
        by_listing[r["listing_id"]].append(SimpleNamespace(**r))
        primary_of[r["listing_id"]] = r["primary_posting_id"]
    for listing_id, postings in by_listing.items():
        pool = [p for p in postings if p.state == "open"] or postings
        primary = next((p for p in postings if p.id == primary_of[listing_id]), pool[0])
        mode, regions = work_place(primary, pool)
        if mode is None and not regions:
            continue
        # Only adds: a value set by hand or by the model is never cleared here.
        bind.execute(
            sa.text(
                "UPDATE listing SET work_mode = COALESCE(CAST(:mode AS work_mode), work_mode),"
                " remote_regions = remote_regions || ARRAY(SELECT r FROM unnest("
                "   CAST(:regions AS text[])) r WHERE r <> ALL(remote_regions)),"
                " updated_at = now()"
                " WHERE id = :id AND (work_mode IS DISTINCT FROM"
                "   COALESCE(CAST(:mode AS work_mode), work_mode)"
                "   OR NOT remote_regions @> CAST(:regions AS text[]))"
            ),
            {"id": listing_id, "mode": mode, "regions": regions},
        )
    refilter_once()


def refilter_once() -> None:
    """The stored listings filtered again with the saved profile, as 0010 does. A database
    with no profile yet has nothing to filter."""
    bind = op.get_bind()
    profile = (
        bind.execute(
            sa.text(
                "SELECT target_roles, must_have_skills, nice_to_have_skills, locations,"
                " remote_regions, remote_any, match_rule, CAST(work_modes AS text[]) AS work_modes,"
                " excluded_companies, excluded_title_words FROM profile LIMIT 1"
            )
        )
        .mappings()
        .first()
    )
    if profile is None:
        return
    sql, params = refilter_sql(Criteria.of(SimpleNamespace(**profile)))
    bind.execute(sa.text(sql), params)


def downgrade() -> None:
    """Nothing to undo: the columns are unchanged and a refresh writes the same values."""
