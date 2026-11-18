"""Word overlap keeps the short all-caps terms that tell two requests apart."""

import pytest

from meeting_assistant.textmatch import content_words, same_words


def test_keeps_short_all_caps_terms():
    assert content_words("Export reports to CSV via the API") == ["export", "report", "csv", "api"]
    assert content_words("Expose two APIs") == ["expose", "api"]


def test_still_drops_short_lowercase_words_and_numbers():
    assert content_words("Add it to my 10 dashboards") == ["dashboard"]


@pytest.mark.parametrize("a,b", [
    ("Export reports to CSV", "Export reports as PDF"),
    ("SSO login", "API login"),
])
def test_requests_differing_only_in_an_acronym_are_not_a_sure_match(a, b):
    assert same_words(a, b) < 0.8


def test_same_acronym_still_matches():
    assert same_words("Export reports to CSV", "CSV export of the reports") == 1.0
