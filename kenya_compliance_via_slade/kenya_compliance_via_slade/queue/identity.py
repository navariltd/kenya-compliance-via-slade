"""
Deterministic request identity (idempotency key) for eTims queue work.

Every logical unit of work submitted to the ``eTims Job Queue`` is assigned a
stable ``request_id``.  The id is derived purely from the *logical* request
parameters (route, HTTP method, company/settings scope, reference document,
normalised payload and page) so that repeated submissions of the same work
produce the same id regardless of payload key ordering or volatile metadata.

The id is used to collapse duplicate queue jobs (see
:mod:`kenya_compliance_via_slade.kenya_compliance_via_slade.queue.job_store`)
and to correlate the individual HTTP attempts recorded in
``Integration Request``.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

#: Payload keys that are resolved per-request (or stripped before enrichment)
#: and therefore must not influence the identity of the logical work.
VOLATILE_KEYS = frozenset(
    {
        "company",
        "company_name",
        "branch_id",
        "document_name",
    }
)

#: Number of hexadecimal characters retained from the SHA-256 digest.
REQUEST_ID_LENGTH = 32


def normalize_payload(value: Any) -> Any:
    """
    Recursively normalise a payload so it can be compared and hashed stably.

    JSON strings are decoded, dictionaries are key-sorted with volatile keys
    removed, and lists are normalised element-wise.

    Args:
        value: Arbitrary payload fragment (dict, list, str, scalar or None).

    Returns:
        A structurally identical object containing only stable values.
    """
    if isinstance(value, str):
        try:
            return normalize_payload(json.loads(value))
        except (TypeError, ValueError):
            return value

    if isinstance(value, dict):
        return {
            key: normalize_payload(item)
            for key, item in sorted(value.items())
            if key not in VOLATILE_KEYS
        }

    if isinstance(value, list):
        return [normalize_payload(item) for item in value]

    return value


def canonicalize(payload: Any) -> str:
    """
    Serialise a payload into a deterministic, human-inspectable string.

    Args:
        payload: Any JSON-serialisable payload.

    Returns:
        Compact JSON with sorted keys and no incidental whitespace.
    """
    return json.dumps(
        normalize_payload(payload),
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def build_request_id(
    route_key: str | None,
    request_method: str | None,
    company: str | None,
    settings_name: str | None,
    reference_doctype: str | None,
    reference_docname: str | None,
    request_data: Any,
    page: int | None = 1,
    scope: str | None = None,
) -> str:
    """
    Build the deterministic identity of a logical unit of queue work.

    Args:
        route_key: Endpoint route identifier (for example ``ItemsSearchReq``).
        request_method: HTTP method used for the request.
        company: Company the work belongs to.
        settings_name: eTims Settings document name scoping the work.
        reference_doctype: Doctype of the document that triggered the work.
        reference_docname: Name of the document that triggered the work.
        request_data: Payload describing the work (normalised before hashing).
        page: Pagination page. Each page is a distinct logical unit of work.
        scope: Optional extra discriminator for future route policies.

    Returns:
        A truncated SHA-256 hex digest uniquely identifying the logical work.
    """
    parts = [
        route_key or "",
        (request_method or "").upper(),
        company or "",
        settings_name or "",
        reference_doctype or "",
        reference_docname or "",
        canonicalize(request_data or {}),
        str(page or 1),
        scope or "",
    ]

    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return digest[:REQUEST_ID_LENGTH]
