"""
Definitions shared by the queue reliability migration patches.

The idempotency/retry fields on ``eTims Job Queue`` and the retry/retention
settings on ``eTims Queue Manager`` now live **directly on the doctypes** owned
by the ``csf_ke`` app.  An earlier iteration of this migration created them as
custom fields from this app; those legacy custom fields are listed here so they
can be removed before the doctype sync runs.
"""

from __future__ import annotations

INTEGRATION_REQUEST_DOCTYPE = "Integration Request"
QUEUE_DOCTYPE = "eTims Job Queue"
QUEUE_MANAGER_DOCTYPE = "eTims Queue Manager"

#: Custom field added to the core ``Integration Request`` doctype (which this
#: app cannot edit) to correlate attempts belonging to one logical queue job.
INTEGRATION_REQUEST_FIELDS = [
    {
        "fieldname": "etims_attempt_number",
        "label": "eTims Attempt Number",
        "fieldtype": "Int",
        "read_only": 1,
        "insert_after": "request_id",
    },
]

#: Custom fields created by an earlier version of this migration. They are
#: deleted because the fields now live directly on the ``csf_ke`` doctypes.
LEGACY_CUSTOM_FIELDS = {
    QUEUE_DOCTYPE: [
        "request_id",
        "dedupe_key",
        "attempt_count",
        "max_retries",
        "next_retry_at",
        "is_terminal",
        "failure_class",
        "last_error",
    ],
    QUEUE_MANAGER_DOCTYPE: [
        "queue_reliability_section",
        "max_retries",
        "retry_backoff_seconds",
        "column_break_reliability",
        "circuit_failure_threshold",
        "circuit_cooldown_seconds",
        "job_retention_hours",
        "integration_request_success_retention_days",
        "integration_request_failed_retention_days",
        "integration_request_max_attempts",
    ],
}

#: Defaults seeded onto the ``eTims Queue Manager`` singleton so the settings
#: are explicit and visible in the UI.
MANAGER_DEFAULT_VALUES = {
    "max_retries": "4",
    "retry_backoff_seconds": "60,300,900,1800",
    "circuit_failure_threshold": "5",
    "circuit_cooldown_seconds": "300,900,1800",
    "job_retention_hours": "72",
    "integration_request_success_retention_days": "3",
    "integration_request_failed_retention_days": "30",
    "integration_request_max_attempts": "10",
}
