"""Tests for :mod:`energy_cost_predictor.accuracy`.

Covers Core Math section 6 / Requirement 9: relative-error computation, the
2-decimal-place percentage, the 1% Accuracy_Target met/not-met decision, and the
undefined-when-actual-is-zero error (Req 9.1-9.5).

Includes unit tests for concrete examples and edge cases plus Property 9, which
asserts the universal relationship between inputs and the reported report.
"""

from __future__ import annotations

import math

import pytest
from hypothesis import given, strategies as st

from energy_cost_predictor.accuracy import ACCURACY_TARGET_PCT, compute_accuracy
from energy_cost_predictor.errors import AccuracyUncomputableError
from energy_cost_predictor.models import AccuracyReport


# ---------------------------------------------------------------------------
# Unit tests: concrete examples and edge cases
# ---------------------------------------------------------------------------


def test_exact_prediction_has_zero_error_and_meets_target():
    report = compute_accuracy(predicted=1000.0, actual=1000.0)
    assert isinstance(report, AccuracyReport)
    assert report.predicted == 1000.0
    assert report.actual == 1000.0
    assert report.relative_error_pct == 0.0
    assert report.target_met is True


def test_error_exactly_at_target_is_met():
    # 1% under: 1010 vs 1000 -> 1.00% relative error, at the target boundary.
    report = compute_accuracy(predicted=1010.0, actual=1000.0)
    assert report.relative_error_pct == 1.00
    assert report.target_met is True


def test_error_just_above_target_is_not_met():
    # 1020 vs 1000 -> 2.00% relative error, above the 1% target.
    report = compute_accuracy(predicted=1020.0, actual=1000.0)
    assert report.relative_error_pct == 2.00
    assert report.target_met is False


def test_relative_error_is_absolute_so_under_prediction_is_positive():
    # 990 vs 1000 -> abs difference 10 -> 1.00% (not negative).
    report = compute_accuracy(predicted=990.0, actual=1000.0)
    assert report.relative_error_pct == 1.00
    assert report.target_met is True


def test_percentage_is_rounded_to_two_decimal_places():
    # 1000.555 vs 1000 -> 0.0555% -> rounds to 0.06.
    report = compute_accuracy(predicted=1000.555, actual=1000.0)
    assert report.relative_error_pct == 0.06


def test_zero_actual_raises_uncomputable_error():
    with pytest.raises(AccuracyUncomputableError):
        compute_accuracy(predicted=100.0, actual=0.0)


def test_accuracy_target_constant_is_one_percent():
    assert ACCURACY_TARGET_PCT == 1.00


# ---------------------------------------------------------------------------
# Property 9: Relative error and accuracy target are computed correctly
# Validates: Requirements 9.1, 9.3, 9.4, 9.5
# ---------------------------------------------------------------------------


@given(
    predicted=st.floats(
        min_value=0.0,
        max_value=1_000_000.0,
        allow_nan=False,
        allow_infinity=False,
    ),
    actual=st.floats(
        min_value=0.01,
        max_value=1_000_000.0,
        allow_nan=False,
        allow_infinity=False,
    ),
)
def test_property_relative_error_and_target(predicted, actual):
    """For any predicted cost and actual > 0, the reported relative error
    percentage equals ``round(abs(predicted - actual) / actual * 100, 2)``, is
    non-negative, and the Accuracy_Target is met iff that percentage <= 1.00.

    **Validates: Requirements 9.1, 9.3, 9.4, 9.5**
    """
    report = compute_accuracy(predicted=predicted, actual=actual)

    expected_pct = round(abs(predicted - actual) / actual * 100, 2)
    assert report.relative_error_pct == expected_pct
    assert report.relative_error_pct >= 0.0
    assert report.target_met == (report.relative_error_pct <= ACCURACY_TARGET_PCT)
    assert report.predicted == predicted
    assert report.actual == actual
    assert math.isfinite(report.relative_error_pct)
