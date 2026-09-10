"""
Configuration for queue retry, circuit-breaker and log-retention behaviour.

Values are read from optional fields on the ``eTims Queue Manager`` singleton
so that operators can tune behaviour without a code change.  When a field is
absent or blank the module-level default is used, which keeps the feature
working even before the accompanying patch has been applied.
"""

from __future__ import annotations

import frappe

from .failures import DEFAULT_BACKOFF_SECONDS

QUEUE_MANAGER_DOCTYPE = "eTims Queue Manager"

DEFAULT_MAX_RETRIES = 4
DEFAULT_CIRCUIT_FAILURE_THRESHOLD = 5
DEFAULT_CIRCUIT_COOLDOWN_SECONDS: tuple[int, ...] = (300, 900, 1800)
DEFAULT_JOB_RETENTION_HOURS = 72
DEFAULT_IR_SUCCESS_RETENTION_DAYS = 3
DEFAULT_IR_FAILED_RETENTION_DAYS = 30
DEFAULT_IR_MAX_ATTEMPTS = 10


def _raw(fieldname: str):
    """Return a raw Queue Manager field value, or ``None`` when unavailable."""
    try:
        return frappe.db.get_single_value(QUEUE_MANAGER_DOCTYPE, fieldname)
    except Exception:
        return None


def _as_int(fieldname: str, default: int, allow_zero: bool = False) -> int:
    """
    Read an integer override, falling back to *default*.

    Args:
        fieldname: Queue Manager field name.
        default: Value returned when the field is unset or invalid.
        allow_zero: When ``False`` (the default), a non-positive value is
            treated as "not configured" and replaced by *default*.
    """
    value = _raw(fieldname)

    if value in (None, ""):
        return default

    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default

    if not allow_zero and parsed <= 0:
        return default

    return parsed


def _as_int_tuple(fieldname: str, default: tuple[int, ...]) -> tuple[int, ...]:
    """Read a comma-separated integer schedule, falling back to *default*."""
    value = _raw(fieldname)

    if not value:
        return tuple(default)

    try:
        parsed = tuple(
            int(part.strip())
            for part in str(value).split(",")
            if part.strip() != ""
        )
    except (TypeError, ValueError):
        return tuple(default)

    return parsed or tuple(default)


def max_retries() -> int:
    """Maximum number of retries before a transient failure becomes terminal."""
    return max(0, _as_int("max_retries", DEFAULT_MAX_RETRIES))


def retry_backoff() -> tuple[int, ...]:
    """Retry backoff schedule in seconds."""
    return _as_int_tuple("retry_backoff_seconds", DEFAULT_BACKOFF_SECONDS)


def circuit_failure_threshold() -> int:
    """Consecutive transient failures that open the circuit breaker."""
    return max(1, _as_int("circuit_failure_threshold", DEFAULT_CIRCUIT_FAILURE_THRESHOLD))


def circuit_cooldown() -> tuple[int, ...]:
    """Circuit-breaker cooldown schedule in seconds."""
    return _as_int_tuple("circuit_cooldown_seconds", DEFAULT_CIRCUIT_COOLDOWN_SECONDS)


def job_retention_hours() -> int:
    """Hours to retain terminal ``eTims Job Queue`` records."""
    return max(1, _as_int("job_retention_hours", DEFAULT_JOB_RETENTION_HOURS))


def ir_success_retention_days() -> int:
    """Days to retain *successful* ``Integration Request`` logs."""
    return _as_int(
        "integration_request_success_retention_days",
        DEFAULT_IR_SUCCESS_RETENTION_DAYS,
        allow_zero=True,
    )


def ir_failed_retention_days() -> int:
    """Days to retain *failed* ``Integration Request`` logs."""
    return max(1, _as_int("integration_request_failed_retention_days", DEFAULT_IR_FAILED_RETENTION_DAYS))


def ir_max_attempts() -> int:
    """Maximum HTTP attempts retained per logical ``request_id``."""
    return max(1, _as_int("integration_request_max_attempts", DEFAULT_IR_MAX_ATTEMPTS))
