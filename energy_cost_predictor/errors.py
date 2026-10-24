"""Typed exception hierarchy for the Energy Cost Predictor.

Each exception corresponds to a row in the design document's Error Handling
table. Every error fails fast and names the offending cause (field, provider,
region key, fuel type, or dependency) so callers can act on it. The pure core
and the orchestration/provider-adapter layers raise these instead of returning
partial or default-valued results.
"""

from __future__ import annotations

from typing import Iterable, Optional, Sequence

__all__ = [
    "EnergyCostPredictorError",
    "MissingFieldError",
    "InvalidPeriodError",
    "DependencyUnavailableError",
    "ProviderDataError",
    "ProviderTimeoutError",
    "UnknownRegionError",
    "MissingRegionKeyError",
    "UnsupportedFuelError",
    "EfficiencyOutOfRangeError",
    "PriceUnavailableError",
    "InvalidActualCostError",
    "AccuracyUncomputableError",
]


class EnergyCostPredictorError(Exception):
    """Base class for all Energy Cost Predictor errors.

    Catching this type catches every domain error raised by the package.
    """


class MissingFieldError(EnergyCostPredictorError):
    """A required ``HouseProfile`` field was missing.

    Names the missing field; no estimate is returned (Req 1.5, 7.5).
    """

    def __init__(self, field_name: str) -> None:
        self.field_name = field_name
        super().__init__(
            f"Required HouseProfile field is missing: {field_name!r}."
        )


class InvalidPeriodError(EnergyCostPredictorError):
    """The supplied heating period is invalid.

    Used both when the period end falls before its start (Req 1.7) and when a
    savings comparison receives a period that is not 12 consecutive months
    (Req 7.6). Names the problem; no estimate or savings is returned.
    """

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"Invalid heating period: {reason}")


class DependencyUnavailableError(EnergyCostPredictorError):
    """A required dependency (demand model or pricing provider) is unavailable.

    Names the dependency; no estimate is returned (Req 1.6).
    """

    def __init__(self, dependency: str) -> None:
        self.dependency = dependency
        super().__init__(
            f"Required dependency is unavailable: {dependency}."
        )


class ProviderDataError(EnergyCostPredictorError):
    """A provider returned data with absent, null, or empty required fields.

    Names the provider and each missing required field; the estimate halts
    with no partial or default-valued result (Req 2.6, 5.6, 6.8).
    """

    def __init__(self, provider: str, missing_fields: Iterable[str]) -> None:
        self.provider = provider
        self.missing_fields: Sequence[str] = tuple(missing_fields)
        fields = ", ".join(repr(f) for f in self.missing_fields)
        super().__init__(
            f"Provider {provider!r} returned data missing required "
            f"field(s): {fields}."
        )


class ProviderTimeoutError(EnergyCostPredictorError):
    """A provider failed to respond within the 30-second deadline.

    Names the provider and indicates a timeout; the estimate halts
    (Req 2.7, 8.7).
    """

    def __init__(self, provider: str, timeout_seconds: float = 30.0) -> None:
        self.provider = provider
        self.timeout_seconds = timeout_seconds
        super().__init__(
            f"Provider {provider!r} did not respond within "
            f"{timeout_seconds:g} seconds (timeout)."
        )


class UnknownRegionError(EnergyCostPredictorError):
    """The requested region key does not match any known ``RegionProfile``.

    Names the unknown key; any previously loaded profile is left unchanged
    (Req 3.4).
    """

    def __init__(self, region_key: str) -> None:
        self.region_key = region_key
        super().__init__(f"Unknown region key: {region_key!r}.")


class MissingRegionKeyError(EnergyCostPredictorError):
    """The supplied region key was empty or null.

    Indicates the region key is missing (Req 3.5).
    """

    def __init__(self, message: str = "Region key is missing (empty or null).") -> None:
        super().__init__(message)


class UnsupportedFuelError(EnergyCostPredictorError):
    """The requested fuel type is not defined for the region.

    Names the unsupported fuel type; no cost is returned (Req 5.4).
    """

    def __init__(self, fuel_type: str, region_key: Optional[str] = None) -> None:
        self.fuel_type = fuel_type
        self.region_key = region_key
        if region_key is not None:
            message = (
                f"Fuel type {fuel_type!r} is not supported for region "
                f"{region_key!r}."
            )
        else:
            message = f"Fuel type {fuel_type!r} is not supported for this region."
        super().__init__(message)


class EfficiencyOutOfRangeError(EnergyCostPredictorError):
    """System efficiency fell outside the valid ``(0, 1]`` range.

    No cost is returned (Req 5.5).
    """

    def __init__(self, efficiency: float) -> None:
        self.efficiency = efficiency
        super().__init__(
            f"System efficiency {efficiency!r} is out of range; it must be "
            f"greater than 0 and less than or equal to 1."
        )


class PriceUnavailableError(EnergyCostPredictorError):
    """A fuel or electricity price was missing or negative.

    Previously computed values are retained unchanged; no cost is returned
    (Req 5.6, 6.8). Names the price key when known.
    """

    def __init__(
        self,
        price_key: Optional[str] = None,
        value: Optional[float] = None,
    ) -> None:
        self.price_key = price_key
        self.value = value
        label = f" for {price_key!r}" if price_key else ""
        if value is not None:
            detail = f" (received {value!r})"
        else:
            detail = ""
        super().__init__(
            f"Price unavailable or invalid{label}{detail}."
        )


class InvalidActualCostError(EnergyCostPredictorError):
    """The reference actual annual cost supplied to calibration was <= 0.

    No parameter adjustment is performed (Req 8.2).
    """

    def __init__(self, actual_cost: float) -> None:
        self.actual_cost = actual_cost
        super().__init__(
            f"Reference actual annual cost must be greater than 0; "
            f"received {actual_cost!r}."
        )


class AccuracyUncomputableError(EnergyCostPredictorError):
    """Relative error could not be computed because the actual cost is zero.

    No Accuracy_Target result is reported (Req 9.2).
    """

    def __init__(
        self,
        message: str = (
            "Relative error cannot be computed because the reference actual "
            "annual cost is zero."
        ),
    ) -> None:
        super().__init__(message)
