
from src.job_market.transformations.extract_skills import (
    extract_skills,
    load_skills,
)


SKILLS = load_skills()


def test_extract_multiple_skills():
    text = "We need Python, AWS, Docker and SQL experience."
    result = extract_skills(text, SKILLS)

    assert {"Python", "AWS", "Docker", "SQL"}.issubset(result)


def test_case_insensitive():
    result = extract_skills("PYTHON and aws", SKILLS)

    assert "Python" in result
    assert "AWS" in result


def test_no_skills():
    result = extract_skills(
        "Excellent communication and teamwork.",
        SKILLS,
    )

    assert result == []


def test_empty_description():
    assert extract_skills("", SKILLS) == []


def test_avoid_partial_matches():
    result = extract_skills(
        "We build JavaScript applications.",
        SKILLS,
    )

    assert "Java" not in result
