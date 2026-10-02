"""Tests for mapping failures onto the queue-job retry lifecycle."""

from __future__ import annotations

import requests
from frappe.tests import UnitTestCase

from kenya_compliance_via_slade.kenya_compliance_via_slade.queue import policy
from kenya_compliance_via_slade.kenya_compliance_via_slade.queue.failure_handler import (
    resolve_job_failure,
)


class _FakeJob:
    """Minimal stand-in for an ``eTims Job Queue`` document."""

    def __init__(self, retry_count: int = 0) -> None:
        self._retry_count = retry_count
        self.calls: list[tuple[str, dict]] = []

    @property
    def retry_count(self) -> int:
        return self._retry_count

    def get(self, key, default=None):
        """Duck-type ``Document.get`` for the fields used by the handler."""
        if key == "retry_count":
            return self._retry_count
        return default

    def update_status(self, status: str, **kwargs) -> None:
        """Record the transition and emulate the retry counter increment."""
        self.calls.append((status, kwargs))
        if status == "Retrying":
            self._retry_count += 1


class TestFailureHandler(UnitTestCase):
    """Transient/auth failures retry; terminal (or exhausted) failures fail."""

    def test_transient_failure_retries(self) -> None:
        """A transient failure moves the job to ``Retrying``."""
        job = _FakeJob(retry_count=0)

        failure_class = resolve_job_failure(job, "boom", status_code=503)

        self.assertEqual(failure_class, "transient")
        self.assertEqual(job.calls[0][0], "Retrying")
        self.assertEqual(job.calls[0][1]["failure_class"], "transient")
        self.assertGreater(job.calls[0][1]["delay_seconds"], 0)

    def test_auth_failure_retries(self) -> None:
        """A 401 moves the job to ``Retrying`` with an auth classification."""
        job = _FakeJob(retry_count=0)

        failure_class = resolve_job_failure(job, "unauthorized", status_code=401)

        self.assertEqual(failure_class, "auth")
        self.assertEqual(job.calls[0][0], "Retrying")
        self.assertEqual(job.calls[0][1]["failure_class"], "auth")

    def test_terminal_failure_terminates(self) -> None:
        """A validation error fails the job immediately."""
        job = _FakeJob(retry_count=0)

        failure_class = resolve_job_failure(job, "bad payload", status_code=422)

        self.assertEqual(failure_class, "terminal")
        self.assertEqual(job.calls[0][0], "Failed")
        self.assertEqual(job.calls[0][1]["failure_class"], "terminal")

    def test_exhausted_budget_terminates(self) -> None:
        """A transient failure past the retry budget becomes terminal."""
        job = _FakeJob(retry_count=policy.max_retries())

        failure_class = resolve_job_failure(job, "still down", status_code=503)

        self.assertEqual(failure_class, "transient")
        self.assertEqual(job.calls[0][0], "Failed")
        self.assertEqual(job.calls[0][1]["failure_class"], "exhausted")

    def test_retry_after_is_honoured(self) -> None:
        """A 429 with ``Retry-After`` schedules the indicated delay."""
        job = _FakeJob(retry_count=0)
        response = requests.Response()
        response.status_code = 429
        response.headers["Retry-After"] = "120"

        resolve_job_failure(job, "slow down", status_code=429, response=response)

        self.assertEqual(job.calls[0][1]["delay_seconds"], 120)

    def test_none_job_only_classifies(self) -> None:
        """Without a job the handler still returns the classification."""
        self.assertEqual(resolve_job_failure(None, "x", status_code=503), "transient")
        self.assertEqual(resolve_job_failure(None, "x", status_code=400), "terminal")
