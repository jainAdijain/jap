import pytest

from database import seed


def test_seed_files_parse() -> None:
    assert seed.check_companies(seed.load("companies.yaml")) >= 0
    assert seed.check_skills(seed.load("skills.yaml")) > 0


def test_companies_rejects_unknown_ats() -> None:
    with pytest.raises(ValueError):
        seed.check_companies({"workday": [{"token": "x", "name": "X"}]})
