# Copyright (c) 2024, Navari Ltd and Contributors
# See license.txt

import frappe
from frappe.tests import UnitTestCase

from ..doctype_names_mapping import IMPORTED_ITEMS_STATUS_DOCTYPE_NAME


class TestNavarieTimsImportItemStatus(UnitTestCase):
    """Import item status records are created and cleaned up idempotently."""

    def _create_status(self, name: str):
        """Create an import item status record named *name*."""
        doc = frappe.new_doc(IMPORTED_ITEMS_STATUS_DOCTYPE_NAME)
        doc.code = "99"
        doc.sort_order = "99"
        doc.code_name = name
        doc.code_description = "Testing Import Status"
        doc.save(ignore_permissions=True)
        return doc

    def test_imported_item_type_creation(self) -> None:
        """An import item status record is persisted with the supplied fields."""
        name = f"Test Status {frappe.generate_hash(length=8)}"
        self.addCleanup(
            frappe.db.delete, IMPORTED_ITEMS_STATUS_DOCTYPE_NAME, {"name": name}
        )

        doc = self._create_status(name)
        fetched_doc = frappe.get_doc(
            IMPORTED_ITEMS_STATUS_DOCTYPE_NAME, doc.name, for_update=False
        )

        self.assertEqual(fetched_doc.name, name)
        self.assertEqual(fetched_doc.code_name, name)
        self.assertEqual(fetched_doc.sort_order, "99")
        self.assertEqual(fetched_doc.code_description, "Testing Import Status")
