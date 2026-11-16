import base64
import io
import json
import unittest
from unittest.mock import Mock, patch

import requests

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
    def test_poll_for_token_announces_waiting_on_stderr_before_polling(self):
        auth_cfg = {"CLIENT_ID": "client", "TOKEN_URL": "https://auth.example/token"}
        response = Mock(status_code=200)
        response.json.return_value = {"access_token": "token"}
        seen_before_first_poll = []

        def first_sleep(_interval):
            seen_before_first_poll.append(stderr.getvalue())

        with patch("sys.stderr", new_callable=io.StringIO) as stderr, \
             patch.object(auth0.time, "sleep", side_effect=first_sleep), \
             patch.object(auth0.requests, "post", return_value=response), \
             patch.object(auth0, "save_tokens"):
            tokens = auth0.poll_for_token(auth_cfg, "device-code", 5)

        self.assertEqual(tokens["access_token"], "token")
        self.assertIn("Waiting for authorization", seen_before_first_poll[0])
        self.assertIn("sima-cli authorized", stderr.getvalue())

    def test_poll_for_token_keeps_polling_after_a_network_timeout(self):
        auth_cfg = {"CLIENT_ID": "client", "TOKEN_URL": "https://auth.example/token"}
        authorized = Mock(status_code=200)
        authorized.json.return_value = {"access_token": "token"}

        with patch("sys.stderr", new_callable=io.StringIO) as stderr, \
             patch.object(auth0.time, "sleep"), \
             patch.object(auth0.requests, "post",
                          side_effect=[requests.Timeout("read timed out"), authorized]) as post, \
             patch.object(auth0, "save_tokens"):
            tokens = auth0.poll_for_token(auth_cfg, "device-code", 5, expires_in=900)

        self.assertEqual(tokens["access_token"], "token")
        self.assertEqual(post.call_count, 2)
        for call in post.call_args_list:
            self.assertEqual(call.kwargs["timeout"], auth0.AUTH_HTTP_TIMEOUT)
        self.assertIn("Login server did not respond (Timeout)", stderr.getvalue())

    def test_poll_for_token_stops_when_the_device_code_expires(self):
        auth_cfg = {"CLIENT_ID": "client", "TOKEN_URL": "https://auth.example/token"}
        clock = iter([0, 400, 901])  # deadline set at 0; first poll at 400 s; next check past 900 s

        with patch("sys.stderr", new_callable=io.StringIO) as stderr, \
             patch.object(auth0.time, "sleep"), \
             patch.object(auth0.time, "monotonic", side_effect=lambda: next(clock)), \
             patch.object(auth0.requests, "post",
                          side_effect=requests.ConnectionError("network is unreachable")) as post, \
             self.assertRaises(SystemExit):
            auth0.poll_for_token(auth_cfg, "device-code", 5, expires_in=900)

        self.assertEqual(post.call_count, 1)
        self.assertIn("Device code expired", stderr.getvalue())

    def test_request_device_code_uses_a_timeout(self):
        auth_cfg = {"CLIENT_ID": "client", "SCOPES": "openid", "AUDIENCE": "aud",
                    "DEVICE_CODE_URL": "https://auth.example/device"}
        response = Mock(status_code=200)
        response.json.return_value = {"device_code": "device-code"}

        with patch.object(auth0.requests, "post", return_value=response) as post:
            self.assertEqual(auth0.request_device_code(auth_cfg), {"device_code": "device-code"})

        self.assertEqual(post.call_args.kwargs["timeout"], auth0.AUTH_HTTP_TIMEOUT)

    def test_request_device_code_reports_an_unreachable_server(self):
        auth_cfg = {"CLIENT_ID": "client", "SCOPES": "openid", "AUDIENCE": "aud",
                    "DEVICE_CODE_URL": "https://auth.example/device"}

        with patch("sys.stderr", new_callable=io.StringIO) as stderr, \
             patch.object(auth0.requests, "post", side_effect=requests.ConnectTimeout("timed out")), \
             self.assertRaises(requests.ConnectTimeout):
            auth0.request_device_code(auth_cfg)

        self.assertIn("Could not reach the login server", stderr.getvalue())

    def test_welcome_message_goes_to_stderr(self):
        payload = base64.urlsafe_b64encode(
            json.dumps({"name": "Dev", "email": "dev@example.com"}).encode()
        ).decode().rstrip("=")

        with patch("sys.stdout", new_callable=io.StringIO) as stdout, \
             patch("sys.stderr", new_callable=io.StringIO) as stderr:
            auth0.print_welcome_message({"id_token": f"header.{payload}.sig"})

        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("Welcome back", stderr.getvalue())

    def test_login_auth0_prints_actionable_manual_browser_instructions(self):
        auth_cfg = {"CLIENT_ID": "client"}
        device_code_response = {
            "verification_uri": "https://auth.example/activate",
            "verification_uri_complete": "https://auth.example/activate?user_code=WGCB-ZWVF",
            "user_code": "WGCB-ZWVF",
            "device_code": "device-code",
            "expires_in": 900,
            "interval": 5,
        }

        with patch.object(auth0, "request_device_code", return_value=device_code_response), \
             patch.object(auth0, "is_browser_available", return_value=False), \
             patch.object(auth0, "poll_for_token", return_value={"access_token": "token"}) as poll_for_token, \
             patch("sys.stdout", new_callable=io.StringIO) as stdout, \
             patch("sys.stderr", new_callable=io.StringIO) as stderr:
            result = auth0.login_auth0(auth_cfg)

        self.assertEqual(result, {"access_token": "token"})
        poll_for_token.assert_called_once_with(auth_cfg, "device-code", 5, expires_in=900)
        # Login prompts go to stderr so they are not mixed into a command's stdout result.
        self.assertEqual(stdout.getvalue(), "")
        output = stderr.getvalue()
        self.assertIn("authorize this device login", output)
        self.assertIn("A browser could not be opened", output)
        self.assertIn("Action required", output)
        self.assertIn("Open this URL in a browser", output)
        self.assertIn("Sign in with your SiMa Developer Portal account", output)
        self.assertIn("Return to this terminal", output)
        self.assertIn("15 minutes", output)
        self.assertIn("WGCB-ZWVF", output)
        self.assertIn("https://auth.example/activate?user_code=WGCB-ZWVF", output)

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
