import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from sima_cli.cli import main
from sima_cli.vulcan.artifacts import (
    DownloadResult,
    ENV_BASE_URLS,
    VulcanArtifactError,
    download_vulcan_artifacts,
    join_url,
    ref_key,
    repository_choices,
    select_from_menu,
)


def _fake_result(environment="production", base_url="https://example.invalid"):
    output_dir = Path("vulcan-downloads") / environment / "core" / "main" / "abcdef0"
    return DownloadResult(
        environment=environment,
        base_url=base_url,
        repository="core",
        ref="main",
        ref_key="main",
        latest_tag="abcdef0",
        manifest_url=f"{base_url}/core/main/manifest.json",
        output_dir=output_dir,
        files=(output_dir / "latest.tag",),
    )


class FakeClient:
    def __init__(self, payloads):
        self.payloads = payloads
        self.urls = []

    def read_bytes(self, url):
        self.urls.append(url)
        try:
            payload = self.payloads[url]
        except KeyError as exc:
            raise VulcanArtifactError(f"missing fake URL: {url}") from exc
        if isinstance(payload, bytes):
            return payload
        return payload.encode("utf-8")

    def read_text(self, url):
        return self.read_bytes(url).decode("utf-8")

    def read_json(self, url):
        return json.loads(self.read_text(url))


class VulcanArtifactTests(unittest.TestCase):
    def test_environment_urls_match_vulcan_domains(self):
        self.assertEqual(ENV_BASE_URLS["dev"], "https://artifacts.neat.paconsultings.com")
        self.assertEqual(ENV_BASE_URLS["staging"], "https://artifacts.stg.neat.sima.ai")
        self.assertEqual(ENV_BASE_URLS["production"], "https://artifacts.neat.sima.ai")

    def test_ref_key_url_encodes_branch_slashes(self):
        self.assertEqual(ref_key("feature/foo"), "feature%2Ffoo")

    def test_join_url_escapes_literal_percent_in_branch_key(self):
        self.assertEqual(
            join_url("https://example.invalid/", "core", "feature%2Ffoo", "latest.tag"),
            "https://example.invalid/core/feature%252Ffoo/latest.tag",
        )

    def test_repository_choices_can_be_overridden(self):
        old_value = os.environ.get("SIMA_VULCAN_REPOS")
        try:
            os.environ["SIMA_VULCAN_REPOS"] = "zeta, alpha,alpha"
            self.assertEqual(repository_choices(), ["alpha", "zeta"])
        finally:
            if old_value is None:
                os.environ.pop("SIMA_VULCAN_REPOS", None)
            else:
                os.environ["SIMA_VULCAN_REPOS"] = old_value

    def test_menu_requires_interactive_stdin(self):
        with self.assertRaisesRegex(VulcanArtifactError, "stdin is not interactive"):
            select_from_menu("Repositories", ["core"])

    def test_download_fetches_latest_manifest_and_artifact(self):
        artifact = b"artifact-bytes"
        digest = hashlib.sha256(artifact).hexdigest()
        base_url = "https://example.invalid"
        manifest = {
            "commit": "abcdef0123456789",
            "artifacts": [
                {
                    "path": "package.tar.gz",
                    "s3_key": "core/main/package.tar.gz",
                    "size": len(artifact),
                    "sha256": digest,
                }
            ],
        }
        client = FakeClient(
            {
                f"{base_url}/core/main/latest.tag": "abcdef0\n",
                f"{base_url}/core/main/manifest.json": json.dumps(manifest),
                f"{base_url}/core/main/package.tar.gz": artifact,
            }
        )

        with tempfile.TemporaryDirectory() as tmp:
            result, warning = download_vulcan_artifacts(
                environment="production",
                repository="core",
                ref="main",
                output=tmp,
                base_url=base_url,
                client=client,
            )

            self.assertIsNone(warning)
            self.assertEqual(result.latest_tag, "abcdef0")
            self.assertEqual(result.output_dir, Path(tmp) / "production" / "core" / "main" / "abcdef0")
            self.assertEqual((result.output_dir / "package.tar.gz").read_bytes(), artifact)
            self.assertEqual((result.output_dir / "latest.tag").read_text(), "abcdef0\n")
            self.assertTrue((result.output_dir / "manifest.json").exists())


class VulcanCommandTests(unittest.TestCase):
    def test_vulcan_download_help_is_registered(self):
        runner = CliRunner()
        result = runner.invoke(main, ["vulcan", "download", "--help"])

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("Download artifacts for REPO", result.output)
        self.assertIn("--env [dev|production|staging]", result.output)

    def test_vulcan_group_accepts_env_before_download(self):
        runner = CliRunner()
        with patch("sima_cli.vulcan.commands.download_vulcan_artifacts") as download_mock:
            download_mock.return_value = (
                _fake_result(environment="dev", base_url=ENV_BASE_URLS["dev"]),
                None,
            )
            result = runner.invoke(main, ["vulcan", "--env", "dev", "download", "core", "main"])

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(download_mock.call_args.kwargs["environment"], "dev")
        self.assertIsNone(download_mock.call_args.kwargs["base_url"])

    def test_vulcan_download_env_overrides_group_env(self):
        runner = CliRunner()
        with patch("sima_cli.vulcan.commands.download_vulcan_artifacts") as download_mock:
            download_mock.return_value = (
                _fake_result(environment="dev", base_url=ENV_BASE_URLS["dev"]),
                None,
            )
            result = runner.invoke(
                main,
                ["vulcan", "--env", "production", "download", "--env", "dev", "core", "main"],
            )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(download_mock.call_args.kwargs["environment"], "dev")

    def test_vulcan_download_rejects_unavailable_environments(self):
        runner = CliRunner()
        with patch("sima_cli.vulcan.commands.download_vulcan_artifacts") as download_mock:
            result = runner.invoke(main, ["vulcan", "--env", "staging", "download", "core", "main"])

        self.assertNotEqual(result.exit_code, 0, result.output)
        self.assertIn("Vulcan staging environment is not yet available to use", result.output)
        download_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
