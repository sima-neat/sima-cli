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
        "expires_in": 3600,
        "timestamp": 1,
    }


class TestAuth0AccessTokenRequirements(unittest.TestCase):
    def test_validates_latest_eula_and_userinfo_audience(self):
        tokens = _tokens({
            "aud": ["https://docs.sima.ai", auth0.USERINFO_AUDIENCE],
            "permissions": [auth0.LATEST_EULA_GRANT],
        })

        valid, checks = auth0._validate_access_token_requirements(tokens)

        self.assertTrue(valid)
        self.assertEqual(checks, {"latest_eula": True, "userinfo_audience": True})

    def test_detects_missing_latest_eula_and_userinfo_audience(self):
        tokens = _tokens({"aud": "https://docs.sima.ai", "permissions": []})

        valid, checks = auth0._validate_access_token_requirements(tokens)

        self.assertFalse(valid)
        self.assertEqual(checks, {"latest_eula": False, "userinfo_audience": False})

    def test_scope_claim_can_supply_latest_eula_grant(self):
        tokens = _tokens({
            "aud": auth0.USERINFO_AUDIENCE,
            "scope": f"openid profile {auth0.LATEST_EULA_GRANT}",
        })

        valid, checks = auth0._validate_access_token_requirements(tokens)

        self.assertTrue(valid)
        self.assertEqual(checks, {"latest_eula": True, "userinfo_audience": True})

    def test_discourse_url_uses_staging_when_enabled(self):
        with patch.dict("os.environ", {"USE_STAGING_DEV_PORTAL": "true"}):
            self.assertEqual(auth0._discourse_sign_in_url(), auth0.STAGING_DISCOURSE_URL)

    def test_cached_invalid_token_logs_out_when_user_declines_discourse_sign_in(self):
        invalid_tokens = _tokens({"aud": "https://docs.sima.ai", "permissions": []})

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

    def test_cached_invalid_token_retries_login_after_discourse_sign_in(self):
        invalid_tokens = _tokens({"aud": "https://docs.sima.ai", "permissions": []})
        valid_tokens = _tokens({
            "aud": ["https://docs.sima.ai", auth0.USERINFO_AUDIENCE],
            "permissions": [auth0.LATEST_EULA_GRANT],
        })
        auth_cfg = {"CLIENT_ID": "client"}

        with patch.object(auth0, "get_auth_config", return_value=auth_cfg), \
             patch.object(auth0, "load_tokens", return_value=invalid_tokens), \
             patch.object(auth0, "is_token_valid", return_value=True), \
             patch.object(auth0, "_prompt_for_discourse_sign_in", return_value=True), \
             patch.object(auth0, "clear_external_login_state") as clear_login_state, \
             patch.object(auth0, "login_auth0", return_value=valid_tokens) as login_auth0:
            result = auth0.get_or_refresh_tokens()

        self.assertIs(result, valid_tokens)
        clear_login_state.assert_called_once_with()
        login_auth0.assert_called_once_with(auth_cfg)


if __name__ == "__main__":
    unittest.main()
