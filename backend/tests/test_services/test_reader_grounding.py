"""Math source claims require literal page-text evidence, never just a page citation."""

import json
import re

import pytest

from app.services.reader_grounding import ground_math_response


QUOTE = "The token embedding layer is implemented by a one-dimensional convolution."
FREEFORM = "The position index must start at zero and be an integer [P4]."


def ground(quotes, *, excerpt=QUOTE, source_notes=FREEFORM, teaching_notes=""):
    content = json.dumps({"source_quotes": quotes, "source_notes": source_notes,
                          "teaching_notes": teaching_notes, "exercise": ""})
    return ground_math_response(content, excerpt, 4, source_notes, teaching_notes)


def normalize(value):
    return re.sub(r"\s+", " ", value).strip()


def test_literal_evidence_cannot_certify_adjacent_freeform_interpretation():
    source, teaching, check = ground([QUOTE])
    assert QUOTE in source and "[P4]" in source
    assert "start at zero" not in source and "integer" not in source
    assert "start at zero" in teaching and "未核实" in teaching
    assert not re.search(r"\[[PS]\d+\]", teaching, re.I)
    assert check.policy == "verbatim_page_text"
    assert check.matched_quotes == 1 and check.rejected_quotes == 0
    assert check.moved_source_notes is True and check.detail


def test_each_quote_is_checked_independently_and_rejected_claim_never_enters_source():
    invented = "The author defines every position index to be an even integer."
    source, _, check = ground([QUOTE, invented])
    assert QUOTE in source and invented not in source
    assert check.matched_quotes == check.rejected_quotes == 1
    assert check.detail


def test_only_the_current_sent_excerpt_is_allowed_not_model_output_or_another_page():
    other_page = "The training loss is evaluated only on the unobserved values."
    source, teaching, check = ground([other_page], source_notes=other_page + " [P4]")
    assert source == "" and other_page in teaching
    assert check.matched_quotes == 0 and check.rejected_quotes == 1


def test_whitespace_wrapping_is_allowed_without_erasing_word_boundaries():
    quote = "The token embedding\nlayer is implemented by a one-dimensional convolution."
    excerpt = "The token embedding  layer\nis implemented by a one-dimensional convolution."
    source, _, check = ground([quote], excerpt=excerpt)
    assert check.matched_quotes == 1 and check.rejected_quotes == 0
    assert normalize(QUOTE) in normalize(source)


@pytest.mark.parametrize("actual,claimed", [
    ("The page explicitly writes x² + y² in the displayed equation.",
     "The page explicitly writes x2 + y2 in the displayed equation."),
    ("The page explicitly writes 𝐱 as the variable in the displayed equation.",
     "The page explicitly writes x as the variable in the displayed equation."),
    ("The page explicitly writes α + β in the displayed equation.",
     "The page explicitly writes a + b in the displayed equation."),
    ("The page explicitly writes x − y in the displayed equation.",
     "The page explicitly writes x - y in the displayed equation."),
    ("The two input variables are a b according to this text.",
     "The two input variables are ab according to this text."),
])
def test_math_notation_and_token_boundaries_are_not_normalized_away(actual, claimed):
    source, _, check = ground([claimed], excerpt=actual)
    assert source == "" and check.matched_quotes == 0 and check.rejected_quotes == 1


@pytest.mark.parametrize("length", [12, 800])
def test_quote_length_endpoints_are_accepted(length):
    quote = "x" * length
    source, _, check = ground([quote], excerpt=quote, source_notes="")
    assert quote in source and "[P4]" in source
    assert check.matched_quotes == 1 and check.rejected_quotes == 0
    assert check.moved_source_notes is False


@pytest.mark.parametrize("quote", ["x" * 11, "x" * 801, "", " " * 20, "x" + " " * 20 + "y"])
def test_short_empty_or_long_quotes_are_not_upgraded_even_if_they_occur(quote):
    source, _, check = ground([quote], excerpt=quote + " " + QUOTE)
    assert source == "" and check.matched_quotes == 0 and check.rejected_quotes == 1


def test_malformed_quote_items_do_not_remove_valid_evidence_or_get_coerced():
    source, _, check = ground([QUOTE, None, 42, True, {"quote": QUOTE}])
    assert QUOTE in source and check.matched_quotes == 1
    assert check.rejected_quotes == 4


def test_missing_quotes_demotes_legacy_source_notes_without_guessing_verification():
    content = json.dumps({"source_notes": FREEFORM, "teaching_notes": "More context [S9]."})
    source, teaching, check = ground_math_response(content, QUOTE, 4, FREEFORM, "More context [S9].")
    assert source == "" and "start at zero" in teaching
    assert "More context" in teaching and "未核实" in teaching
    assert not re.search(r"\[[PS]\d+\]", teaching, re.I)
    assert check.matched_quotes == 0 and check.moved_source_notes is True


@pytest.mark.parametrize("content", ["not JSON", "[]", '{"source_quotes": null}',
                                       '{"source_quotes": "not a list"}'])
def test_unusable_model_structure_never_preserves_freeform_source(content):
    source, teaching, check = ground_math_response(content, QUOTE, 4, FREEFORM, "Teaching [P4].")
    assert source == "" and "start at zero" in teaching
    assert check.matched_quotes == 0 and check.moved_source_notes is True
    assert not re.search(r"\[[PS]\d+\]", teaching, re.I)


def test_scanned_page_cannot_auto_verify_a_visual_transcription():
    candidate = "PE(pos, 2i) = sin(pos / (10000 ** (2i / d_model)))"
    source, teaching, check = ground([candidate], excerpt="", source_notes=candidate + " [P4]")
    assert source == "" and candidate in teaching
    assert check.matched_quotes == 0 and check.rejected_quotes == 1
    assert "未核实" in teaching


def test_source_page_label_is_supplied_by_server_not_model_source_prose():
    content = json.dumps({"source_quotes": [QUOTE]})
    source, teaching, check = ground_math_response(content, QUOTE, 19, "An unsupported explanation [P2].", "")
    assert "[P19]" in source and "[P2]" not in source
    assert "[P2]" not in teaching and check.moved_source_notes is True


def test_empty_legacy_source_does_not_claim_that_a_demotion_occurred():
    source, teaching, check = ground([], source_notes="", teaching_notes="Only a learning suggestion [S1].")
    assert source == "" and "Only a learning suggestion" in teaching
    assert "[S1]" not in teaching and check.moved_source_notes is False


def test_demoted_math_drafts_remove_lowercase_as_well_as_uppercase_page_citations():
    source, teaching, check = ground([], source_notes="A model interpretation [p 4].",
                                    teaching_notes="An explanatory claim [s9] and another [P4].")
    assert source == "" and check.moved_source_notes is True
    assert not re.search(r"\[[PS]\s*\d+\]", teaching, re.I)


def test_more_than_six_quotes_are_rejected_as_an_over_budget_payload():
    quotes = [f"This is distinct original evidence sentence number {index}." for index in range(7)]
    source, _, check = ground(quotes, excerpt="\n".join(quotes), source_notes="")
    assert source == "" and check.matched_quotes == 0
    assert check.rejected_quotes >= 1 and check.detail
