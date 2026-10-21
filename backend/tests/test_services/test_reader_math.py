"""Numeric examples are checked locally; formula/provenance claims are never certified."""

import json

import pytest

from app.services.reader_math import calculation_notes, check_calculations


def _checks(example):
    return check_calculations(json.dumps({"calculation_checks": [example]}))


def _example(**overrides):
    return {"metric": "mse", "y_true": [1, 4], "y_pred": [3, 1], **overrides}


@pytest.mark.parametrize("metric,expected", [("mae", 2.5), ("mse", 6.5), ("rmse", 6.5 ** 0.5)])
def test_distinct_metric_definitions_are_computed_not_accepted_from_model(metric, expected):
    result = _checks(_example(metric=metric, claimed_result=expected))[0]
    assert result.computed_result == pytest.approx(expected)
    assert result.status == "matched"
    assert metric.upper() in result.detail
    assert "不证明" in calculation_notes([result])


def test_absolute_error_cannot_pass_as_mean_square_error():
    result = _checks(_example(claimed_result=2.5))[0]
    assert result.status == "mismatch" and result.computed_result == 6.5
    assert "平方差" in result.detail
    assert "数值不一致" in calculation_notes([result])


def test_mask_explicitly_means_included_and_normalizes_by_active_count():
    result = _checks(_example(mask=[0, 1], claimed_result=9))[0]
    assert result.status == "matched" and result.computed_result == 9
    assert result.mask == [0, 1]
    assert "参与 1 个位置" in result.detail


def test_no_claim_is_computed_not_labelled_a_match_and_numeric_tolerance_is_explicit():
    result = _checks(_example(y_true=[0, 0, 0], y_pred=[0, 0, 1]))[0]
    assert result.status == "computed" and result.claimed_result is None
    assert result.computed_result == pytest.approx(1 / 3)
    assert "10^-9" in result.detail


@pytest.mark.parametrize("changes", [
    {"metric": "code"}, {"metric": ["mse"]},
    {"y_true": []}, {"y_true": [1]}, {"y_pred": [0] * 51},
    {"y_true": [True, 1]}, {"y_true": ["1", 2]},
    {"y_pred": [float("nan"), 1]}, {"y_pred": [float("inf"), 1]},
    {"y_pred": [1e10, 1]}, {"y_pred": [10 ** 500, 1]},
    {"mask": [0, 0]}, {"mask": [1]}, {"mask": [1, 2]},
    {"mask": [True, 0]}, {"mask": [1.0, 0]},
    {"claimed_result": True}, {"claimed_result": "6.5"},
    {"claimed_result": float("nan")}, {"claimed_result": 1e21},
])
def test_unsupported_numbers_or_shapes_never_get_a_computed_result(changes):
    result = _checks(_example(**changes))[0]
    assert result.status == "unsupported" and result.computed_result is None
    assert result.y_true == result.y_pred == []


@pytest.mark.parametrize("content", ["invalid JSON", "{}", "[]", '{"calculation_checks": null}'])
def test_missing_or_unstructured_examples_do_not_add_forced_exercises(content):
    assert check_calculations(content) == []


@pytest.mark.parametrize("examples", ["run_code()", [_example()] * 4])
def test_examples_are_bounded_and_no_alternate_evaluator_is_used(examples):
    checks = check_calculations(json.dumps({"calculation_checks": examples}))
    assert len(checks) == 1 and checks[0].status == "unsupported"


def test_fenced_json_supported_and_arbitrary_program_fields_are_not_run():
    content = json.dumps({"calculation_checks": [_example(code="raise RuntimeError()") ]})
    result = check_calculations("```json\n" + content + "\n```")[0]
    assert result.computed_result == 6.5
