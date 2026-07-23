import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from sima_cli.cli import main
from sima_cli.install.metadata_installer import InstallationPreflightError, MetadataAccessForbidden
from sima_cli.install import metadata_installer
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
        self.headers = []

    def read_bytes(self, url, headers=None):
        self.urls.append(url)
        self.headers.append(headers or {})
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
    def test_metadata_download_only_stops_before_install_side_effects(self):
        metadata = {"name": "demo", "version": "1.0", "resources": ["package.tar.gz"]}
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            metadata_installer, "_download_and_validate_metadata", return_value=(metadata, tmp)
        ) as metadata_mock, patch.object(
            metadata_installer, "_check_whether_disk_is_big_enough", return_value=True
        ), patch.object(
            metadata_installer, "_is_platform_compatible", return_value=True
        ) as compatibility_mock, patch.object(
            metadata_installer, "_download_assets", return_value=[str(Path(tmp) / "package.tar.gz")]
        ) as download_mock, patch.object(
            metadata_installer, "_run_installation_script"
        ) as install_script_mock, patch.object(
            metadata_installer.registry, "create_entry"
        ) as registry_mock:
            result = metadata_installer.install_from_metadata(
                "https://example.invalid/metadata.json",
                internal=False,
                install_dir=tmp,
                download_only=True,
            )

        self.assertEqual(result, [str(Path(tmp) / "package.tar.gz")])
        self.assertFalse(metadata_mock.call_args.kwargs["check_compatibility"])
        download_mock.assert_called_once()
        compatibility_mock.assert_not_called()
        install_script_mock.assert_not_called()
        registry_mock.assert_not_called()

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
        self.assertEqual(parse_install_target("internals"), ("internals", "", "main", "latest"))

    def test_parse_install_target_accepts_subfolder(self):
        self.assertEqual(
            parse_install_target("model-compiler/examples@fix/compile-resnet50-docs-env:88caac51885b"),
            ("model-compiler", "examples", "fix/compile-resnet50-docs-env", "88caac51885b"),
        )

    def test_parse_install_target_accepts_nested_subfolder(self):
        self.assertEqual(
            parse_install_target("model-compiler/examples/resnet50@main:latest"),
            ("model-compiler", "examples/resnet50", "main", "latest"),
        )

    def test_parse_install_target_accepts_colon_branch_and_spec(self):
        self.assertEqual(
            parse_install_target("internals@release/2.1:50649e9aa0ba"),
            ("internals", "", "release/2.1", "50649e9aa0ba"),
        )

    def test_parse_install_target_accepts_main_commit_shorthand(self):
        self.assertEqual(
            parse_install_target("internals@50649e9aa0ba"),
            ("internals", "", "main", "50649e9aa0ba"),
        )

    def test_parse_install_target_treats_tag_as_ref(self):
        self.assertEqual(parse_install_target("internals@2.0.0"), ("internals", "", "2.0.0", "latest"))

    def test_parse_install_target_rejects_unsafe_subfolder(self):
        with self.assertRaisesRegex(VulcanArtifactError, "package path"):
            parse_install_target("model-compiler/../examples@main")

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
        self.assertEqual(result.package_path, "")
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

    def test_resolve_install_metadata_url_uses_subfolder_metadata(self):
        base_url = "https://example.invalid"
        client = FakeClient({})

        result = resolve_install_metadata_url(
            environment="dev",
            target="model-compiler/examples@fix/compile-resnet50-docs-env:88caac51885b",
            base_url=base_url,
            client=client,
        )

        self.assertEqual(result.repository, "model-compiler")
        self.assertEqual(result.package_path, "examples")
        self.assertEqual(client.urls, [])
        self.assertEqual(
            result.metadata_url,
            f"{base_url}/model-compiler/fix%252Fcompile-resnet50-docs-env/88caac51885b/examples/metadata.json",
        )

    def test_resolve_install_metadata_url_uses_subfolder_metadata_for_latest(self):
        base_url = "https://example.invalid"
        latest_url = f"{base_url}/model-compiler/fix%252Fcompile-resnet50-docs-env/latest.tag"
        client = FakeClient({latest_url: "88caac51885b\n"})

        result = resolve_install_metadata_url(
            environment="dev",
            target="model-compiler/examples@fix/compile-resnet50-docs-env",
            base_url=base_url,
            client=client,
        )

        self.assertEqual(client.urls, [latest_url])
        self.assertEqual(
            result.metadata_url,
            f"{base_url}/model-compiler/fix%252Fcompile-resnet50-docs-env/88caac51885b/examples/metadata.json",
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

    def test_resolve_install_metadata_url_requires_latest_tag_for_latest_spec(self):
        base_url = "https://example.invalid"
        client = FakeClient({})

        with self.assertRaisesRegex(VulcanArtifactError, "latest.tag"):
            resolve_install_metadata_url(
                environment="dev",
                target="internals@2.0.0",
                base_url=base_url,
                client=client,
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
        self.assertIn("Download a Neat package's metadata resources", result.output)
        self.assertIn("TARGET", result.output)
        self.assertIn("--env [dev|stg|staging|prd|prod|production]", result.output)
        self.assertIn("--stg, --staging", result.output)
        self.assertIn("--prd, --prod", result.output)

    def test_neat_group_accepts_env_before_download(self):
        runner = CliRunner()
        with patch("sima_cli.vulcan.commands.install_vulcan_package") as install_mock:
            result = runner.invoke(main, ["neat", "--env", "dev", "download", "core"])

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(install_mock.call_args.kwargs["environment"], "dev")
        self.assertTrue(install_mock.call_args.kwargs["download_only"])
        self.assertIsNone(install_mock.call_args.kwargs["base_url"])

    def test_neat_artifacts_preserves_manifest_downloader(self):
        runner = CliRunner()
        with patch("sima_cli.vulcan.commands.download_vulcan_artifacts") as download_mock:
            download_mock.return_value = (_fake_result(), None)
            result = runner.invoke(main, ["neat", "artifacts", "core", "main"])

        self.assertEqual(result.exit_code, 0, result.output)
        download_mock.assert_called_once()

    def test_neat_install_download_only_forwards_download_mode(self):
        runner = CliRunner()
        with patch("sima_cli.vulcan.commands.install_vulcan_package") as install_mock:
            result = runner.invoke(main, ["neat", "install", "core", "--download-only"])

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertTrue(install_mock.call_args.kwargs["download_only"])

    def test_neat_sdk_forwards_passthrough_arguments(self):
        runner = CliRunner()
        with patch("sima_cli.sdk.commands.launch_sdk_tool") as launch_mock:
            result = runner.invoke(main, ["neat", "sdk", "python", "--version"])

        self.assertEqual(result.exit_code, 0, result.output)
        launch_mock.assert_called_once()
        self.assertEqual(launch_mock.call_args.args[0], "neat")
        self.assertEqual(launch_mock.call_args.args[1], ("python", "--version"))

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

    def test_vulcan_download_accepts_production_alias(self):
        runner = CliRunner()
        with patch("sima_cli.vulcan.commands.download_vulcan_artifacts") as download_mock:
            download_mock.return_value = (
                _fake_result(environment="production", base_url=ENV_BASE_URLS["production"]),
                None,
            )
            result = runner.invoke(main, ["vulcan", "--env", "prod", "download", "core", "main"])

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(download_mock.call_args.kwargs["environment"], "production")

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
                    "package_path": "",
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
            command_name="sima-cli vulcan install",
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
                    "package_path": "",
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
            command_name="sima-cli vulcan install",
        )

    def test_neat_install_prints_subfolder_package_path(self):
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
                    "repository": "model-compiler",
                    "package_path": "examples",
                    "ref": "fix/compile-resnet50-docs-env",
                    "ref_key": "fix%2Fcompile-resnet50-docs-env",
                    "requested_spec": "88caac51885b",
                    "resolved_spec": "88caac51885b",
                    "metadata_url": (
                        f"{ENV_BASE_URLS['dev']}/model-compiler/"
                        "fix%252Fcompile-resnet50-docs-env/88caac51885b/examples/metadata.json"
                    ),
                },
            )()

            result = runner.invoke(
                main,
                [
                    "neat",
                    "--env",
                    "dev",
                    "install",
                    "model-compiler/examples@fix/compile-resnet50-docs-env:88caac51885b",
                ],
            )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("Repository:  model-compiler", result.output)
        self.assertIn("Package:     examples", result.output)
        self.assertEqual(
            resolve_mock.call_args.kwargs["target"],
            "model-compiler/examples@fix/compile-resnet50-docs-env:88caac51885b",
        )
        install_mock.assert_called_once()

    def test_neat_install_uses_neat_command_name_for_install_messages(self):
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
                    "package_path": "",
                    "ref": "main",
                    "ref_key": "main",
                    "requested_spec": "latest",
                    "resolved_spec": "50649e9aa0ba",
                    "metadata_url": f"{ENV_BASE_URLS['dev']}/internals/main/50649e9aa0ba/metadata.json",
                },
            )()

            result = runner.invoke(main, ["neat", "--env", "dev", "install", "internals"])

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(install_mock.call_args.kwargs["command_name"], "sima-cli neat install")

    def test_neat_install_preflight_panel_uses_neat_command_name(self):
        runner = CliRunner()
        with patch("sima_cli.vulcan.commands.resolve_install_metadata_url") as resolve_mock, patch(
            "sima_cli.install.metadata_installer.tempfile.NamedTemporaryFile",
            side_effect=PermissionError("denied"),
        ):
            resolve_mock.return_value = type(
                "Result",
                (),
                {
                    "environment": "dev",
                    "base_url": ENV_BASE_URLS["dev"],
                    "repository": "internals",
                    "package_path": "",
                    "ref": "main",
                    "ref_key": "main",
                    "requested_spec": "latest",
                    "resolved_spec": "50649e9aa0ba",
                    "metadata_url": f"{ENV_BASE_URLS['dev']}/internals/main/50649e9aa0ba/metadata.json",
                },
            )()

            result = runner.invoke(main, ["neat", "--env", "dev", "install", "internals"])

        self.assertNotEqual(result.exit_code, 0, result.output)
        self.assertIn("Installation Failed", result.output)
        self.assertIn("sima-cli neat install ...", result.output)
        self.assertIn("sima-cli neat install ... --install-dir", result.output)
        self.assertNotIn("sima-cli install ...", result.output)

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
                    "package_path": "",
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
        self.assertEqual(payload["package_path"], "")
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
            command_name="sima-cli install",
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

    def test_top_level_install_vulcan_alias_still_works(self):
        runner = CliRunner()
        with patch("sima_cli.cli.install_vulcan_package") as install_mock:
            result = runner.invoke(main, ["install", "--vulcan", "--env", "dev", "internals"])

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(install_mock.call_args.kwargs["target"], "internals")

    def test_top_level_install_help_shows_vulcan_alias(self):
        runner = CliRunner()
        result = runner.invoke(main, ["install", "--help"])

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("--vulcan", result.output)

    def test_top_level_install_vulcan_json_forwards_without_installing_legacy_path(self):
        runner = CliRunner()
        with patch("sima_cli.cli.install_vulcan_package") as install_mock:
            result = runner.invoke(main, ["install", "--neat", "--env", "dev", "--json", "internals"])

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertTrue(install_mock.call_args.kwargs["json_output"])

    def test_top_level_install_metadata_url_forwards_install_dir(self):
        runner = CliRunner()
        with patch("sima_cli.cli.install_from_metadata") as install_mock:
            result = runner.invoke(
                main,
                [
                    "install",
                    "--mirror",
                    "https://example.invalid/package/metadata.json",
                    "--install-dir",
                    "downloads",
                ],
            )

        self.assertEqual(result.exit_code, 0, result.output)
        install_mock.assert_called_once_with(
            metadata_url="https://example.invalid/package/metadata.json",
            internal=False,
            install_dir="downloads",
            force=False,
            command_name="sima-cli install",
        )

    def test_top_level_install_resolved_metadata_forwards_install_dir(self):
        runner = CliRunner()
        with patch(
            "sima_cli.cli.metadata_resolver",
            return_value="https://example.invalid/component/metadata.json",
        ), patch("sima_cli.cli.install_from_metadata") as install_mock:
            result = runner.invoke(main, ["install", "samples/llima", "-v", "1.7.0", "-d", "downloads"])

        self.assertEqual(result.exit_code, 0, result.output)
        install_mock.assert_called_once_with(
            metadata_url="https://example.invalid/component/metadata.json",
            internal=False,
            install_dir="downloads",
            force=False,
            command_name="sima-cli install",
        )

    def test_top_level_install_preflight_error_is_not_wrapped_as_resolution_failure(self):
        runner = CliRunner()
        with patch(
            "sima_cli.cli.metadata_resolver",
            return_value="https://example.invalid/component/metadata.json",
        ), patch(
            "sima_cli.cli.install_from_metadata",
            side_effect=InstallationPreflightError("Current directory '/' is not writable."),
        ):
            result = runner.invoke(main, ["install", "tools/oob", "-v", "1.7.0"])

        self.assertNotEqual(result.exit_code, 0, result.output)
        self.assertIn("Installation Failed", result.output)
        self.assertIn("Current directory '/' is not writable.", result.output)
        self.assertNotIn("Failed to resolve metadata", result.output)

    def test_top_level_install_switches_neat_target_after_metadata_403(self):
        runner = CliRunner()
        with patch(
            "sima_cli.cli.metadata_resolver",
            return_value="https://docs.sima.ai/pkg_downloads/SDK2.1.0/core/metadata.json",
        ), patch(
            "sima_cli.cli.install_from_metadata",
            side_effect=MetadataAccessForbidden("HTTP 403"),
        ), patch("sima_cli.cli.install_vulcan_package") as neat_install_mock:
            result = runner.invoke(main, ["install", "core/runtime", "-v", "2.1.0"])

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("Switching to Neat Install", result.output)
        self.assertIn("sima-cli neat install core/runtime", result.output)
        self.assertIn("use sima-cli neat install in the future", result.output)
        neat_install_mock.assert_called_once_with(
            target="core/runtime",
            environment="production",
            package_type=None,
            install_dir=".",
            force=False,
            json_output=False,
            command_name="sima-cli neat install",
        )

    def test_top_level_install_switches_when_nested_metadata_download_raises_403(self):
        runner = CliRunner()
        with patch(
            "sima_cli.cli.metadata_resolver",
            return_value="https://docs.sima.ai/pkg_downloads/SDK2.1.2/core/metadata.json",
        ), patch(
            "sima_cli.install.metadata_installer._download_and_validate_metadata",
            side_effect=MetadataAccessForbidden("HTTP 403"),
        ), patch("sima_cli.cli.install_vulcan_package") as neat_install_mock:
            result = runner.invoke(main, ["install", "core"])

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("Switching to Neat Install", result.output)
        neat_install_mock.assert_called_once()

    def test_top_level_install_does_not_switch_unknown_target_after_metadata_403(self):
        runner = CliRunner()
        with patch(
            "sima_cli.cli.metadata_resolver",
            return_value="https://docs.sima.ai/pkg_downloads/SDK2.1.0/other/metadata.json",
        ), patch(
            "sima_cli.cli.install_from_metadata",
            side_effect=MetadataAccessForbidden("HTTP 403"),
        ), patch("sima_cli.cli.install_vulcan_package") as neat_install_mock:
            result = runner.invoke(main, ["install", "other", "-v", "2.1.0"])

        self.assertNotEqual(result.exit_code, 0, result.output)
        self.assertNotIn("Switching to Neat Install", result.output)
        neat_install_mock.assert_not_called()

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
