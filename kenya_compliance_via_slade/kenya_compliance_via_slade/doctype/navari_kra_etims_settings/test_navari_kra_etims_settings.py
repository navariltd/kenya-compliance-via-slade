# Copyright (c) 2024, Navari Ltd and Contributors
# See license.txt

from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs

from frappe.tests import UnitTestCase

from ...utils import authenticate_and_get_token


class TestNavariKRAeTimsSettings(UnitTestCase):
    def _request_token(self, auth_server_url: str, auth_provider: str) -> tuple:
        response = MagicMock(ok=True, text="{}")
        response.json.return_value = {"access_token": "tok", "expires_in": 1800}
        with patch(
            "kenya_compliance_via_slade.kenya_compliance_via_slade.utils.requests.post",
            return_value=response,
        ) as post:
            token = authenticate_and_get_token(
                auth_server_url,
                "user",
                "pass",
                "client-id",
                "client-secret",
                auth_provider=auth_provider,
            )
        return token, post.call_args.args[0], parse_qs(post.call_args.kwargs["data"])

    def test_identity_provider_uses_client_credentials(self) -> None:
        token, url, payload = self._request_token(
            "https://identity-dev.slade360edi.com", "Slade360 Identity"
        )

        self.assertEqual(
            url,
            "https://identity-dev.slade360edi.com/realms/slade360/protocol/openid-connect/token",
        )
        self.assertEqual(
            payload,
            {
                "grant_type": ["client_credentials"],
                "client_id": ["client-id"],
                "client_secret": ["client-secret"],
            },
        )
        self.assertEqual(token["access_token"], "tok")

    def test_legacy_provider_uses_password_grant(self) -> None:
        _, url, payload = self._request_token(
            "https://accounts.multitenant.slade360.co.ke", "Legacy"
        )

        self.assertEqual(
            url, "https://accounts.multitenant.slade360.co.ke/oauth2/token/"
        )
        self.assertEqual(payload["grant_type"], ["password"])
        self.assertEqual(payload["username"], ["user"])
        self.assertEqual(payload["password"], ["pass"])
