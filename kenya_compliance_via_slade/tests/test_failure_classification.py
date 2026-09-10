"""Unit tests for remote-failure classification and retry backoff."""

from __future__ import annotations

import requests
from frappe.tests import UnitTestCase

from kenya_compliance_via_slade.kenya_compliance_via_slade.queue.failures import (
    classify,
    classify_exception,
    classify_status,
    next_delay_seconds,
    retry_after_seconds,
)


class TestFailureClassification(UnitTestCase):
    """Verify transient/terminal/auth distinctions and the retry schedule."""

    def test_transient_status_codes(self) -> None:
        """429/502/503/504 and timeouts are transient."""
        for status_code in (408, 425, 429, 502, 503, 504):
            self.assertEqual(classify_status(status_code), "transient", status_code)

    def test_terminal_status_codes(self) -> None:
        """Validation/request errors are terminal."""
        for status_code in (400, 404, 409, 422, 501):
            self.assertEqual(classify_status(status_code), "terminal", status_code)

    def test_auth_status_codes(self) -> None:
        """401/403 signal an authentication problem."""
        for status_code in (401, 403):
            self.assertEqual(classify_status(status_code), "auth", status_code)

    def test_connection_errors_are_transient(self) -> None:
        """Timeout and connection errors are transient."""
        self.assertEqual(
            classify_exception(requests.exceptions.Timeout()), "transient"
        )
        self.assertEqual(
            classify_exception(requests.exceptions.ConnectionError()), "transient"
        )
        self.assertEqual(classify_exception(ValueError("bad payload")), "terminal")

    def test_status_code_takes_precedence(self) -> None:
        """A parsed response is more specific than a transport error."""
        self.assertEqual(
            classify(exc=ValueError("x"), status_code=503),
            "transient",
        )

    def test_backoff_is_schedule_then_capped(self) -> None:
        """Backoff follows the schedule and repeats the last value."""
        schedule = (60, 300, 900, 1800)
        self.assertEqual(next_delay_seconds(0, schedule), 60)
        self.assertEqual(next_delay_seconds(1, schedule), 300)
        self.assertEqual(next_delay_seconds(2, schedule), 900)
        self.assertEqual(next_delay_seconds(3, schedule), 1800)
        self.assertEqual(next_delay_seconds(9, schedule), 1800)

    def test_retry_after_header(self) -> None:
        """Retry-After seconds are honoured; invalid values return None."""
        response = requests.Response()
        response.headers["Retry-After"] = "120"
        self.assertEqual(retry_after_seconds(response), 120)

        response.headers["Retry-After"] = "not-a-number"
        self.assertIsNone(retry_after_seconds(response))

        self.assertIsNone(retry_after_seconds(None))
