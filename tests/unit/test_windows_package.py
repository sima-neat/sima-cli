import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from sima_cli.cli import main
from sima_cli.install.metadata_validator import MetadataValidationError, validate_metadata
from sima_cli.install.windows_package import (
    WindowsNativeInstallError,
    WindowsPackageRestartRequired,
    WindowsPythonInstallError,
    install_windows_package,
)
from sima_cli.vulcan.commands import install_vulcan_package


def completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr=stderr)


class WindowsPackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.msi = self.root / "sima-pcie-host-0.6.1-win64.msi"
        self.wheel = self.root / "pyneatpcie-0.6.1-cp314-cp314-win_amd64.whl"
        self.msi.write_bytes(b"msi fixture")
        self.scheduler_msi = self.root / "simaai-scheduler-pcie-host-0.6.1-win64.msi"
        self.scheduler_msi.write_bytes(b"scheduler msi fixture")
        self.wheel.write_bytes(b"wheel fixture")
        self.python = self.root / "python.exe"
        self.python.write_bytes(b"python fixture")
        self.metadata = {
            "name": "gh:sima-neat/core/pciehost/windows/amd64",
            "version": "0.6.1",
            "release": "develop",
            "platforms": [{"type": "host", "os": ["windows"], "arch": ["amd64"]}],
            "resources": [self.msi.name, self.wheel.name],
            "installation": {
                "type": "windows-msi",
                "msi": self.msi.name,
                "python": {
                    "wheel": self.wheel.name,
                    "distribution": "pyneatpcie",
                    "version": "0.6.1",
                    "import": "pyneatpcie",
                },
            },
        }

    def tearDown(self):
        self.temp.cleanup()

    def test_metadata_accepts_declarative_msi_and_wheel(self):
        self.assertTrue(validate_metadata(self.metadata))

    def test_metadata_rejects_msi_not_in_resources(self):
        self.metadata["resources"].remove(self.msi.name)
        with self.assertRaisesRegex(MetadataValidationError, "installation.msi"):
            validate_metadata(self.metadata)

    def test_native_only_invokes_msiexec_and_does_not_invoke_pip(self):
        with patch("sima_cli.install.windows_package._is_windows", return_value=True), patch(
            "sima_cli.install.windows_package._run", return_value=completed()
        ) as run:
            result = install_windows_package(self.metadata, str(self.root))

        self.assertEqual(result["python"], "not-requested")
        self.assertEqual(run.call_count, 1)
        command = run.call_args.args[0]
        self.assertEqual(command[:3], ["msiexec.exe", "/i", str(self.msi)])
        self.assertIn("/norestart", command)
        self.assertIn("/L*v", command)

    def test_coordinated_native_packages_install_in_manifest_order(self):
        self.metadata["resources"].insert(0, self.scheduler_msi.name)
        self.metadata["installation"].pop("msi")
        self.metadata["installation"]["native-packages"] = [
            {
                "owner": "scheduler",
                "msi": self.scheduler_msi.name,
                "version": "0.6.1",
            },
            {"owner": "core", "msi": self.msi.name, "version": "0.6.1"},
        ]

        self.assertTrue(validate_metadata(self.metadata))
        with patch("sima_cli.install.windows_package._is_windows", return_value=True), patch(
            "sima_cli.install.windows_package._run", return_value=completed()
        ) as run:
            result = install_windows_package(self.metadata, str(self.root))

        self.assertEqual(
            [package["owner"] for package in result["native_packages"]],
            ["scheduler", "core"],
        )
        self.assertEqual(run.call_args_list[0].args[0][2], str(self.scheduler_msi))
        self.assertEqual(run.call_args_list[1].args[0][2], str(self.msi))

    def test_coordinated_native_failure_reports_completed_packages(self):
        self.metadata["resources"].insert(0, self.scheduler_msi.name)
        self.metadata["installation"].pop("msi")
        self.metadata["installation"]["native-packages"] = [
            {
                "owner": "scheduler",
                "msi": self.scheduler_msi.name,
                "version": "0.6.1",
            },
            {"owner": "core", "msi": self.msi.name, "version": "0.6.1"},
        ]
        with patch("sima_cli.install.windows_package._is_windows", return_value=True), patch(
            "sima_cli.install.windows_package._run",
            side_effect=[completed(), completed(returncode=1603)],
        ):
            with self.assertRaisesRegex(WindowsNativeInstallError, "scheduler"):
                install_windows_package(self.metadata, str(self.root))

    def test_matching_python_package_is_verified_without_reinstall(self):
        responses = [completed(), completed(stdout="0.6.1\n"), completed()]
        with patch("sima_cli.install.windows_package._is_windows", return_value=True), patch(
            "sima_cli.install.windows_package._run", side_effect=responses
        ) as run:
            result = install_windows_package(
                self.metadata, str(self.root), python_executable=str(self.python)
            )

        self.assertEqual(result["python"], "already-installed")
        self.assertEqual(run.call_count, 3)
        self.assertNotIn("pip", [part for call in run.call_args_list for part in call.args[0]])

    def test_matching_broken_python_package_is_reinstalled_and_verified(self):
        responses = [
            completed(),
            completed(stdout="0.6.1\n"),
            completed(returncode=1, stderr="import failed"),
            completed(),
            completed(stdout="0.6.1\n"),
            completed(),
        ]
        with patch("sima_cli.install.windows_package._is_windows", return_value=True), patch(
            "sima_cli.install.windows_package._run", side_effect=responses
        ) as run:
            result = install_windows_package(
                self.metadata, str(self.root), python_executable=str(self.python)
            )

        self.assertEqual(result["python"], "installed")
        self.assertEqual(
            run.call_args_list[3].args[0],
            [
                str(self.python),
                "-m",
                "pip",
                "install",
                "--upgrade",
                "--force-reinstall",
                str(self.wheel),
            ],
        )

    def test_missing_python_package_is_installed_and_verified(self):
        responses = [
            completed(),
            completed(returncode=3),
            completed(),
            completed(stdout="0.6.1\n"),
            completed(),
        ]
        with patch("sima_cli.install.windows_package._is_windows", return_value=True), patch(
            "sima_cli.install.windows_package._run", side_effect=responses
        ) as run:
            result = install_windows_package(
                self.metadata, str(self.root), python_executable=str(self.python)
            )

        self.assertEqual(result["python"], "installed")
        pip_command = run.call_args_list[2].args[0]
        self.assertEqual(
            pip_command,
            [str(self.python), "-m", "pip", "install", "--upgrade", str(self.wheel)],
        )

    def test_different_python_version_is_replaced_with_requested_version(self):
        responses = [
            completed(),
            completed(stdout="0.5.0\n"),
            completed(),
            completed(stdout="0.6.1\n"),
            completed(),
        ]
        with patch("sima_cli.install.windows_package._is_windows", return_value=True), patch(
            "sima_cli.install.windows_package._run", side_effect=responses
        ) as run:
            result = install_windows_package(
                self.metadata, str(self.root), python_executable=str(self.python)
            )

        self.assertEqual(result["python"], "installed")
        self.assertEqual(run.call_args_list[2].args[0][-2:], ["--upgrade", str(self.wheel)])

    def test_pip_failure_reports_partial_installation(self):
        responses = [completed(), completed(returncode=3), completed(returncode=1)]
        with patch("sima_cli.install.windows_package._is_windows", return_value=True), patch(
            "sima_cli.install.windows_package._run", side_effect=responses
        ):
            with self.assertRaisesRegex(WindowsPythonInstallError, "native installation was kept"):
                install_windows_package(
                    self.metadata, str(self.root), python_executable=str(self.python)
                )

    def test_restart_result_stops_before_python(self):
        with patch("sima_cli.install.windows_package._is_windows", return_value=True), patch(
            "sima_cli.install.windows_package._run", return_value=completed(returncode=3010)
        ) as run:
            with self.assertRaises(WindowsPackageRestartRequired):
                install_windows_package(
                    self.metadata, str(self.root), python_executable=str(self.python)
                )
        self.assertEqual(run.call_count, 1)

    def test_cli_exposes_customer_python_environment_option(self):
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["neat", "install", "--help"],
            env={"SIMA_CLI_CHECK_FOR_UPDATE": "0"},
        )
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("--python-executable", result.output)
        self.assertIn("customer", result.output)
        self.assertIn("Python environment", result.output)

    def test_vulcan_install_forwards_selected_python_only_when_requested(self):
        resolved = type(
            "Result",
            (),
            {
                "environment": "dev",
                "base_url": "https://example.invalid",
                "repository": "core",
                "package_path": "pciehost/windows/amd64",
                "ref": "3.0",
                "ref_key": "3.0",
                "requested_spec": "latest",
                "resolved_spec": "a" * 40,
                "metadata_url": "https://example.invalid/metadata.json",
            },
        )()
        with patch(
            "sima_cli.vulcan.commands.resolve_install_metadata_url", return_value=resolved
        ), patch("sima_cli.vulcan.commands.install_from_metadata") as install:
            install_vulcan_package(
                target="core/pciehost/windows/amd64@3.0",
                environment="dev",
                python_executable=str(self.python),
            )

        self.assertEqual(install.call_args.kwargs["python_executable"], str(self.python))


if __name__ == "__main__":
    unittest.main()
