"""
Classify remote failures and compute retry backoff for the eTims queue.

A temporary Slade/eTIMS outage must not be treated the same way as a genuine
validation error.  This module provides a single, pure source of truth for the
distinction so that every call site classifies failures identically.

Failure classes:

* ``transient`` — the work should be retried later against the *same* logical
  queue job (network failures, HTTP 429/502/503/504, timeouts).
* ``auth`` — the access token must be refreshed before retrying.
* ``terminal`` — the request can never succeed as-is (validation errors,
  malformed payloads, HTTP 400/422).
"""

from __future__ import annotations

from typing import Literal

import requests

#: Classification of a single failed remote attempt.
FailureClass = Literal["transient", "auth", "terminal"]

#: HTTP statuses that indicate a temporary remote condition.
TRANSIENT_STATUS_CODES = frozenset({408, 425, 429, 502, 503, 504})

#: HTTP statuses that indicate an authentication problem.
AUTH_STATUS_CODES = frozenset({401, 403})

#: Default retry schedule in seconds (1m, 5m, 15m, 30m).
DEFAULT_BACKOFF_SECONDS: tuple[int, ...] = (60, 300, 900, 1800)

#: Exception types that always represent a transient network condition.
_TRANSIENT_EXCEPTIONS = (
    requests.exceptions.Timeout,
    requests.exceptions.ConnectionError,
    requests.exceptions.SSLError,
    requests.exceptions.ChunkedEncodingError,
)


def classify_exception(exc: BaseException | None) -> FailureClass:
    """
    Classify a raised exception.

    Args:
        exc: The exception raised while performing the remote call.

    Returns:
        ``"transient"`` for network/timeout errors, otherwise ``"terminal"``.
    """
    if isinstance(exc, _TRANSIENT_EXCEPTIONS):
        return "transient"

    return "terminal"


def classify_status(status_code: int | None) -> FailureClass:
    """
    Classify a non-2xx HTTP status code.

    Args:
        status_code: HTTP status code, or ``None`` when no response was
            received (which is treated as a transient network failure).

    Returns:
        The failure class for the status code.
    """
    if status_code is None:
        return "transient"

    if status_code in AUTH_STATUS_CODES:
        return "auth"

    if status_code in TRANSIENT_STATUS_CODES:
        return "transient"

    return "terminal"


def classify(
    exc: BaseException | None = None,
    status_code: int | None = None,
) -> FailureClass:
    """
    Classify a failed attempt from an optional exception and status code.

    When both are supplied the status code takes precedence, because a parsed
    HTTP response is more specific than a generic transport error.

    Args:
        exc: Exception raised during the attempt, if any.
        status_code: HTTP status code returned, if any.

    Returns:
        The failure class for the attempt.
    """
    if status_code is not None:
        return classify_status(status_code)

    return classify_exception(exc)


def next_delay_seconds(
    retry_count: int,
    backoff: tuple[int, ...] | list[int] | None = None,
) -> int:
    """
    Return the backoff delay to apply before the next attempt.

    Args:
        retry_count: Number of retries already scheduled (0 for the first
            retry).
        backoff: Optional custom schedule in seconds. Falls back to
            :data:`DEFAULT_BACKOFF_SECONDS`.

    Returns:
        The delay in seconds. Once the schedule is exhausted its final value
        is reused for all subsequent attempts.
    """
    schedule = tuple(backoff) if backoff else DEFAULT_BACKOFF_SECONDS

    if not schedule:
        return DEFAULT_BACKOFF_SECONDS[-1]

    if retry_count < len(schedule):
        return int(schedule[retry_count])

    return int(schedule[-1])


def retry_after_seconds(response: requests.Response | None) -> int | None:
    """
    Read the ``Retry-After`` hint from a response, when present.

    Args:
        response: The HTTP response, or ``None``.

    Returns:
        The number of seconds to wait, or ``None`` when the header is absent
        or not a plain number of seconds.
    """
    if response is None:
        return None

    header = None
    headers = getattr(response, "headers", None)
    if headers:
        header = headers.get("Retry-After")

    if not header:
        return None

    try:
        return max(0, int(str(header).strip()))
    except (TypeError, ValueError):
        return None
