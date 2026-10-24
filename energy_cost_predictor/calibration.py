"""Single-scalar bisection calibration for the Energy Cost Predictor (Req 8).

Calibration tunes the primary :class:`~energy_cost_predictor.models.ModelParameters`
scalar ``demand_scale`` to drive a Reference_House's *predicted* annual heating
cost toward its known *actual* annual cost, within the 1% ``Accuracy_Target``,
in at most 100 iterations (design "Calibration Approach").

Why ``demand_scale``: predicted cost is monotonic increasing in ``demand_scale``
(more building loss -> more demand -> more fuel -> more cost), so a bounded
one-dimensional bisection converges quickly and stays physically interpretable.

The reference actual enters exclusively through a ``ReferenceActualProvider``,
wrapped by the timeout- and field-guarded
:class:`~energy_cost_predictor.provider_adapter.ReferenceActualProviderAdapter`
(30s deadline, Req 8.6, 8.7). A non-positive actual is rejected with
:class:`~energy_cost_predictor.errors.InvalidActualCostError` and no parameter
adjustment is performed (Req 8.2).

Calibration sits *outside* the pure core: it repeatedly runs the predictor with
adjusted parameters and never mutates core source. On success it commits the
calibrated params onto the predictor so subsequent same-region predictions use
them (Req 8.4); on failure to converge it retains the params that produced the
best observed error and returns that error (Req 8.5), without raising.
"""

from __future__ import annotations

from dataclasses import dataclass

from energy_cost_predictor.accuracy import compute_accuracy
from energy_cost_predictor.errors import (
    InvalidActualCostError,
    ProviderTimeoutError,
)
from energy_cost_predictor.models import (
    CalibrationResult,
    HouseProfile,
    ModelParameters,
)
from energy_cost_predictor.provider_adapter import (
    DEFAULT_TIMEOUT_SECONDS,
    ReferenceActualProviderAdapter,
)
from energy_cost_predictor.providers import ReferenceActualProvider

__all__ = [
    "CalibrationConfig",
    "Calibration",
]


#: The accuracy target as a relative-error percentage (Req 8.1, 8.3). Matches
#: :data:`energy_cost_predictor.accuracy.ACCURACY_TARGET_PCT`.
DEFAULT_ACCURACY_TARGET_PCT: float = 1.00

#: Maximum bisection iterations before giving up (Req 8.1).
DEFAULT_ITERATION_LIMIT: int = 100

#: Search bounds for ``demand_scale``. The bisection brackets the actual within
#: ``[min, max]``; a scale achieving <= 1% inside these bounds is found within
#: the iteration budget.
DEFAULT_SCALE_MIN: float = 1e-6
DEFAULT_SCALE_MAX: float = 1e6


@dataclass(frozen=True)
class CalibrationConfig:
    """Tunable knobs for a calibration run.

    :param iteration_limit: Maximum bisection iterations (Req 8.1).
    :param accuracy_target_pct: Relative-error percentage at or below which the
        target is met (Req 8.1, 8.3).
    :param scale_min: Lower bound of the ``demand_scale`` search interval.
    :param scale_max: Upper bound of the ``demand_scale`` search interval.
    :param timeout_seconds: Deadline for the reference-actual provider call
        (Req 8.6, 8.7).
    """

    iteration_limit: int = DEFAULT_ITERATION_LIMIT
    accuracy_target_pct: float = DEFAULT_ACCURACY_TARGET_PCT
    scale_min: float = DEFAULT_SCALE_MIN
    scale_max: float = DEFAULT_SCALE_MAX
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS


class Calibration:
    """Calibrate a predictor's ``demand_scale`` against a reference actual.

    The calibrator wraps the pipeline: it evaluates the predictor's annual cost
    at a candidate ``demand_scale``, compares it to the reference actual, and
    bisects the scale using the monotonic predicted-cost relationship until the
    relative error is within target or the iteration budget is exhausted.
    """

    def __init__(
        self,
        predictor,
        house: HouseProfile,
        period,
        reference_provider: ReferenceActualProvider,
        config: CalibrationConfig = CalibrationConfig(),
    ) -> None:
        """Wire the calibrator to a predictor, reference house, and provider.

        :param predictor: A :class:`~energy_cost_predictor.predictor.Predictor`
            (or any object exposing ``params`` and
            ``predict_annual_cost(house, period).cost_usd``). Its ``params`` are
            read and, on completion, replaced with the calibrated/best params so
            subsequent same-region predictions use them (Req 8.4, 8.5).
        :param house: The Reference_House to calibrate against.
        :param period: The annual :class:`~energy_cost_predictor.predictor.Period`
            over which predicted cost is computed.
        :param reference_provider: An object exposing the
            ``ReferenceActualProvider`` Protocol. It is wrapped in a
            :class:`ReferenceActualProviderAdapter` so the field and 30s-timeout
            guards apply (Req 8.6, 8.7).
        :param config: Iteration limit, accuracy target, search bounds, and the
            provider timeout.
        """
        self.predictor = predictor
        self.house = house
        self.period = period
        self.config = config
        self._reference_adapter = ReferenceActualProviderAdapter(
            reference_provider, timeout_seconds=config.timeout_seconds
        )

    def _predicted_cost(self, demand_scale: float) -> float:
        """Return the predicted annual cost at ``demand_scale``.

        The predictor's ``params`` are temporarily set to a
        :class:`ModelParameters` with the candidate scale (preserving the
        existing ``efficiency_trim``), the prediction is run, and the original
        ``params`` are restored so evaluation never clobbers predictor state.
        The calibrated/best params are committed separately, only on completion.
        """
        saved = self.predictor.params
        try:
            self.predictor.params = ModelParameters(
                demand_scale=demand_scale,
                efficiency_trim=saved.efficiency_trim,
            )
            return self.predictor.predict_annual_cost(
                self.house, self.period
            ).cost_usd
        finally:
            self.predictor.params = saved

    def calibrate(self) -> CalibrationResult:
        """Run the bisection calibration (design "Calibration Approach").

        Procedure:

        1. Obtain the reference actual via the adapter under a 30s timeout
           (Req 8.6, 8.7). A timeout propagates as
           :class:`ProviderTimeoutError` and aborts with no adjustment.
        2. Reject an actual annual cost <= 0 with
           :class:`InvalidActualCostError`, performing NO parameter adjustment
           (Req 8.2).
        3. Bisect ``demand_scale`` over ``[scale_min, scale_max]`` using the
           monotonic predicted-cost relationship: when predicted < actual raise
           the scale (move ``lo`` up), otherwise lower it (move ``hi`` down).
           Track the smallest relative error seen and the scale that produced it
           so the tracked best is non-increasing across iterations (Req 8.1).
        4. Stop at relative error <= target (success) or after the iteration
           limit (Req 8.1).
        5. On success, commit the calibrated params onto the predictor so
           same-region predictions use them (Req 8.3, 8.4). On failure, commit
           the best-error params and return that error with
           ``target_met=False`` — without raising (Req 8.5).

        :returns: A :class:`CalibrationResult` with the committed params, the
            best relative-error percentage, whether the target was met, and the
            number of iterations performed.
        :raises InvalidActualCostError: If the reference actual is <= 0 (Req 8.2).
        :raises ProviderTimeoutError: If the reference-actual provider does not
            respond within the deadline (Req 8.7).
        """
        # 1. Obtain the reference actual (adapter enforces the 30s timeout and
        #    required-field guards). A timeout raises ProviderTimeoutError,
        #    which propagates to abort the run with no adjustment (Req 8.6, 8.7).
        reference = self._reference_adapter.get_reference_actual(self.house.house_id)
        actual = reference.actual_annual_cost_usd

        # 2. Reject non-positive actual with NO parameter adjustment (Req 8.2).
        if actual <= 0:
            raise InvalidActualCostError(actual)

        cfg = self.config
        trim = self.predictor.params.efficiency_trim

        lo = cfg.scale_min
        hi = cfg.scale_max

        best_error_pct = float("inf")
        best_scale = self.predictor.params.demand_scale

        def consider(scale: float) -> float:
            """Evaluate ``scale``; update the (non-increasing) best; return pct."""
            nonlocal best_error_pct, best_scale
            predicted = self._predicted_cost(scale)
            report = compute_accuracy(predicted, actual)
            pct = report.relative_error_pct
            if pct < best_error_pct:
                best_error_pct = pct
                best_scale = scale
            return pct

        iterations = 0
        target_met = False

        # Check the bracket endpoints first: they establish the best-so-far and
        # can satisfy the target immediately for an actual reachable at a bound.
        for endpoint in (lo, hi):
            iterations += 1
            if consider(endpoint) <= cfg.accuracy_target_pct:
                target_met = True
                break

        # 3-4. Bisect while the budget remains and the target is unmet.
        while not target_met and iterations < cfg.iteration_limit:
            mid = (lo + hi) / 2.0
            iterations += 1

            predicted_mid = self._predicted_cost(mid)
            report = compute_accuracy(predicted_mid, actual)
            pct = report.relative_error_pct
            if pct < best_error_pct:
                best_error_pct = pct
                best_scale = mid

            if pct <= cfg.accuracy_target_pct:
                target_met = True
                break

            # Monotonic increasing: predicted too low -> need a larger scale
            # (raise lo); predicted too high -> need a smaller scale (lower hi).
            if predicted_mid < actual:
                lo = mid
            else:
                hi = mid

        # 5. Commit the calibrated (success) or best (failure) params so
        #    subsequent same-region predictions use them (Req 8.3, 8.4, 8.5).
        calibrated = ModelParameters(demand_scale=best_scale, efficiency_trim=trim)
        self.predictor.params = calibrated

        return CalibrationResult(
            params=calibrated,
            best_relative_error_pct=best_error_pct,
            target_met=target_met,
            iterations=iterations,
        )
