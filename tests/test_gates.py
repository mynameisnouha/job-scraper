"""The C1-German detector: explicit fluent-German demands, decided in code.

The screen passed 222 of 636 full-scored jobs that the full scorer then labelled
C1-fluent, so the phrases the screen prompt already lists are matched here before
any model call. The tests that matter most are the negatives: a German-language ad
that names no level, or names a lower one, must not be thrown out.
"""

import pytest

from scoring.gates import german_c1_match


@pytest.mark.parametrize("text", [
    "Fließende Deutschkenntnisse in Wort und Schrift",
    "Fliessende Deutschkenntnisse",
    "Deutsch verhandlungssicher",
    "Deutsch auf muttersprachlichem Niveau",
    "Sehr gute Deutschkenntnisse",
    "exzellente Deutsch- und Englischkenntnisse",
    "ausgezeichnete Deutschkenntnisse",
    "Excellent German skills required",
    "German (C1) required",
    "Deutsch mindestens C2",
    "fluent German",
    "German (fluent)",
    "Deutsch fließend",
])
def test_c1_demands_match(text):
    assert german_c1_match(text)


@pytest.mark.parametrize("text", [
    "Deutschkenntnisse von Vorteil",
    "Gute Deutschkenntnisse",
    "German B2",
    "Wir sprechen Deutsch und Englisch im Team",
    "Valid C1 driving licence",
    "Fluent English, German is a plus",
    "Sehr gute Englischkenntnisse",
    "English C1, German B1",
    "Englisch verhandlungssicher",
    "",
])
def test_no_c1_demand_no_match(text):
    assert german_c1_match(text) is None


def test_none_description_is_no_match():
    assert german_c1_match(None) is None


def test_returns_the_matched_phrase():
    assert "verhandlungssicher" in german_c1_match("Deutsch verhandlungssicher, Englisch gut").lower()
