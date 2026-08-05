import os
import subprocess
import unittest
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from sima_cli.utils import container_registries


def _pull_error(message):
    return subprocess.CalledProcessError(
        1,
        ["docker", "pull", "ghcr.io/owner/private:latest"],
        stderr=message,
    )


class ContainerRegistryAuthTests(unittest.TestCase):
    def test_classifies_ghcr_auth_errors(self):
        for message in (
            "unauthorized",
            "authentication required",
            "error from registry: denied",
            "unexpected status: 403 Forbidden",
        ):
            with self.subTest(message=message):
                self.assertTrue(
                    container_registries._is_ghcr_auth_error(_pull_error(message))
                )

        self.assertFalse(
            container_registries._is_ghcr_auth_error(
                _pull_error("manifest unknown")
            )
        )

    def test_normalizes_github_token_prefix(self):
        self.assertEqual(
            container_registries._normalize_github_token("Bearer secret"),
            "secret",
        )
        self.assertEqual(
            container_registries._normalize_github_token("token secret"),
            "secret",
        )

    def test_explicit_github_user_overrides_actions_actor(self):
        with patch.dict(
            os.environ,
            {
                "GITHUB_USER": "configured-user",
                "GITHUB_ACTOR": "actions-actor",
            },
            clear=True,
        ):
            self.assertEqual(
                container_registries._github_username_for_token("secret"),
                "configured-user",
            )

    @patch.object(container_registries, "docker_login_with_token")
    def test_authorize_ghcr_uses_environment_token(self, login):
        with patch.dict(
            os.environ,
            {"GITHUB_TOKEN": "secret", "GITHUB_USER": "octocat"},
            clear=True,
        ):
            self.assertTrue(container_registries._authorize_ghcr())

        login.assert_called_once_with("octocat", "secret", "ghcr.io")

    @patch.object(container_registries, "docker_login_with_token")
    @patch.object(
        container_registries,
        "_github_credentials_from_gh",
        return_value=("octocat", "secret"),
    )
    def test_authorize_ghcr_uses_github_cli(self, credentials, login):
        with patch.dict(os.environ, {}, clear=True):
            self.assertTrue(container_registries._authorize_ghcr())

        credentials.assert_called_once_with()
        login.assert_called_once_with("octocat", "secret", "ghcr.io")

    @patch.object(container_registries, "_run_quiet", return_value=True)
    @patch.object(container_registries.shutil, "which", return_value="/usr/bin/gh")
    @patch.object(container_registries, "_gh_output")
    @patch.object(container_registries.sys.stdin, "isatty", return_value=True)
    @patch.object(container_registries.subprocess, "run")
    def test_refreshes_existing_gh_authorization_for_packages(
        self, run, isatty, gh_output, which, run_quiet
    ):
        run.return_value = MagicMock(returncode=0)
        gh_output.side_effect = ["octocat", "secret"]

        self.assertEqual(
            container_registries._github_credentials_from_gh(),
            ("octocat", "secret"),
        )

        run_quiet.assert_called_once_with(
            ["gh", "auth", "status", "--hostname", "github.com"]
        )
        run.assert_called_once_with(
            [
                "gh",
                "auth",
                "refresh",
                "--hostname",
                "github.com",
                "--scopes",
                "read:packages",
            ],
            check=False,
        )

    @patch.object(container_registries, "_run_quiet", return_value=True)
    @patch.object(container_registries.shutil, "which", return_value="/usr/bin/gh")
    @patch.object(container_registries, "_gh_output", side_effect=["octocat", "secret"])
    @patch.object(container_registries.sys.stdin, "isatty", return_value=False)
    @patch.object(container_registries.subprocess, "run")
    def test_noninteractive_existing_gh_authorization_does_not_prompt(
        self, run, isatty, gh_output, which, run_quiet
    ):
        self.assertEqual(
            container_registries._github_credentials_from_gh(),
            ("octocat", "secret"),
        )

        run.assert_not_called()


class ContainerRegistryInstallTests(unittest.TestCase):
    def setUp(self):
        self.runner = CliRunner()

    def _invoke_install(self):
        @container_registries.click.command()
        def command():
            container_registries.install_from_cr(
                "ghcr:sima-vertical-solutions/ros2-sdk"
            )

        return self.runner.invoke(command)

    @patch.object(container_registries, "check_and_start_docker", return_value=True)
    @patch.object(container_registries, "ensure_docker_available", return_value=True)
    @patch.object(container_registries, "_authorize_ghcr", return_value=True)
    @patch.object(container_registries, "_pull_container_from_registry")
    def test_private_ghcr_authenticates_and_retries_once(
        self, pull, authorize, ensure_docker, start_docker
    ):
        image = "ghcr.io/sima-vertical-solutions/ros2-sdk:latest"
        pull.side_effect = [_pull_error("unauthorized"), image]

        result = self._invoke_install()

        self.assertEqual(result.exit_code, 0, result.output)
        authorize.assert_called_once_with()
        self.assertEqual(pull.call_count, 2)
        self.assertIn("Retrying container image pull", result.output)
        self.assertNotIn("docker login", result.output.lower())

    @patch.object(container_registries, "check_and_start_docker", return_value=True)
    @patch.object(container_registries, "ensure_docker_available", return_value=True)
    @patch.object(container_registries, "_authorize_ghcr", return_value=False)
    @patch.object(container_registries, "_pull_container_from_registry")
    def test_missing_authorization_does_not_retry(
        self, pull, authorize, ensure_docker, start_docker
    ):
        pull.side_effect = _pull_error("unauthorized")

        result = self._invoke_install()

        self.assertNotEqual(result.exit_code, 0)
        self.assertEqual(pull.call_count, 1)
        self.assertIn("GitHub authorization is required", result.output)
        self.assertNotIn("docker login", result.output.lower())

    @patch.object(container_registries, "check_and_start_docker", return_value=True)
    @patch.object(container_registries, "ensure_docker_available", return_value=True)
    @patch.object(container_registries, "_authorize_ghcr")
    @patch.object(container_registries, "_pull_container_from_registry")
    def test_public_ghcr_does_not_prompt_for_authorization(
        self, pull, authorize, ensure_docker, start_docker
    ):
        pull.return_value = "ghcr.io/sima-vertical-solutions/ros2-sdk:latest"

        result = self._invoke_install()

        self.assertEqual(result.exit_code, 0, result.output)
        authorize.assert_not_called()
        pull.assert_called_once_with(
            "ghcr.io", "sima-vertical-solutions/ros2-sdk:latest"
        )

    @patch.object(container_registries, "check_and_start_docker", return_value=True)
    @patch.object(container_registries, "ensure_docker_available", return_value=True)
    @patch.object(container_registries, "_authorize_ghcr", return_value=True)
    @patch.object(container_registries, "_pull_container_from_registry")
    def test_rejected_retry_reports_github_access_without_docker_guidance(
        self, pull, authorize, ensure_docker, start_docker
    ):
        pull.side_effect = [
            _pull_error("unauthorized"),
            _pull_error("denied"),
        ]

        result = self._invoke_install()

        self.assertNotEqual(result.exit_code, 0)
        self.assertEqual(pull.call_count, 2)
        self.assertIn("read:packages", result.output)
        self.assertIn("SSO", result.output)
        self.assertNotIn("docker login", result.output.lower())


if __name__ == "__main__":
    unittest.main()
