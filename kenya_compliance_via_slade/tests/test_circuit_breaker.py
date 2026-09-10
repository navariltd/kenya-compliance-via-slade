"""Tests for the per-endpoint circuit breaker."""

from __future__ import annotations

from frappe.tests import UnitTestCase

from kenya_compliance_via_slade.kenya_compliance_via_slade.queue import (
    circuit_breaker,
    policy,
)


class TestCircuitBreaker(UnitTestCase):
    """The breaker opens on repeated transient failures and closes on success."""

    SETTINGS = "test-circuit-settings"
    SCOPE = "example.invalid"

    def setUp(self) -> None:
        """Ensure a clean breaker before each test."""
        circuit_breaker.reset(self.SETTINGS, self.SCOPE)
        self.threshold = policy.circuit_failure_threshold()

    def tearDown(self) -> None:
        """Clear breaker state after each test."""
        circuit_breaker.reset(self.SETTINGS, self.SCOPE)

    def test_closed_until_threshold(self) -> None:
        """The breaker opens only on the threshold-th consecutive failure."""
        self.assertFalse(circuit_breaker.is_open(self.SETTINGS, self.SCOPE))

        for _ in range(self.threshold - 1):
            self.assertFalse(circuit_breaker.record_failure(self.SETTINGS, self.SCOPE))
            self.assertFalse(circuit_breaker.is_open(self.SETTINGS, self.SCOPE))

        self.assertTrue(circuit_breaker.record_failure(self.SETTINGS, self.SCOPE))
        self.assertTrue(circuit_breaker.is_open(self.SETTINGS, self.SCOPE))
        self.assertGreater(
            circuit_breaker.remaining_seconds(self.SETTINGS, self.SCOPE), 0
        )

    def test_success_resets_state(self) -> None:
        """A successful probe closes the breaker and clears the counter."""
        for _ in range(self.threshold):
            circuit_breaker.record_failure(self.SETTINGS, self.SCOPE)

        self.assertTrue(circuit_breaker.is_open(self.SETTINGS, self.SCOPE))

        circuit_breaker.record_success(self.SETTINGS, self.SCOPE)

        self.assertFalse(circuit_breaker.is_open(self.SETTINGS, self.SCOPE))
        self.assertEqual(circuit_breaker.failure_count(self.SETTINGS, self.SCOPE), 0)

    def test_host_extraction(self) -> None:
        """Hosts are parsed from URLs for scoping."""
        self.assertEqual(
            circuit_breaker.host_from_url("https://api.example.com/v1/items/"),
            "api.example.com",
        )
        self.assertEqual(circuit_breaker.host_from_url(None), "")
