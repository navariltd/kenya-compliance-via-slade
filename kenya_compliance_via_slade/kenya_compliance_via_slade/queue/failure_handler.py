"""
Map a failed remote attempt onto the ``eTims Job Queue`` lifecycle.

The handler is the single place that decides whether a failed attempt should:

* move the *same* logical job into ``Retrying`` (transient / auth failures), or
* terminate it as ``Failed`` once the retry budget is exhausted or the failure
  is genuinely terminal.

It also honours the circuit breaker so that a known-unavailable endpoint pauses
work instead of generating further attempts.
"""

from __future__ import annotations

import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime

from . import circuit_breaker, policy
from .failures import (
    FailureClass,
    classify,
    next_delay_seconds,
    retry_after_seconds,
)


def circuit_open_error(settings_name: str | None, url: str | None) -> str | None:
    """
    Return a human-readable reason when the circuit is open, else ``None``.

    Args:
        settings_name: eTims Settings document name.
        url: Target request URL.

    Returns:
        A message describing the open circuit, or ``None`` when calls may
        proceed.
    """
    scope = circuit_breaker.host_from_url(url)

    if not circuit_breaker.is_open(settings_name, scope):
        return None

    remaining = circuit_breaker.remaining_seconds(settings_name, scope)
    return (
        f"Remote endpoint unavailable; circuit breaker open for {remaining}s "
        f"({scope or 'unknown host'})."
    )


def resolve_job_failure(
    job_queue: Document | None,
    error_message: str,
    exc: BaseException | None = None,
    status_code: int | None = None,
    response=None,
    integration_request: str | None = None,
) -> FailureClass:
    """
    Apply a failure to the queue job according to its classification.

    Transient and auth failures keep the job alive in ``Retrying`` until the
    retry budget is exhausted; terminal failures (or an exhausted budget) mark
    it ``Failed`` and release its ``request_id``.

    Args:
        job_queue: The driving ``eTims Job Queue`` document, or ``None``.
        error_message: Human-readable error detail to persist.
        exc: Exception raised during the attempt, if any.
        status_code: HTTP status code returned, if any.
        response: Raw ``requests.Response`` used to read ``Retry-After``.
        integration_request: Optional ``Integration Request`` name to link.

    Returns:
        The classification applied to the failure.
    """
    failure_class = classify(exc=exc, status_code=status_code)

    if job_queue is None:
        return failure_class

    if failure_class == "transient" and _retry_budget_available(job_queue):
        delay = retry_after_seconds(response)
        if delay is None:
            delay = next_delay_seconds(job_queue.retry_count or 0, policy.retry_backoff())

        job_queue.update_status(
            "Retrying",
            error_message=error_message,
            integration_request=integration_request,
            failure_class="transient",
            delay_seconds=delay,
        )
        return failure_class

    if failure_class == "auth" and _retry_budget_available(job_queue):
        job_queue.update_status(
            "Retrying",
            error_message=error_message,
            integration_request=integration_request,
            failure_class="auth",
            delay_seconds=next_delay_seconds(
                job_queue.retry_count or 0, policy.retry_backoff()
            ),
        )
        return failure_class

    job_queue.update_status(
        "Failed",
        error_message=error_message,
        integration_request=integration_request,
        failure_class=failure_class if failure_class == "terminal" else "exhausted",
    )

    return failure_class


def pause_for_open_circuit(job_queue: Document | None, reason: str) -> None:
    """
    Defer an attempt while the remote endpoint is known to be unavailable.

    No ``Integration Request`` is created, which is what prevents the attempt
    log from growing during an extended outage.

    Args:
        job_queue: The driving ``eTims Job Queue`` document, or ``None``.
        reason: Message explaining why the attempt was skipped.
    """
    if job_queue is None:
        return

    job_queue.update_status(
        "Retrying",
        error_message=reason,
        failure_class="circuit_open",
        delay_seconds=_cooldown_seconds(),
    )


def _cooldown_seconds() -> int:
    """Return a short deferral used while the circuit is open."""
    cooldown = policy.circuit_cooldown()
    return int(cooldown[0]) if cooldown else 300


def _retry_budget_available(job_queue: Document) -> bool:
    """
    Return whether the job may be retried again.

    Args:
        job_queue: The ``eTims Job Queue`` document being evaluated.

    Returns:
        ``True`` when the number of retries already used is below the
        configured maximum.
    """
    used = int(job_queue.get("retry_count") or 0)
    return used < policy.max_retries()
