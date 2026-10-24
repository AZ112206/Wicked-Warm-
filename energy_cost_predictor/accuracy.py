"""Relative-error accuracy reporting for the Energy Cost Predictor.

This module implements Core Math section 6 (Relative error and accuracy) from
the design document. Given a predicted annual cost and a Reference_House known
actual annual cost, it reports the relative error as a percentage and whether
the 1% ``Accuracy_Target`` is met (Req 9).

The single public entry point, :func:`compute_accuracy`, is a pure function: it
performs no I/O, imports no providers, and uses only the standard library. When
the actual annual cost is zero the relative error is undefined, so it raises
:class:`AccuracyUncomputableError` and returns no report (Req 9.2).
"""

from __future__ import annotations

from .errors import AccuracyUncomputableError
from .models import AccuracyReport

__all__ = [
    "ACCURACY_TARGET_PCT",
    "compute_accuracy",
]

#: The maximum acceptable relative error between predicted and actual annual
#: cost for the Reference_House, expressed as a percentage (Req 9.4, 9.5). The
#: Accuracy_Target is met iff the reported relative error percentage is at or
#: below this value.
ACCURACY_TARGET_PCT: float = 1.00


def compute_accuracy(predicted: float, actual: float) -> AccuracyReport:
    """Compute the relative-error accuracy report for a reference house.

    Implements Core Math section 6:

    * ``relative_error = abs(predicted - actual) / actual`` (actual > 0)
    * ``relative_error_pct = round(relative_error * 100, 2)`` (Req 9.1, 9.3)
    * ``target_met = relative_error_pct <= ACCURACY_TARGET_PCT`` (Req 9.4, 9.5)

    Args:
        predicted: The predicted annual heating cost.
        actual: The Reference_House known actual annual heating cost. Must be
            non-zero; a zero actual makes the relative error undefined.

    Returns:
        An :class:`AccuracyReport` carrying ``predicted``, ``actual``, the
        relative error as a percentage rounded to 2 decimal places, and whether
        the Accuracy_Target is met.

    Raises:
        AccuracyUncomputableError: If ``actual`` is zero. No Accuracy_Target
            result is reported (Req 9.2).
    """
    if actual == 0:
        raise AccuracyUncomputableError()

    relative_error = abs(predicted - actual) / actual
    relative_error_pct = round(relative_error * 100, 2)
    target_met = relative_error_pct <= ACCURACY_TARGET_PCT

    return AccuracyReport(
        predicted=predicted,
        actual=actual,
        relative_error_pct=relative_error_pct,
        target_met=target_met,
    )
