"""
Per-endpoint circuit breaker protecting the eTims queue from remote outages.

When the Slade/eTIMS endpoint is unavailable, thousands of independent jobs
must not each attempt the same dead endpoint.  After a configurable number of
consecutive transient failures the circuit *opens*: further remote calls for
that ``(settings, host)`` scope are refused (and, crucially, no new
``Integration Request`` attempt is logged) until a cooldown elapses.

The breaker is *half-open* once the cooldown expires: the next attempt is
allowed as a probe.  A success closes the circuit; a failure re-opens it with
the next, longer cooldown.

State is stored in the Frappe cache (Redis) so it is shared across workers.
"""

from __future__ import annotations

import time
from urllib.parse import urlparse

import frappe

from . import policy

CACHE_KEY_PREFIX = "etims_circuit_breaker"
STATE_TTL_SECONDS = 24 * 60 * 60


def _scope_key(settings_name: str | None, scope: str | None) -> str:
    """Build the cache key for a ``(settings, host)`` circuit."""
    return f"{CACHE_KEY_PREFIX}::{settings_name or '-'}::{scope or '-'}"


def host_from_url(url: str | None) -> str:
    """
    Extract the hostname from a URL.

    Args:
        url: Full request URL, or ``None``.

    Returns:
        The hostname, or an empty string when it cannot be parsed.
    """
    if not url:
        return ""

    try:
        return urlparse(url).hostname or ""
    except ValueError:
        return ""


def _load(settings_name: str | None, scope: str | None) -> dict:
    """Return the breaker state dict for the given scope."""
    try:
        state = frappe.cache().get_value(_scope_key(settings_name, scope))
    except Exception:
        state = None

    if not isinstance(state, dict):
        return {"failures": 0, "level": 0, "open_until": 0.0}

    return state


def _store(settings_name: str | None, scope: str | None, state: dict) -> None:
    """Persist the breaker state dict for the given scope."""
    try:
        frappe.cache().set_value(
            _scope_key(settings_name, scope),
            state,
            expires_in_sec=STATE_TTL_SECONDS,
        )
    except Exception:
        frappe.log_error(
            title="eTims circuit breaker — cache write failed",
            message=frappe.get_traceback(),
        )


def _cooldown_for_level(level: int) -> int:
    """Return the cooldown in seconds for a given breaker level."""
    schedule = policy.circuit_cooldown()

    if not schedule:
        return 0

    if level < len(schedule):
        return int(schedule[level])

    return int(schedule[-1])


def is_open(settings_name: str | None, scope: str | None) -> bool:
    """
    Return whether the circuit is currently open (blocking calls).

    Args:
        settings_name: eTims Settings document name.
        scope: Endpoint scope (typically the request host).

    Returns:
        ``True`` while the circuit is open.
    """
    state = _load(settings_name, scope)
    return float(state.get("open_until") or 0.0) > time.time()


def remaining_seconds(settings_name: str | None, scope: str | None) -> int:
    """Return the number of seconds until the circuit closes again."""
    state = _load(settings_name, scope)
    remaining = float(state.get("open_until") or 0.0) - time.time()
    return max(0, int(remaining))


def failure_count(settings_name: str | None, scope: str | None) -> int:
    """Return the number of consecutive failures recorded for the scope."""
    return int(_load(settings_name, scope).get("failures") or 0)


def record_success(settings_name: str | None, scope: str | None) -> None:
    """
    Reset the breaker after a successful remote call.

    Args:
        settings_name: eTims Settings document name.
        scope: Endpoint scope (typically the request host).
    """
    _store(settings_name, scope, {"failures": 0, "level": 0, "open_until": 0.0})


def record_failure(settings_name: str | None, scope: str | None) -> bool:
    """
    Record a transient failure and open the circuit at the threshold.

    Args:
        settings_name: eTims Settings document name.
        scope: Endpoint scope (typically the request host).

    Returns:
        ``True`` when this failure opened (or re-opened) the circuit.
    """
    state = _load(settings_name, scope)

    failures = int(state.get("failures") or 0) + 1
    level = int(state.get("level") or 0)

    threshold = policy.circuit_failure_threshold()

    opened = False
    open_until = float(state.get("open_until") or 0.0)

    if failures >= threshold:
        open_until = time.time() + _cooldown_for_level(level)
        level += 1
        failures = 0
        opened = True

    _store(
        settings_name,
        scope,
        {"failures": failures, "level": level, "open_until": open_until},
    )

    return opened


def reset(settings_name: str | None, scope: str | None) -> None:
    """Clear all breaker state for the scope."""
    try:
        frappe.cache().delete_value(_scope_key(settings_name, scope))
    except Exception:
        pass
