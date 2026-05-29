import unittest
from unittest.mock import Mock, patch

from sima_cli.auth import devportal


class _FakeCookies:
    def set(self, *args, **kwargs):
        pass


class _FakeSession:
    def __init__(self, response, name="session"):
        self.headers = {}
        self.cookies = _FakeCookies()
        self._response = response
        self.name = name

    def get(self, *args, **kwargs):
        return self._response


class _FakeResponse:
    status_code = 302
    headers = {"Location": "https://docs.sima.ai/login?show-request-form=1"}


class _FakePostResponse:
    def raise_for_status(self):
        pass


class TestDevportalLogin(unittest.TestCase):
    def test_request_access_redirect_does_not_recurse_login(self):
        fake_session = _FakeSession(_FakeResponse())

        with patch.object(devportal.requests, "Session", return_value=fake_session), \
             patch.object(devportal, "get_cached_access_token", return_value="access-token"), \
             patch.object(devportal, "_submit_access_request", return_value=False) as submit_access_request, \
             patch.object(devportal, "login_external") as login_external:
            session, valid = devportal.validate_session()

        self.assertIs(session, fake_session)
        self.assertFalse(valid)
        submit_access_request.assert_called_once_with()
        login_external.assert_not_called()

    def test_request_access_redirect_submits_form_from_identity_claims(self):
        fake_session = _FakeSession(_FakeResponse())
        claims = {
            "https://auth.sima.ai/user_info": {
                "company": "test ignore",
                "country": "United States",
                "email": "user@example.com",
                "family_name": "Ignore",
                "first_name": "Test",
                "industry": "Aerospace and Defense",
            },
            "given_name": "Test",
            "family_name": "Ignore",
        }
        post = Mock(return_value=_FakePostResponse())

        with patch.object(devportal.requests, "Session", return_value=fake_session), \
             patch.object(devportal, "get_cached_access_token", return_value="access-token"), \
             patch.object(devportal, "_load_identity_claims", return_value=claims), \
             patch.object(devportal, "_has_submitted_access_request", return_value=False), \
             patch.object(devportal, "_mark_access_request_submitted") as mark_submitted, \
             patch.object(devportal, "_show_access_request_info_panel") as show_panel, \
             patch.object(devportal, "_logout_external_credentials") as logout_external_credentials, \
             patch.object(devportal.click, "prompt", return_value="Building a robotics demo"), \
             patch.object(devportal.requests, "post", post), \
             patch.object(devportal, "login_external") as login_external:
            session, valid = devportal.validate_session()

        self.assertIs(session, fake_session)
        self.assertFalse(valid)
        login_external.assert_not_called()
        post.assert_called_once_with(
            devportal.ACCESS_REQUEST_FORM_URL,
            data={
                "message": "Building a robotics demo",
                "first_name": "Test",
                "last_name": "Ignore",
                "email": "user@example.com",
                "company": "test ignore",
                "country": "United States",
                "account_type": "Prospect",
                "industry": "Aerospace and Defense",
            },
            timeout=15,
        )
        mark_submitted.assert_called_once_with(claims)
        show_panel.assert_called_once_with()
        logout_external_credentials.assert_called_once_with()

    def test_request_access_redirect_does_not_prompt_after_submission(self):
        fake_session = _FakeSession(_FakeResponse())
        claims = {
            "sub": "google-oauth2|123",
            "https://auth.sima.ai/user_info": {"email": "user@example.com"},
        }

        with patch.object(devportal.requests, "Session", return_value=fake_session), \
             patch.object(devportal, "get_cached_access_token", return_value="access-token"), \
             patch.object(devportal, "_load_identity_claims", return_value=claims), \
             patch.object(devportal, "_has_submitted_access_request", return_value=True), \
             patch.object(devportal, "_show_access_request_info_panel") as show_panel, \
             patch.object(devportal, "_logout_external_credentials") as logout_external_credentials, \
             patch.object(devportal.click, "prompt") as prompt, \
             patch.object(devportal.requests, "post") as post, \
             patch.object(devportal, "login_external") as login_external:
            session, valid = devportal.validate_session()

        self.assertIs(session, fake_session)
        self.assertFalse(valid)
        show_panel.assert_not_called()
        prompt.assert_not_called()
        post.assert_not_called()
        logout_external_credentials.assert_called_once_with()
        login_external.assert_not_called()

    def test_login_external_stops_after_access_request_handler(self):
        fake_session = _FakeSession(_FakeResponse())

        def validate_session():
            devportal._ACCESS_REQUEST_HANDLED = True
            return fake_session, False

        try:
            with patch.object(devportal, "validate_session", side_effect=validate_session) as validate, \
                 patch.object(devportal, "get_or_refresh_tokens") as get_or_refresh_tokens:
                result = devportal.login_external()
        finally:
            devportal._ACCESS_REQUEST_HANDLED = False

        self.assertIsNone(result)
        validate.assert_called_once_with()
        get_or_refresh_tokens.assert_not_called()

    def test_login_external_shows_pending_banner_for_limited_access_user(self):
        limited_tokens = {"access_token": "limited-token"}

        with patch.object(devportal, "validate_session", return_value=(None, False)) as validate, \
             patch.object(devportal, "get_or_refresh_tokens", return_value=limited_tokens) as get_tokens, \
             patch.object(devportal, "access_token_has_doc_access", return_value=False) as has_doc_access, \
             patch.object(devportal, "_show_limited_access_pending_message") as show_pending:
            result = devportal.login_external()

        self.assertIsNone(result)
        get_tokens.assert_called_once_with(force=False)
        has_doc_access.assert_called_once_with(limited_tokens)
        show_pending.assert_called_once_with()
        validate.assert_called_once_with()

    def test_eula_flow_opens_developer_portal_login_page(self):
        with patch.object(devportal.webbrowser, "open", return_value=True) as browser_open, \
             patch.object(devportal.click, "confirm", return_value=False), \
             patch.object(devportal.click, "echo") as echo:
            result = devportal._handle_eula_flow(session=Mock(), username="", domain="")

        self.assertFalse(result)
        browser_open.assert_called_once_with(devportal.DEV_PORTAL_LOGIN_URL)
        output = "\n".join(str(call.args[0]) for call in echo.call_args_list if call.args)
        self.assertIn(devportal.DEV_PORTAL_LOGIN_URL, output)
        self.assertNotIn(devportal.DUMMY_CHECK_URL, output)

    def test_eula_flow_prints_developer_portal_login_when_browser_does_not_open(self):
        with patch.object(devportal.webbrowser, "open", return_value=False) as browser_open, \
             patch.object(devportal.click, "confirm", return_value=False), \
             patch.object(devportal.click, "echo") as echo:
            result = devportal._handle_eula_flow(session=Mock(), username="", domain="")

        self.assertFalse(result)
        browser_open.assert_called_once_with(devportal.DEV_PORTAL_LOGIN_URL)
        output = "\n".join(str(call.args[0]) for call in echo.call_args_list if call.args)
        self.assertIn(f"Open this sign-in page manually: {devportal.DEV_PORTAL_LOGIN_URL}", output)
        self.assertNotIn(devportal.DUMMY_CHECK_URL, output)


if __name__ == "__main__":
    unittest.main()
