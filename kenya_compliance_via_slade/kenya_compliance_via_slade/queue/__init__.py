"""
Idempotency, retry and outage-handling primitives for the eTims Job Queue.

The package provides:

* :mod:`identity` — deterministic ``request_id`` construction for logical work.
* :mod:`failures` — remote-failure classification and retry backoff policy.
* :mod:`policy` — configurable retry / circuit-breaker / retention defaults.
* :mod:`circuit_breaker` — per-endpoint outage protection.
* :mod:`job_store` — dedupe-aware persistence of ``eTims Job Queue`` records.
* :mod:`failure_handler` — maps a failed attempt onto the job lifecycle.
"""
