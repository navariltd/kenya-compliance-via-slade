import frappe

from ..doctype.doctype_names_mapping import SETTINGS_DOCTYPE_NAME
from ..utils import LEGACY_AUTH_PROVIDER


def execute():
    frappe.db.set_value(
        SETTINGS_DOCTYPE_NAME,
        {"auth_username": ["is", "set"]},
        "auth_provider",
        LEGACY_AUTH_PROVIDER,
        update_modified=False,
    )
