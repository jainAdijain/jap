import pytest

from pipeline.canonical import canonical_role, clean_company, seniority_of


@pytest.mark.parametrize(
    ("title", "locations", "role"),
    [
        ("Sr. Backend Engineer (Remote)", [], "senior backend engineer"),
        ("Senior Backend Engineer - Bangalore", ["Bangalore, India"], "senior backend engineer"),
        ("Senior Backend Engineer | Remote", [], "senior backend engineer"),
        ("Software Engineer, Backend (R-107406)", [], "software engineer backend"),
        ("SWE II", [], "software engineer ii"),
        ("Staff ML Engineer", [], "staff machine learning engineer"),
        ("Product Designer - Remote, US", [], "product designer"),
        ("Junior Backend Engineer", [], "junior backend engineer"),
        ("Senior Frontend Engineer", [], "senior frontend engineer"),
        ("Backend Engineer (Python)", [], "backend engineer python"),  # a specialization
        ("Data Analyst (Noida)", ["Noida, India"], "data analyst"),
        ("Senior Data Engineer [Hybrid]", [], "senior data engineer"),
        ("Solutions Engineer - EMEA", [], "solutions engineer"),
    ],
)
def test_canonical_role(title: str, locations: list[str], role: str) -> None:
    assert canonical_role(title, locations) == role


@pytest.mark.parametrize(
    ("name", "clean"),
    [
        ("Acme Technologies Inc.", "acme"),
        ("ACME Tech", "acme"),
        ("Acme", "acme"),
        ("Acme Labs Pvt Ltd", "acme"),
        ("Labs", "labs"),  # never stripped to nothing
        ("Procter & Gamble", "procter and gamble"),
    ],
)
def test_clean_company(name: str, clean: str) -> None:
    assert clean_company(name) == clean


def test_seniority_is_part_of_the_role() -> None:
    assert seniority_of("senior backend engineer") != seniority_of("junior backend engineer")
    assert seniority_of("backend engineer") == frozenset()
