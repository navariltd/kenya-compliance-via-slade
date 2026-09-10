# Copyright (c) 2024, Navari Ltd and Contributors
# See license.txt

import frappe
from frappe.tests import UnitTestCase

from ..doctype_names_mapping import TAXATION_TYPE_DOCTYPE_NAME


class TestNavariKRAeTimsTaxationType(UnitTestCase):
    """Taxation types are unique and cleaned up after the test."""

    def test_duplicates(self) -> None:
        """A second record with the same code raises a duplicate error."""
        code = frappe.generate_hash(length=8)
        self.addCleanup(frappe.db.delete, TAXATION_TYPE_DOCTYPE_NAME, {"name": code})

        with self.assertRaises(frappe.DuplicateEntryError):
            doc = frappe.new_doc(TAXATION_TYPE_DOCTYPE_NAME)
            doc.cd = code
            doc.save(ignore_permissions=True)

            doc = frappe.new_doc(TAXATION_TYPE_DOCTYPE_NAME)
            doc.cd = code
            doc.save(ignore_permissions=True)
