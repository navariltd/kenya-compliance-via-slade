"""Routing tests: request APIs forward the correct route key to the queue."""

from __future__ import annotations

import inspect
from unittest import mock

from frappe.tests import UnitTestCase

from kenya_compliance_via_slade.kenya_compliance_via_slade.apis import apis as apis_module
from kenya_compliance_via_slade.kenya_compliance_via_slade.apis.process_request import (
    process_request,
)
from kenya_compliance_via_slade.kenya_compliance_via_slade.background_tasks import (
    tasks as tasks_module,
)

#: ``apis.apis`` wrappers: (function name, positional args, expected route key).
APIS_CASES = [
    ("perform_customer_search", ('{"name": "X"}',), "CustSearchReq"),
    ("fetch_item_details", ('{"name": "X"}', "S"), "ItemSearchReq"),
    ("search_customers_request", ('{"name": "X"}', "S"), "CustomersSearchReq"),
    ("get_customer_details", ('{"name": "X"}', "S"), "CustomerSearchReq"),
    ("get_my_user_details", ('{"name": "X"}',), "BhfUserSearchReq"),
    ("get_branch_user_details", ('{"name": "X"}',), "BhfUserSaveReq"),
    ("save_branch_user_details", ('{"name": "X"}',), "BhfUserSaveReq"),
    ("perform_item_search", ('{"name": "X"}', "S"), "ItemsSearchReq"),
    ("perform_import_item_search", ('{"name": "X"}', "S"), "ImportItemSearchReq"),
    ("perform_purchases_search", ('{"name": "X"}', "S"), "TrnsPurchaseSalesReq"),
    ("perform_purchase_search", ('{"name": "X"}', "S"), "TrnsPurchaseSearchReq"),
    ("send_imported_item_request", ('{"name": "X"}',), "ImportItemSearchReq"),
    ("update_imported_item_request", ('{"name": "X"}',), "ImportItemUpdateReq"),
    ("sync_operation_type", ('{"name": "X"}',), "OperationTypeReq"),
]

#: ``background_tasks.tasks`` wrappers.
TASKS_CASES = [
    ("get_item_classification_codes", ('{"name": "X"}', "S"), "ItemClsSearchReq"),
    ("fetch_etims_operation_types", ('{"name": "X"}',), "OperationTypesReq"),
    ("fetch_workstations", ("S",), "WorkstationSearchReq"),
    ("search_branch_request", ('{"name": "X"}', "S"), "BhfSearchReq"),
]


class TestApiRouting(UnitTestCase):
    """Thin API wrappers forward to ``process_request`` with the right route."""

    def _assert_routing(self, module, name: str, args: tuple, route_key: str) -> None:
        """Assert the wrapper calls ``process_request`` with *route_key*."""
        function = getattr(module, name)

        with mock.patch.object(module, "process_request") as patched:
            function(*args)

        patched.assert_called_once()

        call = patched.call_args
        self.assertEqual(call.args[1], route_key, f"{name} used the wrong route")

        valid_params = set(inspect.signature(process_request).parameters)
        for kwarg in call.kwargs:
            self.assertIn(
                kwarg,
                valid_params,
                f"{name} passed an invalid process_request kwarg: {kwarg}",
            )

    def test_apis_module_routing(self) -> None:
        """Every simple ``apis.apis`` wrapper routes to the expected key."""
        for name, args, route_key in APIS_CASES:
            with self.subTest(api=name):
                self._assert_routing(apis_module, name, args, route_key)

    def test_tasks_module_routing(self) -> None:
        """Every simple ``tasks`` wrapper routes to the expected key."""
        for name, args, route_key in TASKS_CASES:
            with self.subTest(api=name):
                self._assert_routing(tasks_module, name, args, route_key)
