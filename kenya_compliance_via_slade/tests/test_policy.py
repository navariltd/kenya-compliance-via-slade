"""Tests for the configurable queue policy accessors."""

from __future__ import annotations

from unittest import mock

from frappe.tests import UnitTestCase

from kenya_compliance_via_slade.kenya_compliance_via_slade.queue import policy


class TestQueuePolicy(UnitTestCase):
    """Policy accessors fall back to code defaults and parse overrides."""

    def test_int_falls_back_when_unset(self) -> None:
        """Unset or non-positive values fall back to the default."""
        with mock.patch.object(policy, "_raw", return_value=None):
            self.assertEqual(policy._as_int("max_retries", 4), 4)

        with mock.patch.object(policy, "_raw", return_value="0"):
            self.assertEqual(policy._as_int("max_retries", 4), 4)

        with mock.patch.object(policy, "_raw", return_value="not-a-number"):
            self.assertEqual(policy._as_int("max_retries", 4), 4)

    def test_int_override_is_used(self) -> None:
        """A positive override wins over the default."""
        with mock.patch.object(policy, "_raw", return_value="7"):
            self.assertEqual(policy._as_int("max_retries", 4), 7)

    def test_int_allows_zero_when_requested(self) -> None:
        """``allow_zero`` keeps an explicit zero."""
        with mock.patch.object(policy, "_raw", return_value="0"):
            self.assertEqual(
                policy._as_int("success_days", 3, allow_zero=True), 0
            )

    def test_int_tuple_parsing(self) -> None:
        """Comma-separated schedules parse, blanks fall back."""
        default = (60, 300)

        with mock.patch.object(policy, "_raw", return_value="10, 20 ,30"):
            self.assertEqual(
                policy._as_int_tuple("retry_backoff_seconds", default), (10, 20, 30)
            )

        with mock.patch.object(policy, "_raw", return_value=""):
            self.assertEqual(
                policy._as_int_tuple("retry_backoff_seconds", default), default
            )

    def test_public_accessors_return_usable_values(self) -> None:
        """All public accessors return sane values with no overrides."""
        with mock.patch.object(policy, "_raw", return_value=None):
            self.assertGreaterEqual(policy.max_retries(), 0)
            self.assertTrue(policy.retry_backoff())
            self.assertGreaterEqual(policy.circuit_failure_threshold(), 1)
            self.assertTrue(policy.circuit_cooldown())
            self.assertGreaterEqual(policy.job_retention_hours(), 1)
            self.assertGreaterEqual(policy.ir_success_retention_days(), 0)
            self.assertGreaterEqual(policy.ir_failed_retention_days(), 1)
            self.assertGreaterEqual(policy.ir_max_attempts(), 1)
