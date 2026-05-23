import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from sima_cli.cli import main
from sima_cli.install.package_builder import build_metadata, metadata_filename, parse_selectables, resolve_install_script


class PackageBuilderTests(unittest.TestCase):
    def test_build_metadata_collects_artifacts_and_checksums(self):
        with tempfile.TemporaryDirectory() as tmp:
            artifacts = Path(tmp)
            (artifacts / "install.sh").write_text("#!/bin/sh\n", encoding="utf-8")
            (artifacts / "models").mkdir()
            (artifacts / "models" / "model.bin").write_bytes(b"model")
            (artifacts / "metadata.json").write_text("{}", encoding="utf-8")

            with patch("sima_cli.install.package_builder.resolve_git_context", return_value=(None, None)), \
                 patch("sima_cli.install.package_builder.datetime") as mock_datetime:
                mock_datetime.datetime.now.return_value.strftime.return_value = "20260520112233"
                metadata = build_metadata(
                    artifacts_folder=artifacts,
                    install_script="install.sh",
                    description=None,
                )

            self.assertEqual(metadata["name"], Path.cwd().name)
            self.assertEqual(metadata["version"], "20260520112233")
            self.assertEqual(metadata["release"], "")
            self.assertEqual(metadata["platforms"], [])
            self.assertEqual(metadata["selectable-resources"], [])
            self.assertEqual(metadata["installation"]["script"], "./install.sh")
            self.assertEqual(metadata["installation"]["post-message"], "[bold]Package installed successfully.[/bold]\n")
            self.assertEqual(metadata["resources"], ["install.sh", "models/model.bin"])
            self.assertEqual(set(metadata["resources-checksum"]), {"install.sh", "models/model.bin"})
            self.assertRegex(metadata["resources-checksum"]["install.sh"], r"^[a-f0-9]{64}$")
            self.assertEqual(metadata["size"]["download"], metadata["size"]["install"])

    def test_build_metadata_ignores_metadata_variants(self):
        with tempfile.TemporaryDirectory() as tmp:
            artifacts = Path(tmp)
            (artifacts / "install.sh").write_text("#!/bin/sh\n", encoding="utf-8")
            (artifacts / "payload.txt").write_text("payload", encoding="utf-8")
            (artifacts / "metadata.json").write_text("{}", encoding="utf-8")
            (artifacts / "metadata-full.json").write_text("{}", encoding="utf-8")

            with patch("sima_cli.install.package_builder.resolve_git_context", return_value=(None, None)):
                metadata = build_metadata(
                    artifacts_folder=artifacts,
                    name="demo",
                    version="1.0.0",
                    install_script="install.sh",
                )

            self.assertEqual(metadata["resources"], ["install.sh", "payload.txt"])

    def test_build_metadata_excludes_partial_resource_matches(self):
        with tempfile.TemporaryDirectory() as tmp:
            artifacts = Path(tmp)
            (artifacts / "install.sh").write_text("#!/bin/sh\n", encoding="utf-8")
            (artifacts / "neat-runtime.deb").write_bytes(b"runtime")
            (artifacts / "neat-internals-dev.deb").write_bytes(b"internals")
            (artifacts / "models").mkdir()
            (artifacts / "models" / "large-model.tar.gz").write_bytes(b"model")

            with patch("sima_cli.install.package_builder.resolve_git_context", return_value=(None, None)):
                metadata = build_metadata(
                    artifacts_folder=artifacts,
                    name="demo",
                    version="1.0.0",
                    install_script="install.sh",
                    exclude=("internals", "large-model"),
                )

            self.assertEqual(metadata["resources"], ["install.sh", "neat-runtime.deb"])
            self.assertEqual(set(metadata["resources-checksum"]), {"install.sh", "neat-runtime.deb"})
            self.assertEqual(metadata["size"]["download"], metadata["size"]["install"])

    def test_build_metadata_can_request_compatible_file_downloads_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            artifacts = Path(tmp)
            (artifacts / "install.sh").write_text("#!/bin/sh\n", encoding="utf-8")
            (artifacts / "demo-1.0.0-py3-none-any.whl").write_bytes(b"wheel")

            with patch("sima_cli.install.package_builder.resolve_git_context", return_value=(None, None)):
                metadata = build_metadata(
                    artifacts_folder=artifacts,
                    name="demo",
                    version="1.0.0",
                    install_script="install.sh",
                    download_compatible_files_only=True,
                )

            self.assertTrue(metadata["download-compatible-files-only"])

    def test_build_metadata_rejects_selectable_excluded_by_pattern(self):
        with tempfile.TemporaryDirectory() as tmp:
            artifacts = Path(tmp)
            (artifacts / "install.sh").write_text("#!/bin/sh\n", encoding="utf-8")
            (artifacts / "demo-full.tar.gz").write_bytes(b"demo")

            with self.assertRaisesRegex(ValueError, "selectable resource is not in artifacts-folder"):
                build_metadata(
                    artifacts_folder=artifacts,
                    name="demo",
                    version="1.0.0",
                    install_script="install.sh",
                    selectables="Demo:demo-full.tar.gz",
                    exclude=("full",),
                )

    def test_metadata_filename_supports_variants(self):
        self.assertEqual(metadata_filename(None), "metadata.json")
        self.assertEqual(metadata_filename(""), "metadata.json")
        self.assertEqual(metadata_filename("full"), "metadata-full.json")
        self.assertEqual(metadata_filename("minimum_2.0"), "metadata-minimum_2.0.json")

    def test_metadata_filename_rejects_unsafe_variant(self):
        with self.assertRaisesRegex(ValueError, "variant"):
            metadata_filename("../full")

    def test_install_script_uses_command_when_no_artifact_file_matches(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = resolve_install_script(Path(tmp), "bash ./install.sh && echo done")
            self.assertEqual(script, "bash ./install.sh && echo done")

    def test_selectables_are_removed_from_resources_and_added_with_checksum(self):
        with tempfile.TemporaryDirectory() as tmp:
            artifacts = Path(tmp)
            (artifacts / "install.sh").write_text("#!/bin/sh\n", encoding="utf-8")
            (artifacts / "payload.txt").write_text("payload", encoding="utf-8")
            (artifacts / "demo.tar.gz").write_bytes(b"demo")

            with patch("sima_cli.install.package_builder.resolve_git_context", return_value=(None, None)):
                metadata = build_metadata(
                    artifacts_folder=artifacts,
                    name="demo",
                    version="1.0.0",
                    install_script="install.sh",
                    selectables="Demo Web App:demo.tar.gz",
                )

            self.assertEqual(metadata["resources"], ["install.sh", "payload.txt"])
            self.assertNotIn("demo.tar.gz", metadata["resources-checksum"])
            self.assertEqual(len(metadata["selectable-resources"]), 1)
            selectable = metadata["selectable-resources"][0]
            self.assertEqual(selectable["name"], "Demo Web App")
            self.assertEqual(selectable["url"], "")
            self.assertEqual(selectable["resource"], "demo.tar.gz")
            self.assertRegex(selectable["checksum"], r"^[a-f0-9]{64}$")

    def test_selectables_reject_missing_artifact_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            artifacts = Path(tmp)
            (artifacts / "install.sh").write_text("#!/bin/sh\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "selectable resource is not in artifacts-folder"):
                build_metadata(
                    artifacts_folder=artifacts,
                    name="demo",
                    version="1.0.0",
                    install_script="install.sh",
                    selectables="Missing:missing.tar.gz",
                )

    def test_parse_selectables_rejects_invalid_format(self):
        with self.assertRaisesRegex(ValueError, "name:file"):
            parse_selectables("Demo")

    def test_defaults_use_github_repo_and_exact_tag(self):
        with tempfile.TemporaryDirectory() as tmp:
            artifacts = Path(tmp)
            (artifacts / "install.sh").write_text("#!/bin/sh\n", encoding="utf-8")

            def fake_git(args, cwd):
                if args == ["describe", "--tags", "--exact-match"]:
                    return "v1.2.3"
                return None

            with patch("sima_cli.install.package_builder.resolve_git_context", return_value=(artifacts, ("sima-neat", "core"))), \
                 patch("sima_cli.install.package_builder._run_git", side_effect=fake_git), \
                 patch("sima_cli.install.package_builder.github_repo_description", return_value="Core repo"):
                metadata = build_metadata(artifacts, install_script="install.sh")

            self.assertEqual(metadata["name"], "gh:sima-neat/core")
            self.assertEqual(metadata["version"], "v1.2.3")
            self.assertEqual(metadata["description"], "Core repo")

    def test_packages_build_command_writes_metadata_json(self):
        runner = CliRunner()
        with tempfile.TemporaryDirectory() as tmp:
            artifacts = Path(tmp)
            (artifacts / "install.sh").write_text("#!/bin/sh\n", encoding="utf-8")
            (artifacts / "payload.txt").write_text("payload", encoding="utf-8")
            (artifacts / "optional.bin").write_text("optional", encoding="utf-8")

            with patch("sima_cli.install.package_builder.resolve_git_context", return_value=(None, None)):
                result = runner.invoke(
                    main,
                    [
                        "packages",
                        "build",
                        str(artifacts),
                        "--name",
                        "demo",
                        "--version",
                        "1.0.0",
                        "--description",
                        "Demo package",
                        "--install-script",
                        "install.sh",
                        "--selectables",
                        "Optional Payload:optional.bin",
                        "--exclude",
                        "ignored",
                    ],
                )

            self.assertEqual(result.exit_code, 0, result.output)
            metadata_path = artifacts / "metadata.json"
            self.assertTrue(metadata_path.exists())
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            self.assertEqual(metadata["name"], "demo")
            self.assertEqual(metadata["version"], "1.0.0")
            self.assertEqual(metadata["description"], "Demo package")
            self.assertEqual(metadata["installation"]["script"], "./install.sh")
            self.assertEqual(metadata["selectable-resources"][0]["name"], "Optional Payload")
            self.assertEqual(metadata["selectable-resources"][0]["resource"], "optional.bin")

    def test_packages_build_command_writes_compatible_download_flag(self):
        runner = CliRunner()
        with tempfile.TemporaryDirectory() as tmp:
            artifacts = Path(tmp)
            (artifacts / "install.sh").write_text("#!/bin/sh\n", encoding="utf-8")
            (artifacts / "demo-1.0.0-py3-none-any.whl").write_bytes(b"wheel")

            with patch("sima_cli.install.package_builder.resolve_git_context", return_value=(None, None)):
                result = runner.invoke(
                    main,
                    [
                        "packages",
                        "build",
                        str(artifacts),
                        "--name",
                        "demo",
                        "--version",
                        "1.0.0",
                        "--install-script",
                        "install.sh",
                        "--download-compatible-files-only",
                    ],
                )

            self.assertEqual(result.exit_code, 0, result.output)
            metadata = json.loads((artifacts / "metadata.json").read_text(encoding="utf-8"))
            self.assertTrue(metadata["download-compatible-files-only"])

    def test_packages_build_command_writes_variant_metadata_json(self):
        runner = CliRunner()
        with tempfile.TemporaryDirectory() as tmp:
            artifacts = Path(tmp)
            (artifacts / "install.sh").write_text("#!/bin/sh\n", encoding="utf-8")
            (artifacts / "payload.txt").write_text("payload", encoding="utf-8")

            with patch("sima_cli.install.package_builder.resolve_git_context", return_value=(None, None)):
                result = runner.invoke(
                    main,
                    [
                        "packages",
                        "build",
                        str(artifacts),
                        "--name",
                        "demo",
                        "--version",
                        "1.0.0",
                        "--install-script",
                        "install.sh",
                        "--variant",
                        "minimum",
                    ],
                )

            self.assertEqual(result.exit_code, 0, result.output)
            self.assertFalse((artifacts / "metadata.json").exists())
            metadata_path = artifacts / "metadata-minimum.json"
            self.assertTrue(metadata_path.exists())
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            self.assertEqual(metadata["name"], "demo")
            self.assertEqual(metadata["resources"], ["install.sh", "payload.txt"])

    def test_packages_build_command_excludes_partial_matches(self):
        runner = CliRunner()
        with tempfile.TemporaryDirectory() as tmp:
            artifacts = Path(tmp)
            (artifacts / "install.sh").write_text("#!/bin/sh\n", encoding="utf-8")
            (artifacts / "neat-runtime.deb").write_text("runtime", encoding="utf-8")
            (artifacts / "neat-internals-dev.deb").write_text("internals", encoding="utf-8")
            (artifacts / "debug-symbols.tar.gz").write_text("debug", encoding="utf-8")

            with patch("sima_cli.install.package_builder.resolve_git_context", return_value=(None, None)):
                result = runner.invoke(
                    main,
                    [
                        "packages",
                        "build",
                        str(artifacts),
                        "--name",
                        "demo",
                        "--version",
                        "1.0.0",
                        "--install-script",
                        "install.sh",
                        "--exclude",
                        "internals",
                        "--exclude",
                        "debug",
                        "--variant",
                        "minimum",
                    ],
                )

            self.assertEqual(result.exit_code, 0, result.output)
            metadata = json.loads((artifacts / "metadata-minimum.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["resources"], ["install.sh", "neat-runtime.deb"])
            self.assertEqual(set(metadata["resources-checksum"]), {"install.sh", "neat-runtime.deb"})

    def test_packages_build_command_rejects_unsafe_variant(self):
        runner = CliRunner()
        with tempfile.TemporaryDirectory() as tmp:
            artifacts = Path(tmp)
            (artifacts / "install.sh").write_text("#!/bin/sh\n", encoding="utf-8")

            result = runner.invoke(
                main,
                [
                    "packages",
                    "build",
                    str(artifacts),
                    "--name",
                    "demo",
                    "--version",
                    "1.0.0",
                    "--install-script",
                    "install.sh",
                    "--variant",
                    "../minimum",
                ],
            )

            self.assertNotEqual(result.exit_code, 0, result.output)
            self.assertIn("variant must contain only", result.output)


if __name__ == "__main__":
    unittest.main()
