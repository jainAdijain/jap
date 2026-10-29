from datetime import UTC, datetime

from schemas.job import ATS, Job, Salary


def test_job_round_trips_through_json() -> None:
    job = Job(
        source=ATS.ashby,
        external_id="abc-123",
        company_name="Acme",
        title="Backend Engineer",
        locations=["Remote - India", "Bengaluru"],
        department="Engineering",
        employment_type="FullTime",
        posted_at=datetime(2026, 10, 1, tzinfo=UTC),
        posting_url="https://jobs.ashbyhq.com/acme/abc-123",  # type: ignore[arg-type]
        apply_url="https://jobs.ashbyhq.com/acme/abc-123/application",  # type: ignore[arg-type]
        requisition_id="R-1",
        description_html="<p>Python</p>",
        raw_payload={"id": "abc-123"},
        salary=Salary(min=40, max=60, currency="INR", period="year"),
    )
    assert Job.model_validate_json(job.model_dump_json()) == job
