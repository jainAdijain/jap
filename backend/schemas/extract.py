"""What extraction fills on a posting (I3), stored in posting.extracted."""

from typing import Any, Literal

from pydantic import BaseModel, create_model

from schemas.job import Salary

Seniority = Literal[
    "intern", "junior", "mid", "senior", "staff", "principal", "lead", "manager", "unknown"
]
WorkMode = Literal["remote", "hybrid", "onsite", "unknown"]
YesNo = Literal["yes", "no", "unknown"]


class Extracted(BaseModel):
    """posting.extracted. A field the model has not filled yet is absent, not null."""

    skills: list[str] | None = None
    seniority: Seniority | None = None
    work_mode: WorkMode | None = None
    salary: Salary | None = None
    visa_sponsorship: YesNo | None = None
    remote_regions: list[str] | None = None
    cover_letter_asked: bool | None = None
    # Which fields came straight from the ATS rather than the model (I2 before I3).
    direct: list[str] = []


FIELDS = [
    "skills",
    "seniority",
    "work_mode",
    "salary",
    "visa_sponsorship",
    "remote_regions",
    "cover_letter_asked",
]

# The model answers flat fields (no nested objects), one model per set of wanted fields.
_OUTPUT: dict[str, tuple[Any, Any]] = {
    "skills": (list[str], ...),
    "seniority": (Seniority, ...),
    "work_mode": (WorkMode, ...),
    "salary_min": (float | None, ...),
    "salary_max": (float | None, ...),
    "salary_currency": (str | None, ...),
    "salary_period": (Literal["year", "month", "week", "day", "hour"] | None, ...),
    "visa_sponsorship": (YesNo, ...),
    "remote_regions": (list[str], ...),
    "cover_letter_asked": (bool, ...),
}


def output_model(fields: list[str]) -> type[BaseModel]:
    keys: list[str] = []
    for f in fields:
        keys += [k for k in _OUTPUT if k == f or k.startswith(f"{f}_")]
    spec: dict[str, Any] = {k: _OUTPUT[k] for k in keys}
    return create_model("ExtractedFields", **spec)


def from_output(out: BaseModel) -> dict[str, Any]:
    data = out.model_dump()
    result = {k: v for k, v in data.items() if not k.startswith("salary_")}
    if "salary_min" in data:
        has_number = data["salary_min"] is not None or data["salary_max"] is not None
        result["salary"] = (
            Salary(
                min=data["salary_min"],
                max=data["salary_max"],
                currency=data["salary_currency"],
                period=data["salary_period"],
            ).model_dump()
            if has_number
            else None
        )
    return result
