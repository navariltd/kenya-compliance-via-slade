"""Maps doctype names defined and used in the app to variable names"""

from typing import Final

# Doctypes
SETTINGS_DOCTYPE_NAME: Final[str] = "Navari KRA eTims Settings"
ORGANISATION_MAPPING_DOCTYPE_NAME: Final[str] = "eTims Settings Organisation Mapping"
COMPANY_MAPPING_DOCTYPE_NAME: Final[str] = "eTims Company Setup Mapping"
ROUTES_TABLE_DOCTYPE_NAME: Final[str] = "eTims Routes"
ROUTES_TABLE_CHILD_DOCTYPE_NAME: Final[str] = "eTims Route Table Item"
ITEM_CLASSIFICATIONS_DOCTYPE_NAME: Final[str] = "eTims Item Classification"
SLADE_ID_MAPPING_DOCTYPE_NAME: Final[str] = "eTims ID Mapping"
TAXATION_TYPE_DOCTYPE_NAME: Final[str] = "eTims Taxation Type"
PAYMENT_TYPE_DOCTYPE_NAME: Final[str] = "Navari KRA eTims Payment Type"
PACKAGING_UNIT_DOCTYPE_NAME: Final[str] = "eTims Packaging Unit"
WORKSTATION_DOCTYPE_NAME: Final[str] = "Navari KRA eTims Workstation"
OPERATION_TYPE_DOCTYPE_NAME: Final[str] = "Navari eTims Stock Operation Type"
UNIT_OF_QUANTITY_DOCTYPE_NAME: Final[str] = "eTims Unit of Quantity"
ENVIRONMENT_SPECIFICATION_DOCTYPE_NAME: Final[str] = (
    "Navari KRA eTims Environment Identifier"
)
INTEGRATION_LOGS_DOCTYPE_NAME: Final[str] = "Navari KRA eTims Integration Log"
ITEM_TYPE_DOCTYPE_NAME: Final[str] = "eTims Item Type"
PRODUCT_TYPE_DOCTYPE_NAME: Final[str] = "eTims Product Type"
COUNTRIES_DOCTYPE_NAME: Final[str] = "eTims Country of Origin"
IMPORTED_ITEMS_STATUS_DOCTYPE_NAME: Final[str] = "Navari eTims Import Item Status"
REGISTERED_PURCHASES_DOCTYPE_NAME: Final[str] = "Navari eTims Registered Purchases"
UOM_CATEGORY_DOCTYPE_NAME: Final[str] = "Navari eTims UOM Category"
REGISTERED_PURCHASES_DOCTYPE_NAME_ITEM: Final[str] = (
    "Navari eTims Registered Purchases Items"
)
NOTICES_DOCTYPE_NAME: Final[str] = "Navari KRA eTims Notices"
USER_DOCTYPE_NAME: Final[str] = "Navari eTims User"
REGISTERED_IMPORTED_ITEM_DOCTYPE_NAME: Final[str] = (
    "Navari eTims Registered Imported Item"
)
QUEUE_MANAGER_DOCTYPE: Final[str] = "eTims Queue Manager"
QUEUE_DOCTYPE: Final[str] = "eTims Job Queue"

# Global Variables
SANDBOX_SERVER_URL: Final[str] = "https://etims-api-sbx.kra.go.ke/etims-api"
PRODUCTION_SERVER_URL: Final[str] = "https://etims-api.kra.go.ke/etims-api"

# Slade Headers
X_APP_HEADER: Final[str] = "etims"