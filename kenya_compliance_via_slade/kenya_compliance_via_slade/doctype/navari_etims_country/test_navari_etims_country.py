# Copyright (c) 2024, Navari Ltd and Contributors
# See license.txt

import frappe
from frappe.tests import UnitTestCase

from ..doctype_names_mapping import COUNTRIES_DOCTYPE_NAME


class TestNavarieTimsCountry(UnitTestCase):
    """Country of origin records are created with a unique name and cleaned up."""

    def _create_country(self, name: str):
        """Create a country record named *name*."""
        doc = frappe.new_doc(COUNTRIES_DOCTYPE_NAME)
        doc.code = "TEST"
        doc.sort_order = "0"
        doc.code_name = name
        doc.code_description = name
        doc.save(ignore_permissions=True)
        return doc

    def test_country_creation(self) -> None:
        """A country record is persisted with the supplied fields."""
        name = f"Test Country {frappe.generate_hash(length=8)}"
        self.addCleanup(frappe.db.delete, COUNTRIES_DOCTYPE_NAME, {"name": name})

        doc = self._create_country(name)
        fetched_doc = frappe.get_doc(COUNTRIES_DOCTYPE_NAME, doc.name, for_update=False)

        self.assertEqual(fetched_doc.name, name)
        self.assertEqual(fetched_doc.code, "TEST")
        self.assertEqual(fetched_doc.code_name, name)
        self.assertEqual(fetched_doc.code_description, name)

    def test_duplicate_country_creation(self) -> None:
        """Creating two records with the same name raises a duplicate error."""
        name = f"Test Country {frappe.generate_hash(length=8)}"
        self.addCleanup(frappe.db.delete, COUNTRIES_DOCTYPE_NAME, {"name": name})

        self._create_country(name)

        with self.assertRaises(frappe.DuplicateEntryError):
            self._create_country(name)
