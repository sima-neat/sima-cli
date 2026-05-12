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


if __name__ == "__main__":
    unittest.main()
