"""Integration smoke tests for the queue modules and their wiring."""

from __future__ import annotations

from frappe.tests import UnitTestCase


class TestQueueWiring(UnitTestCase):
    """Ensure the queue modules import cleanly and expose the expected API."""

    def test_modules_import(self) -> None:
        """All queue modules must be importable without side effects."""
        from kenya_compliance_via_slade.kenya_compliance_via_slade.background_tasks import (  # noqa: F401
            integration_request_retention,
            queue_maintenance,
        )
        from kenya_compliance_via_slade.kenya_compliance_via_slade.queue import (  # noqa: F401
            circuit_breaker,
            failure_handler,
            failures,
            identity,
            job_store,
            policy,
        )

    def test_builder_exposes_request_id(self) -> None:
        """The HTTP builder carries a request_id for attempt correlation."""
        from kenya_compliance_via_slade.kenya_compliance_via_slade.apis.api_builder import (
            EndpointsBuilder,
        )

        builder = EndpointsBuilder()
        self.assertIsNone(builder.request_id)
        builder.request_id = "abc123"
        self.assertEqual(builder.request_id, "abc123")

    def test_custom_queue_overrides_lifecycle(self) -> None:
        """The override implements identity, retry and failure hooks."""
        from kenya_compliance_via_slade.kenya_compliance_via_slade.overrides.server.etims_job_queue import (
            CustomETimsJobQueue,
        )

        for method in (
            "before_insert",
            "run_queue",
            "update_status",
            "_ensure_identity",
            "_handle_unexpected_failure",
        ):
            self.assertTrue(hasattr(CustomETimsJobQueue, method), method)

    def test_policy_defaults_are_sane(self) -> None:
        """Policy accessors return usable values even without overrides."""
        from kenya_compliance_via_slade.kenya_compliance_via_slade.queue import policy

        self.assertGreaterEqual(policy.max_retries(), 0)
        self.assertTrue(policy.retry_backoff())
        self.assertGreaterEqual(policy.circuit_failure_threshold(), 1)
        self.assertTrue(policy.circuit_cooldown())
        self.assertGreaterEqual(policy.job_retention_hours(), 1)
