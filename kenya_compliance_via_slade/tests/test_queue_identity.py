"""Unit tests for deterministic ``request_id`` construction."""

from __future__ import annotations

from frappe.tests import UnitTestCase

from kenya_compliance_via_slade.kenya_compliance_via_slade.queue.identity import (
    build_request_id,
    canonicalize,
    normalize_payload,
)


class TestRequestIdentity(UnitTestCase):
    """Verify that identity is stable, scoped and payload-order independent."""

    def test_payload_key_order_does_not_change_id(self) -> None:
        """Identical payloads with different key order share one id."""
        first = build_request_id(
            route_key="ItemsSearchReq",
            request_method="GET",
            company="Acme",
            settings_name="Settings-1",
            reference_doctype="Item",
            reference_docname="ITEM-001",
            request_data={"name": "ITEM-001", "extra": {"b": 2, "a": 1}},
        )
        second = build_request_id(
            route_key="ItemsSearchReq",
            request_method="GET",
            company="Acme",
            settings_name="Settings-1",
            reference_doctype="Item",
            reference_docname="ITEM-001",
            request_data={"extra": {"a": 1, "b": 2}, "name": "ITEM-001"},
        )

        self.assertEqual(first, second)

    def test_json_string_and_dict_share_id(self) -> None:
        """A JSON string payload hashes the same as the equivalent dict."""
        as_dict = build_request_id(
            "ItemsSearchReq", "GET", "Acme", "S1", "Item", "ITEM-1", {"name": "ITEM-1"}
        )
        as_json = build_request_id(
            "ItemsSearchReq",
            "GET",
            "Acme",
            "S1",
            "Item",
            "ITEM-1",
            '{"name": "ITEM-1"}',
        )

        self.assertEqual(as_dict, as_json)

    def test_volatile_metadata_is_ignored(self) -> None:
        """Company/branch/document keys never influence identity."""
        base = build_request_id(
            "ItemsSearchReq", "GET", "Acme", "S1", "Item", "ITEM-1", {"name": "ITEM-1"}
        )
        noisy = build_request_id(
            "ItemsSearchReq",
            "GET",
            "Acme",
            "S1",
            "Item",
            "ITEM-1",
            {"name": "ITEM-1", "company": "Other", "branch_id": "00"},
        )

        self.assertEqual(base, noisy)

    def test_distinct_logical_work_gets_distinct_id(self) -> None:
        """Different route, page, payload, reference or settings differ in id."""
        base = dict(
            route_key="ItemsSearchReq",
            request_method="GET",
            company="Acme",
            settings_name="S1",
            reference_doctype="Item",
            reference_docname="ITEM-1",
            request_data={"name": "ITEM-1"},
        )

        base_id = build_request_id(**base)

        self.assertNotEqual(base_id, build_request_id(**{**base, "page": 2}))
        self.assertNotEqual(
            base_id, build_request_id(**{**base, "request_data": {"name": "ITEM-2"}})
        )
        self.assertNotEqual(
            base_id, build_request_id(**{**base, "settings_name": "S2"})
        )
        self.assertNotEqual(base_id, build_request_id(**{**base, "route_key": "Other"}))
        self.assertNotEqual(
            base_id, build_request_id(**{**base, "reference_docname": "ITEM-2"})
        )

    def test_normalize_payload_is_recursive(self) -> None:
        """Normalisation strips volatile keys at every nesting level."""
        result = normalize_payload(
            {"a": 1, "nested": {"company": "X", "keep": 2}, "company": "Y"}
        )

        self.assertEqual(result, {"a": 1, "nested": {"keep": 2}})

    def test_canonicalize_is_stable(self) -> None:
        """Canonical output is deterministic and whitespace-free."""
        self.assertEqual(
            canonicalize({"b": 1, "a": 2}),
            canonicalize({"a": 2, "b": 1}),
        )
        self.assertNotIn(" ", canonicalize({"a": 1}))
