import frappe

APP = "kenya_compliance_via_slade"
MODULE = "Kenya Compliance Via Slade"
SHIPPED_SIDEBAR = "KRA eTIMS"

LEGACY_NAME = "eTims Integration"


def execute():
    """Remove sidebars Frappe's v16 navigation upgrade built from old records."""
    if not frappe.db.exists("Sidebar", {"name": SHIPPED_SIDEBAR, "app": APP}):
        return

    leftovers = frappe.get_all(
        "Sidebar",
        filters={
            "module": MODULE,
            "standard": 0,
            "app": ["is", "not set"],
            "name": ["!=", SHIPPED_SIDEBAR],
        },
        pluck="name",
    )
    for name in leftovers:
        frappe.delete_doc("Sidebar", name, ignore_permissions=True, force=True)

    for doctype in ("Workspace Sidebar", "Desktop Icon", "Workspace"):
        if frappe.db.exists("DocType", doctype) and frappe.db.exists(doctype, LEGACY_NAME):
            frappe.delete_doc(doctype, LEGACY_NAME, ignore_permissions=True, force=True)
