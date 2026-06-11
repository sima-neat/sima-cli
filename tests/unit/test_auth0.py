import base64
import json
import unittest
from unittest.mock import patch

from sima_cli.auth import auth0


def _jwt(payload):
    def encode(value):
        raw = json.dumps(value, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    return ".".join([
        encode({"alg": "none", "typ": "JWT"}),
        encode(payload),
        "",
    ])


def _tokens(payload):
    return {
        "access_token": _jwt(payload),
        "id_token": _jwt({"name": "Test User", "email": "test@example.com"}),
        "refresh_token": "refresh-token",
        "expires_in": 3600,
        "timestamp": 1,
    }


class TestAuth0AccessTokenRequirements(unittest.TestCase):
    def test_validates_latest_eula_and_userinfo_audience(self):
        tokens = _tokens({
            "aud": ["https://docs.sima.ai", auth0.PROD_USERINFO_AUDIENCE],
            "permissions": [auth0.DOC_ACCESS_GRANT, auth0.LATEST_EULA_GRANT],
        })

        with patch.dict("os.environ", {}, clear=True):
            valid, checks = auth0._validate_access_token_requirements(tokens)

        self.assertTrue(valid)
        self.assertEqual(checks, {"doc_access": True, "latest_eula": True, "userinfo_audience": True})

    def test_validates_staging_userinfo_audience_and_namespaced_roles(self):
        tokens = _tokens({
            "https://auth.sima.ai/roles": [auth0.DOC_ACCESS_GRANT, auth0.LATEST_EULA_GRANT],
            "aud": ["https://docs-dev.sima.ai", auth0.STAGING_USERINFO_AUDIENCE],
        })

        with patch.dict("os.environ", {"USE_STAGING_DEV_PORTAL": "true"}):
            valid, checks = auth0._validate_access_token_requirements(tokens)

        self.assertTrue(valid)
        self.assertEqual(checks, {"doc_access": True, "latest_eula": True, "userinfo_audience": True})

    def test_rejects_staging_userinfo_audience_in_production(self):
        tokens = _tokens({
            "roles": [auth0.DOC_ACCESS_GRANT, auth0.LATEST_EULA_GRANT],
            "aud": ["https://docs.sima.ai", auth0.STAGING_USERINFO_AUDIENCE],
        })

        with patch.dict("os.environ", {}, clear=True):
            valid, checks = auth0._validate_access_token_requirements(tokens)

        self.assertFalse(valid)
        self.assertEqual(checks, {"doc_access": True, "latest_eula": True, "userinfo_audience": False})

    def test_detects_missing_latest_eula_and_userinfo_audience(self):
        tokens = _tokens({"aud": "https://docs.sima.ai", "permissions": [auth0.DOC_ACCESS_GRANT]})

        valid, checks = auth0._validate_access_token_requirements(tokens)

        self.assertFalse(valid)
        self.assertEqual(checks, {"doc_access": True, "latest_eula": False, "userinfo_audience": False})

    def test_scope_claim_can_supply_latest_eula_grant(self):
        tokens = _tokens({
            "aud": auth0.PROD_USERINFO_AUDIENCE,
            "scope": f"openid profile {auth0.DOC_ACCESS_GRANT} {auth0.LATEST_EULA_GRANT}",
        })

        with patch.dict("os.environ", {}, clear=True):
            valid, checks = auth0._validate_access_token_requirements(tokens)

        self.assertTrue(valid)
        self.assertEqual(checks, {"doc_access": True, "latest_eula": True, "userinfo_audience": True})

    def test_access_token_has_latest_eula(self):
        tokens = _tokens({"permissions": [auth0.LATEST_EULA_GRANT]})

        self.assertTrue(auth0.access_token_has_latest_eula(tokens))

    def test_discourse_url_uses_staging_when_enabled(self):
        with patch.dict("os.environ", {"USE_STAGING_DEV_PORTAL": "true"}):
            self.assertEqual(auth0._discourse_sign_in_url(), auth0.STAGING_DISCOURSE_URL)
            self.assertEqual(auth0._discourse_sign_in_url(), "https://community-dev.sima.ai/login")

    def test_discourse_url_uses_community_in_production(self):
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(auth0._discourse_sign_in_url(), "https://community.sima.ai/login")

    def test_cached_invalid_token_logs_out_when_user_declines_discourse_sign_in(self):
        invalid_tokens = _tokens({"aud": "https://docs.sima.ai", "permissions": [auth0.DOC_ACCESS_GRANT]})

        with patch.object(auth0, "get_auth_config", return_value={}), \
             patch.object(auth0, "load_tokens", return_value=invalid_tokens), \
             patch.object(auth0, "is_token_valid", return_value=True), \
             patch.object(auth0, "_prompt_for_discourse_sign_in", return_value=False), \
             patch.object(auth0, "clear_external_login_state") as clear_login_state, \
             patch.object(auth0, "login_auth0") as login_auth0:
            result = auth0.get_or_refresh_tokens()

        self.assertIsNone(result)
        clear_login_state.assert_called_once_with()
        login_auth0.assert_not_called()

    def test_cached_invalid_token_refreshes_after_discourse_sign_in(self):
        invalid_tokens = _tokens({"aud": "https://docs.sima.ai", "permissions": [auth0.DOC_ACCESS_GRANT]})
        valid_tokens = _tokens({
            "aud": ["https://docs.sima.ai", auth0.PROD_USERINFO_AUDIENCE],
            "permissions": [auth0.DOC_ACCESS_GRANT, auth0.LATEST_EULA_GRANT],
        })
        auth_cfg = {"CLIENT_ID": "client", "TOKEN_URL": "https://auth.example/oauth/token"}

        with patch.object(auth0, "get_auth_config", return_value=auth_cfg), \
             patch.object(auth0, "load_tokens", return_value=invalid_tokens), \
             patch.object(auth0, "is_token_valid", return_value=True), \
             patch.object(auth0, "_prompt_for_discourse_sign_in", return_value=True), \
             patch.object(auth0, "clear_external_login_state") as clear_login_state, \
             patch.object(auth0, "refresh_access_token", return_value=valid_tokens) as refresh_access_token, \
             patch.object(auth0, "login_auth0") as login_auth0:
            result = auth0.get_or_refresh_tokens()

        self.assertIs(result, valid_tokens)
        clear_login_state.assert_not_called()
        refresh_access_token.assert_called_once_with(auth_cfg, "refresh-token")
        login_auth0.assert_not_called()

    def test_cached_invalid_token_logs_out_when_refresh_token_is_unavailable(self):
        invalid_tokens = _tokens({"aud": "https://docs.sima.ai", "permissions": [auth0.DOC_ACCESS_GRANT]})
        invalid_tokens.pop("refresh_token")

        with patch.object(auth0, "get_auth_config", return_value={}), \
             patch.object(auth0, "load_tokens", return_value=invalid_tokens), \
             patch.object(auth0, "is_token_valid", return_value=True), \
             patch.object(auth0, "_prompt_for_discourse_sign_in", return_value=True), \
             patch.object(auth0, "clear_external_login_state") as clear_login_state, \
             patch.object(auth0, "refresh_access_token") as refresh_access_token, \
             patch.object(auth0, "login_auth0") as login_auth0:
            result = auth0.get_or_refresh_tokens()

        self.assertIsNone(result)
        clear_login_state.assert_called_once_with()
        refresh_access_token.assert_not_called()
        login_auth0.assert_not_called()

    def test_cached_token_without_doc_access_returns_limited_access_without_retry(self):
        limited_tokens = _tokens({
            "aud": "https://docs.sima.ai",
            "permissions": [auth0.LATEST_EULA_GRANT],
        })

        with patch.object(auth0, "get_auth_config", return_value={}), \
             patch.object(auth0, "load_tokens", return_value=limited_tokens), \
             patch.object(auth0, "is_token_valid", return_value=True), \
             patch.object(auth0, "_prompt_for_discourse_sign_in") as prompt_for_discourse, \
             patch.object(auth0, "refresh_access_token") as refresh_access_token, \
             patch.object(auth0, "login_auth0") as login_auth0:
            result = auth0.get_or_refresh_tokens()

        self.assertIs(result, limited_tokens)
        prompt_for_discourse.assert_not_called()
        refresh_access_token.assert_not_called()
        login_auth0.assert_not_called()


if __name__ == "__main__":
    unittest.main()
