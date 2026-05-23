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
    parse_install_target,
    ref_key,
    repository_choices,
    resolve_install_metadata_url,
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

    def read_bytes(self, url, headers=None):
        self.urls.append(url)
        try:
            payload = self.payloads[url]
        except KeyError as exc:
            raise VulcanArtifactError(f"missing fake URL: {url}") from exc
        if isinstance(payload, bytes):
            return payload
        return payload.encode("utf-8")

    def read_text(self, url, headers=None):
        return self.read_bytes(url, headers=headers).decode("utf-8")

    def read_json(self, url, headers=None):
        return json.loads(self.read_text(url, headers=headers))


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

    def test_parse_install_target_defaults_to_latest_main(self):
        self.assertEqual(parse_install_target("internals"), ("internals", "main", "latest"))

    def test_parse_install_target_accepts_colon_branch_and_spec(self):
        self.assertEqual(
            parse_install_target("internals@release/2.1:50649e9aa0ba"),
            ("internals", "release/2.1", "50649e9aa0ba"),
        )

    def test_parse_install_target_accepts_main_commit_shorthand(self):
        self.assertEqual(
            parse_install_target("internals@50649e9aa0ba"),
            ("internals", "main", "50649e9aa0ba"),
        )

    def test_parse_install_target_treats_tag_as_ref(self):
        self.assertEqual(parse_install_target("internals@2.0.0"), ("internals", "2.0.0", "latest"))

    def test_resolve_install_metadata_url_resolves_latest_tag(self):
        base_url = "https://example.invalid"
        client = FakeClient({f"{base_url}/internals/main/latest.tag": "50649e9aa0ba\n"})

        result = resolve_install_metadata_url(
            environment="dev",
            target="internals",
            base_url=base_url,
            client=client,
        )

        self.assertEqual(result.repository, "internals")
        self.assertEqual(result.ref, "main")
        self.assertEqual(result.requested_spec, "latest")
        self.assertEqual(result.resolved_spec, "50649e9aa0ba")
        self.assertEqual(
            result.metadata_url,
            f"{base_url}/internals/main/50649e9aa0ba/metadata.json",
        )

    def test_resolve_install_metadata_url_uses_explicit_spec_without_latest_lookup(self):
        base_url = "https://example.invalid"
        client = FakeClient({})

        result = resolve_install_metadata_url(
            environment="dev",
            target="internals@vulcan-prep:50649e9aa0ba",
            base_url=base_url,
            client=client,
        )

        self.assertEqual(client.urls, [])
        self.assertEqual(
            result.metadata_url,
            f"{base_url}/internals/vulcan-prep/50649e9aa0ba/metadata.json",
        )

    def test_resolve_install_metadata_url_uses_metadata_type_variant(self):
        base_url = "https://example.invalid"
        client = FakeClient({f"{base_url}/internals/main/latest.tag": "50649e9aa0ba\n"})

        result = resolve_install_metadata_url(
            environment="dev",
            target="internals@main",
            base_url=base_url,
            package_type="minimum",
            client=client,
        )

        self.assertEqual(
            result.metadata_url,
            f"{base_url}/internals/main/50649e9aa0ba/metadata-minimum.json",
        )

    def test_resolve_install_metadata_url_rejects_unsafe_metadata_type(self):
        base_url = "https://example.invalid"
        client = FakeClient({f"{base_url}/internals/main/latest.tag": "50649e9aa0ba\n"})

        with self.assertRaisesRegex(VulcanArtifactError, "metadata type"):
            resolve_install_metadata_url(
                environment="dev",
                target="internals@main",
                base_url=base_url,
                package_type="../minimum",
                client=client,
            )

    def test_resolve_install_metadata_url_falls_back_to_github_tag_commit(self):
        base_url = "https://example.invalid"
        tag_sha = "1234567890abcdef1234567890abcdef12345678"
        client = FakeClient({
            "https://api.github.com/repos/sima-neat/internals/commits/2.0.0": json.dumps({"sha": tag_sha}),
        })

        result = resolve_install_metadata_url(
            environment="dev",
            target="internals@2.0.0",
            base_url=base_url,
            client=client,
        )

        self.assertEqual(result.ref, "2.0.0")
        self.assertEqual(result.requested_spec, "latest")
        self.assertEqual(result.resolved_spec, "1234567890ab")
        self.assertEqual(
            result.metadata_url,
            f"{base_url}/internals/2.0.0/1234567890ab/metadata.json",
        )


class VulcanCommandTests(unittest.TestCase):
    def test_neat_command_is_visible_and_vulcan_alias_is_hidden(self):
        runner = CliRunner()
        result = runner.invoke(main, ["--help"])

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("neat", result.output)
        self.assertNotIn("vulcan", result.output)

    def test_neat_download_help_is_registered(self):
        runner = CliRunner()
        result = runner.invoke(main, ["neat", "download", "--help"])

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("Download artifacts for REPO", result.output)
        self.assertIn("--env [dev|stg|staging|prd|prod|production]", result.output)
        self.assertIn("--stg, --staging", result.output)
        self.assertIn("--prd, --prod", result.output)

    def test_neat_group_accepts_env_before_download(self):
        runner = CliRunner()
        with patch("sima_cli.vulcan.commands.download_vulcan_artifacts") as download_mock:
            download_mock.return_value = (
                _fake_result(environment="dev", base_url=ENV_BASE_URLS["dev"]),
                None,
            )
            result = runner.invoke(main, ["neat", "--env", "dev", "download", "core", "main"])

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(download_mock.call_args.kwargs["environment"], "dev")
        self.assertIsNone(download_mock.call_args.kwargs["base_url"])

    def test_vulcan_download_help_is_registered(self):
        runner = CliRunner()
        result = runner.invoke(main, ["vulcan", "download", "--help"])

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("Download artifacts for REPO", result.output)
        self.assertIn("--env [dev|stg|staging|prd|prod|production]", result.output)

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

    def test_vulcan_group_normalizes_staging_alias_before_download(self):
        runner = CliRunner()
        with patch("sima_cli.vulcan.commands.download_vulcan_artifacts") as download_mock:
            download_mock.return_value = (
                _fake_result(environment="staging", base_url=ENV_BASE_URLS["staging"]),
                None,
            )
            result = runner.invoke(main, ["vulcan", "--env", "stg", "download", "core", "main"])

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(download_mock.call_args.kwargs["environment"], "staging")

    def test_vulcan_group_accepts_staging_shortcut_before_download(self):
        runner = CliRunner()
        with patch("sima_cli.vulcan.commands.download_vulcan_artifacts") as download_mock:
            download_mock.return_value = (
                _fake_result(environment="staging", base_url=ENV_BASE_URLS["staging"]),
                None,
            )
            result = runner.invoke(main, ["vulcan", "--stg", "download", "core", "main"])

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(download_mock.call_args.kwargs["environment"], "staging")

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

    def test_vulcan_download_rejects_unavailable_production_alias(self):
        runner = CliRunner()
        with patch("sima_cli.vulcan.commands.download_vulcan_artifacts") as download_mock:
            result = runner.invoke(main, ["vulcan", "--env", "prod", "download", "core", "main"])

        self.assertNotEqual(result.exit_code, 0, result.output)
        self.assertIn("Artifact environment 'production' is not yet available to use", result.output)
        download_mock.assert_not_called()

    def test_vulcan_install_help_is_registered(self):
        runner = CliRunner()
        result = runner.invoke(main, ["vulcan", "install", "--help"])

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("Install a Neat artifact package from TARGET", result.output)

    def test_vulcan_install_resolves_metadata_and_installs(self):
        runner = CliRunner()
        with patch("sima_cli.vulcan.commands.resolve_install_metadata_url") as resolve_mock, patch(
            "sima_cli.vulcan.commands.install_from_metadata"
        ) as install_mock:
            resolve_mock.return_value = type(
                "Result",
                (),
                {
                    "environment": "dev",
                    "base_url": ENV_BASE_URLS["dev"],
                    "repository": "internals",
                    "ref": "main",
                    "ref_key": "main",
                    "requested_spec": "latest",
                    "resolved_spec": "50649e9aa0ba",
                    "metadata_url": f"{ENV_BASE_URLS['dev']}/internals/main/50649e9aa0ba/metadata.json",
                },
            )()

            result = runner.invoke(main, ["vulcan", "--env", "dev", "install", "internals"])

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(resolve_mock.call_args.kwargs["environment"], "dev")
        self.assertEqual(resolve_mock.call_args.kwargs["target"], "internals")
        self.assertIsNone(resolve_mock.call_args.kwargs["package_type"])
        install_mock.assert_called_once_with(
            metadata_url=f"{ENV_BASE_URLS['dev']}/internals/main/50649e9aa0ba/metadata.json",
            internal=False,
            install_dir=".",
            force=False,
        )

    def test_vulcan_install_forwards_metadata_type(self):
        runner = CliRunner()
        with patch("sima_cli.vulcan.commands.resolve_install_metadata_url") as resolve_mock, patch(
            "sima_cli.vulcan.commands.install_from_metadata"
        ) as install_mock:
            resolve_mock.return_value = type(
                "Result",
                (),
                {
                    "environment": "dev",
                    "base_url": ENV_BASE_URLS["dev"],
                    "repository": "internals",
                    "ref": "main",
                    "ref_key": "main",
                    "requested_spec": "latest",
                    "resolved_spec": "50649e9aa0ba",
                    "metadata_url": f"{ENV_BASE_URLS['dev']}/internals/main/50649e9aa0ba/metadata-minimum.json",
                },
            )()

            result = runner.invoke(main, ["vulcan", "--env", "dev", "install", "internals@main", "-t", "minimum"])

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(resolve_mock.call_args.kwargs["package_type"], "minimum")
        install_mock.assert_called_once_with(
            metadata_url=f"{ENV_BASE_URLS['dev']}/internals/main/50649e9aa0ba/metadata-minimum.json",
            internal=False,
            install_dir=".",
            force=False,
        )

    def test_vulcan_install_json_prints_metadata_without_installing(self):
        runner = CliRunner()
        with patch("sima_cli.vulcan.commands.resolve_install_metadata_url") as resolve_mock, patch(
            "sima_cli.vulcan.commands.install_from_metadata"
        ) as install_mock:
            resolve_mock.return_value = type(
                "Result",
                (),
                {
                    "environment": "dev",
                    "base_url": ENV_BASE_URLS["dev"],
                    "repository": "internals",
                    "ref": "main",
                    "ref_key": "main",
                    "requested_spec": "latest",
                    "resolved_spec": "50649e9aa0ba",
                    "metadata_url": f"{ENV_BASE_URLS['dev']}/internals/main/50649e9aa0ba/metadata.json",
                },
            )()

            result = runner.invoke(main, ["vulcan", "--env", "dev", "install", "internals", "--json"])

        self.assertEqual(result.exit_code, 0, result.output)
        payload = json.loads(result.output[result.output.index("{"):])
        self.assertEqual(
            payload["metadata_url"],
            f"{ENV_BASE_URLS['dev']}/internals/main/50649e9aa0ba/metadata.json",
        )
        install_mock.assert_not_called()

    def test_top_level_install_forwards_neat_options(self):
        runner = CliRunner()
        with patch("sima_cli.cli.install_vulcan_package") as install_mock:
            result = runner.invoke(
                main,
                [
                    "install",
                    "--neat",
                    "--env",
                    "dev",
                    "--base-url",
                    "https://artifacts.example.invalid",
                    "-d",
                    "tmp",
                    "-f",
                    "-t",
                    "minimum",
                    "internals@vulcan-prep:f47d4e286bca",
                ],
            )

        self.assertEqual(result.exit_code, 0, result.output)
        install_mock.assert_called_once_with(
            target="internals@vulcan-prep:f47d4e286bca",
            environment="dev",
            base_url="https://artifacts.example.invalid",
            package_type="minimum",
            install_dir="tmp",
            force=True,
            json_output=False,
        )

    def test_top_level_install_normalizes_staging_alias(self):
        runner = CliRunner()
        with patch("sima_cli.cli.install_vulcan_package") as install_mock:
            result = runner.invoke(main, ["install", "--neat", "--env", "stg", "internals"])

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(install_mock.call_args.kwargs["environment"], "staging")

    def test_top_level_install_accepts_staging_shortcut(self):
        runner = CliRunner()
        with patch("sima_cli.cli.install_vulcan_package") as install_mock:
            result = runner.invoke(main, ["install", "--neat", "--staging", "internals"])

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(install_mock.call_args.kwargs["environment"], "staging")

    def test_top_level_install_hidden_vulcan_alias_still_works(self):
        runner = CliRunner()
        with patch("sima_cli.cli.install_vulcan_package") as install_mock:
            result = runner.invoke(main, ["install", "--vulcan", "--env", "dev", "internals"])

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(install_mock.call_args.kwargs["target"], "internals")

    def test_top_level_install_vulcan_json_forwards_without_installing_legacy_path(self):
        runner = CliRunner()
        with patch("sima_cli.cli.install_vulcan_package") as install_mock:
            result = runner.invoke(main, ["install", "--neat", "--env", "dev", "--json", "internals"])

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertTrue(install_mock.call_args.kwargs["json_output"])

    def test_top_level_install_vulcan_requires_target(self):
        runner = CliRunner()
        result = runner.invoke(main, ["install", "--neat", "--env", "dev"])

        self.assertNotEqual(result.exit_code, 0, result.output)
        self.assertIn("You must specify a Neat target", result.output)

    def test_sdk_help_is_visible(self):
        runner = CliRunner()
        result = runner.invoke(main, ["--help"])

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("sdk", result.output)
        self.assertIn("neat", result.output)
        self.assertNotIn("vulcan", result.output)


if __name__ == "__main__":
    unittest.main()
