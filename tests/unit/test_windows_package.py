import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import click
from click.testing import CliRunner

from sima_cli.cli import main
from sima_cli.install.metadata_validator import MetadataValidationError, validate_metadata
from sima_cli.install.windows_package import (
    WindowsNativeInstallError,
    WindowsPackageRestartRequired,
    WindowsPrerequisiteError,
    WindowsPythonInstallError,
    _managed_python_executable,
    _windows_pnp_driver_version,
    install_windows_package,
)
from sima_cli.vulcan.commands import install_vulcan_package


def completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr=stderr)


def python_facts(version="3.14", arch="AMD64"):
    return completed(stdout=f'{{"version": "{version}", "arch": "{arch}"}}\n')


class WindowsPackageTests(unittest.TestCase):
    def setUp(self):
        self.administrator = patch(
            "sima_cli.install.windows_package._is_windows_administrator",
            return_value=True,
        )
        self.administrator.start()
        self.addCleanup(self.administrator.stop)
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
            "resources-checksum": {
                self.msi.name: "a" * 64,
                self.wheel.name: "b" * 64,
            },
            "installation": {
                "type": "windows-msi",
                "msi": self.msi.name,
                "python": {
                    "wheel": self.wheel.name,
                    "distribution": "pyneatpcie",
                    "version": "0.6.1",
                    "import": "pyneatpcie",
                    "requires-python": "3.14",
                    "arch": "amd64",
                },
            },
        }

    def tearDown(self):
        self.temp.cleanup()

    def test_metadata_accepts_declarative_msi_and_wheel(self):
        self.assertTrue(validate_metadata(self.metadata))

    def test_driver_version_query_passes_device_name_through_environment(self):
        with patch(
            "sima_cli.install.windows_package._run",
            return_value=completed(stdout="3.0.0.2"),
        ) as run:
            self.assertEqual(
                _windows_pnp_driver_version("SiMa.ai MLA Device"), "3.0.0.2")

        self.assertEqual(
            run.call_args.kwargs["env"]["SIMA_CLI_PREREQ_DEVICE"],
            "SiMa.ai MLA Device",
        )
        self.assertNotIn("SiMa.ai MLA Device", run.call_args.args[0])

    def test_metadata_rejects_msi_not_in_resources(self):
        self.metadata["resources"].remove(self.msi.name)
        self.metadata["resources-checksum"].pop(self.msi.name)
        with self.assertRaisesRegex(MetadataValidationError, "installation.msi"):
            validate_metadata(self.metadata)

    def test_metadata_requires_complete_checksums_for_windows_install(self):
        self.metadata["resources-checksum"].pop(self.wheel.name)
        with self.assertRaisesRegex(MetadataValidationError, "exactly match"):
            validate_metadata(self.metadata)

    def test_metadata_rejects_unsafe_installed_path(self):
        self.metadata["installation"]["verification"] = {
            "command": {"environment": "ProgramFiles", "relative": "..\\doctor.cmd"}
        }
        with self.assertRaisesRegex(MetadataValidationError, "safe relative Windows path"):
            validate_metadata(self.metadata)

    def test_native_only_invokes_msiexec_and_does_not_invoke_pip(self):
        with patch("sima_cli.install.windows_package._is_windows", return_value=True), patch(
            "sima_cli.install.windows_package._run", return_value=completed()
        ) as run:
            result = install_windows_package(
                self.metadata, str(self.root), native_only=True
            )

        self.assertEqual(result["python"], "not-requested")
        self.assertEqual(run.call_count, 1)
        command = run.call_args.args[0]
        self.assertEqual(command[:3], ["msiexec.exe", "/i", str(self.msi)])
        self.assertIn("/norestart", command)
        self.assertIn("/L*v", command)

    def test_standard_user_elevates_only_the_native_installer(self):
        with patch(
            "sima_cli.install.windows_package._is_windows", return_value=True
        ), patch(
            "sima_cli.install.windows_package._is_windows_administrator",
            return_value=False,
        ), patch(
            "sima_cli.install.windows_package.sys.stdin.isatty", return_value=True
        ), patch(
            "sima_cli.install.windows_package._run", return_value=completed()
        ) as run:
            result = install_windows_package(
                self.metadata, str(self.root), native_only=True
            )

        self.assertEqual(result["native"], "installed")
        command = run.call_args.args[0]
        self.assertEqual(command[:4], [
            "powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive"
        ])
        self.assertNotIn(str(self.msi), command)
        arguments = run.call_args.kwargs["env"]["SIMA_CLI_MSI_ARGUMENTS"]
        self.assertIn(str(self.msi), arguments)
        self.assertIn("/passive", arguments)

    def test_noninteractive_standard_user_fails_before_starting_installer(self):
        with patch(
            "sima_cli.install.windows_package._is_windows", return_value=True
        ), patch(
            "sima_cli.install.windows_package._is_windows_administrator",
            return_value=False,
        ), patch(
            "sima_cli.install.windows_package.sys.stdin.isatty", return_value=False
        ), patch("sima_cli.install.windows_package._run") as run:
            with self.assertRaisesRegex(
                WindowsNativeInstallError, "interactive PowerShell"
            ):
                install_windows_package(
                    self.metadata, str(self.root), native_only=True
                )

        run.assert_not_called()

    def test_cancelled_standard_user_elevation_stops_the_transaction(self):
        with patch(
            "sima_cli.install.windows_package._is_windows", return_value=True
        ), patch(
            "sima_cli.install.windows_package._is_windows_administrator",
            return_value=False,
        ), patch(
            "sima_cli.install.windows_package.sys.stdin.isatty", return_value=True
        ), patch(
            "sima_cli.install.windows_package._run", return_value=completed(returncode=1223)
        ):
            with self.assertRaisesRegex(
                WindowsNativeInstallError, "administrator approval was cancelled"
            ):
                install_windows_package(
                    self.metadata, str(self.root), native_only=True
                )

    def test_coordinated_native_packages_install_in_manifest_order(self):
        self.metadata["resources"].insert(0, self.scheduler_msi.name)
        self.metadata["resources-checksum"][self.scheduler_msi.name] = "c" * 64
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
            result = install_windows_package(
                self.metadata, str(self.root), native_only=True
            )

        self.assertEqual(
            [package["owner"] for package in result["native_packages"]],
            ["scheduler", "core"],
        )
        self.assertEqual(run.call_args_list[0].args[0][2], str(self.scheduler_msi))
        self.assertEqual(run.call_args_list[1].args[0][2], str(self.msi))

    def test_coordinated_native_failure_reports_completed_packages(self):
        self.metadata["resources"].insert(0, self.scheduler_msi.name)
        self.metadata["resources-checksum"][self.scheduler_msi.name] = "c" * 64
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
                install_windows_package(
                    self.metadata, str(self.root), native_only=True
                )

    def test_policy_rejected_msi_reports_policy_without_bypass_guidance(self):
        with patch("sima_cli.install.windows_package._is_windows", return_value=True), patch(
            "sima_cli.install.windows_package._run", return_value=completed(returncode=1625)
        ):
            with self.assertRaises(WindowsNativeInstallError) as raised:
                install_windows_package(
                    self.metadata, str(self.root), native_only=True
                )

        message = str(raised.exception)
        self.assertIn("system policy", message)
        self.assertIn("approved package", message)
        self.assertNotIn("disable", message.lower())

    def test_missing_selected_python_blocks_before_msi(self):
        self.python.unlink()
        with patch("sima_cli.install.windows_package._is_windows", return_value=True), patch(
            "sima_cli.install.windows_package._run"
        ) as run:
            with self.assertRaisesRegex(WindowsPrerequisiteError, "no MSI was started"):
                install_windows_package(
                    self.metadata, str(self.root), python_executable=str(self.python)
                )
        run.assert_not_called()

    def test_wrong_python_version_blocks_before_msi(self):
        with patch("sima_cli.install.windows_package._is_windows", return_value=True), patch(
            "sima_cli.install.windows_package._run", return_value=python_facts(version="3.13")
        ) as run:
            with self.assertRaisesRegex(WindowsPrerequisiteError, "requires Python 3.14"):
                install_windows_package(
                    self.metadata, str(self.root), python_executable=str(self.python)
                )
        self.assertEqual(run.call_count, 1)

    def test_selected_python_launch_failure_blocks_before_msi(self):
        with patch("sima_cli.install.windows_package._is_windows", return_value=True), patch(
            "sima_cli.install.windows_package._run", side_effect=OSError("access denied")
        ) as run:
            with self.assertRaisesRegex(WindowsPrerequisiteError, "no MSI was started"):
                install_windows_package(
                    self.metadata, str(self.root), python_executable=str(self.python)
                )
        self.assertEqual(run.call_count, 1)

    def test_wrong_python_architecture_blocks_before_msi(self):
        with patch("sima_cli.install.windows_package._is_windows", return_value=True), patch(
            "sima_cli.install.windows_package._run", return_value=python_facts(arch="ARM64")
        ) as run:
            with self.assertRaisesRegex(WindowsPrerequisiteError, "requires amd64"):
                install_windows_package(
                    self.metadata, str(self.root), python_executable=str(self.python)
                )
        self.assertEqual(run.call_count, 1)

    def test_missing_declared_prerequisite_blocks_before_msi(self):
        self.metadata["installation"]["prerequisites"] = [
            {
                "name": "Windows OpenSSH client",
                "type": "executable",
                "value": "ssh.exe",
                "guidance": "Install Windows OpenSSH Client.",
            }
        ]
        self.assertTrue(validate_metadata(self.metadata))
        with patch("sima_cli.install.windows_package._is_windows", return_value=True), patch(
            "sima_cli.install.windows_package.shutil.which", return_value=None
        ), patch("sima_cli.install.windows_package._run") as run:
            with self.assertRaisesRegex(WindowsPrerequisiteError, "OpenSSH"):
                install_windows_package(self.metadata, str(self.root))
        run.assert_not_called()

    def test_file_and_service_prerequisites_are_checked_before_msi(self):
        runtime = self.root / "gstreamer" / "bin" / "gstreamer-1.0-0.dll"
        runtime.parent.mkdir(parents=True)
        runtime.write_bytes(b"fixture")
        self.metadata["installation"]["prerequisites"] = [
            {
                "name": "GStreamer",
                "type": "file",
                "candidates": [
                    {"environment": "TEST_GSTREAMER", "relative": "bin\\gstreamer-1.0-0.dll"}
                ],
                "guidance": "Install GStreamer.",
            },
            {
                "name": "Platform service",
                "type": "windows-service",
                "value": "SiMaMlaDaemon",
                "guidance": "Install the Platform package.",
            },
        ]
        self.assertTrue(validate_metadata(self.metadata))
        with patch.dict(os.environ, {"TEST_GSTREAMER": str(runtime.parent.parent)}), patch(
            "sima_cli.install.windows_package._is_windows", return_value=True
        ), patch(
            "sima_cli.install.windows_package._check_windows_service", return_value=True
        ), patch(
            "sima_cli.install.windows_package._run", return_value=completed()
        ) as run:
            result = install_windows_package(
                self.metadata, str(self.root), native_only=True
            )
        self.assertEqual(result["native"], "installed")
        self.assertEqual(run.call_count, 1)

    def test_exact_platform_and_gstreamer_versions_are_checked_before_msi(self):
        gst_inspect = self.root / "gstreamer" / "bin" / "gst-inspect-1.0.exe"
        gst_inspect.parent.mkdir(parents=True)
        gst_inspect.write_bytes(b"fixture")
        self.metadata["installation"]["prerequisites"] = [
            {
                "name": "SiMa Platform MLA driver",
                "type": "windows-pnp-driver",
                "value": "SiMa.ai MLA Device",
                "version": "3.0.0.2",
                "guidance": "Install the supported Platform package.",
            },
            {
                "name": "GStreamer 1.28.6",
                "type": "command-version",
                "candidates": [{
                    "environment": "TEST_GSTREAMER",
                    "relative": "bin\\gst-inspect-1.0.exe",
                }],
                "arguments": ["--version"],
                "version": "1.28.6",
                "version-pattern": (
                    r"(?m)^gst-inspect-1\.0 version ([0-9]+(?:\.[0-9]+){2})$"
                ),
                "guidance": "Install the supported GStreamer package.",
            },
        ]
        self.assertTrue(validate_metadata(self.metadata))
        with patch.dict(os.environ, {"TEST_GSTREAMER": str(gst_inspect.parent.parent)}), patch(
            "sima_cli.install.windows_package._is_windows", return_value=True
        ), patch(
            "sima_cli.install.windows_package._windows_pnp_driver_version",
            return_value="3.0.0.2",
        ), patch(
            "sima_cli.install.windows_package._run",
            side_effect=[
                completed(stdout="gst-inspect-1.0 version 1.28.6\n"),
                completed(),
            ],
        ) as run:
            result = install_windows_package(
                self.metadata, str(self.root), native_only=True
            )
        self.assertEqual(result["native"], "installed")
        self.assertEqual(run.call_count, 2)

    def test_wrong_platform_version_blocks_before_msi(self):
        self.metadata["installation"]["prerequisites"] = [{
            "name": "SiMa Platform MLA driver",
            "type": "windows-pnp-driver",
            "value": "SiMa.ai MLA Device",
            "version": "3.0.0.2",
            "guidance": "Install the supported Platform package.",
        }]
        with patch("sima_cli.install.windows_package._is_windows", return_value=True), patch(
            "sima_cli.install.windows_package._windows_pnp_driver_version",
            return_value="2.1.3.0",
        ), patch("sima_cli.install.windows_package._run") as run:
            with self.assertRaisesRegex(
                WindowsPrerequisiteError, "found 2.1.3.0, expected 3.0.0.2"
            ):
                install_windows_package(self.metadata, str(self.root))
        run.assert_not_called()

    def test_wrong_gstreamer_version_blocks_before_msi(self):
        gst_inspect = self.root / "gstreamer" / "bin" / "gst-inspect-1.0.exe"
        gst_inspect.parent.mkdir(parents=True)
        gst_inspect.write_bytes(b"fixture")
        self.metadata["installation"]["prerequisites"] = [{
            "name": "GStreamer 1.28.6",
            "type": "command-version",
            "candidates": [{
                "environment": "TEST_GSTREAMER",
                "relative": "bin\\gst-inspect-1.0.exe",
            }],
            "arguments": ["--version"],
            "version": "1.28.6",
            "version-pattern": r"version ([0-9]+(?:\.[0-9]+){2})",
            "guidance": "Install the supported GStreamer package.",
        }]
        with patch.dict(os.environ, {"TEST_GSTREAMER": str(gst_inspect.parent.parent)}), patch(
            "sima_cli.install.windows_package._is_windows", return_value=True
        ), patch(
            "sima_cli.install.windows_package._run",
            return_value=completed(stdout="gst-inspect-1.0 version 1.26.0\n"),
        ) as run:
            with self.assertRaisesRegex(
                WindowsPrerequisiteError, "found 1.26.0, expected 1.28.6"
            ):
                install_windows_package(self.metadata, str(self.root))
        self.assertEqual(run.call_count, 1)

    def test_declared_native_verification_runs_after_msi(self):
        install_root = self.root / "installed"
        doctor = install_root / "bin" / "sima-pcie-host-doctor.cmd"
        doctor.parent.mkdir(parents=True)
        doctor.write_text("@exit /b 0\n", encoding="utf-8")
        self.metadata["installation"]["verification"] = {
            "command": {
                "environment": "TEST_PROGRAM_FILES",
                "relative": "installed\\bin\\sima-pcie-host-doctor.cmd",
            }
        }
        self.assertTrue(validate_metadata(self.metadata))
        with patch.dict(os.environ, {
            "TEST_PROGRAM_FILES": str(self.root), "COMSPEC": "cmd.exe"
        }), patch(
            "sima_cli.install.windows_package._is_windows", return_value=True
        ), patch(
            "sima_cli.install.windows_package._run",
            side_effect=[completed(), completed(stdout="doctor pass\n")],
        ) as run:
            result = install_windows_package(
                self.metadata, str(self.root), native_only=True
            )
        self.assertEqual(result["verification"]["status"], "passed")
        self.assertEqual(run.call_args_list[1].args[0][:3], ["cmd.exe", "/d", "/c"])

    def test_native_verification_failure_reports_retained_install(self):
        doctor = self.root / "doctor.cmd"
        doctor.write_text("@exit /b 12\n", encoding="utf-8")
        self.metadata["installation"]["verification"] = {
            "command": {"environment": "TEST_ROOT", "relative": "doctor.cmd"}
        }
        with patch.dict(os.environ, {"TEST_ROOT": str(self.root)}), patch(
            "sima_cli.install.windows_package._is_windows", return_value=True
        ), patch(
            "sima_cli.install.windows_package._run",
            side_effect=[completed(), completed(returncode=12, stderr="plugin failed")],
        ):
            with self.assertRaisesRegex(WindowsNativeInstallError, "installation was kept"):
                install_windows_package(
                    self.metadata, str(self.root), native_only=True
                )

    def test_native_verification_launch_failure_reports_retained_install(self):
        doctor = self.root / "doctor.cmd"
        doctor.write_text("@exit /b 0\n", encoding="utf-8")
        self.metadata["installation"]["verification"] = {
            "command": {"environment": "TEST_ROOT", "relative": "doctor.cmd"}
        }
        with patch.dict(os.environ, {"TEST_ROOT": str(self.root)}), patch(
            "sima_cli.install.windows_package._is_windows", return_value=True
        ), patch(
            "sima_cli.install.windows_package._run",
            side_effect=[completed(), OSError("blocked")],
        ):
            with self.assertRaisesRegex(WindowsNativeInstallError, "installation was kept"):
                install_windows_package(
                    self.metadata, str(self.root), native_only=True
                )

    def test_installed_customer_skill_is_synced_after_native_verification(self):
        skill = self.root / "installed" / "skill"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("# Customer skill\n", encoding="utf-8")
        self.metadata["installation"]["skills"] = [
            {"environment": "TEST_PROGRAM_FILES", "relative": "installed\\skill"}
        ]
        self.assertTrue(validate_metadata(self.metadata))
        with patch.dict(os.environ, {"TEST_PROGRAM_FILES": str(self.root)}), patch(
            "sima_cli.install.windows_package._is_windows", return_value=True
        ), patch(
            "sima_cli.install.windows_package._run", return_value=completed()
        ), patch(
            "sima_cli.install.windows_package.SkillManager"
        ) as manager_type:
            manager_type.return_value.install.return_value = [
                "neat-pcie-application-builder"
            ]
            result = install_windows_package(
                self.metadata, str(self.root), native_only=True
            )

        manager_type.return_value.install.assert_called_once_with(str(skill), force=True)
        self.assertEqual(result["skills"], ["neat-pcie-application-builder"])

    def test_missing_installed_customer_skill_reports_retained_native_install(self):
        self.metadata["installation"]["skills"] = [
            {"environment": "TEST_PROGRAM_FILES", "relative": "missing\\skill"}
        ]
        with patch.dict(os.environ, {"TEST_PROGRAM_FILES": str(self.root)}), patch(
            "sima_cli.install.windows_package._is_windows", return_value=True
        ), patch("sima_cli.install.windows_package._run", return_value=completed()):
            with self.assertRaisesRegex(WindowsNativeInstallError, "customer skill is missing"):
                install_windows_package(
                    self.metadata, str(self.root), native_only=True
                )

    def test_customer_skill_registry_failure_reports_retained_native_install(self):
        skill = self.root / "installed" / "skill"
        skill.mkdir(parents=True)
        self.metadata["installation"]["skills"] = [
            {"environment": "TEST_PROGRAM_FILES", "relative": "installed\\skill"}
        ]
        with patch.dict(os.environ, {"TEST_PROGRAM_FILES": str(self.root)}), patch(
            "sima_cli.install.windows_package._is_windows", return_value=True
        ), patch(
            "sima_cli.install.windows_package._run", return_value=completed()
        ), patch(
            "sima_cli.install.windows_package.SkillManager",
            side_effect=OSError("registry unavailable"),
        ):
            with self.assertRaisesRegex(WindowsNativeInstallError, "registry could not be opened"):
                install_windows_package(
                    self.metadata, str(self.root), native_only=True
                )

    def test_msiexec_permission_denial_is_reported_with_log_location(self):
        with patch("sima_cli.install.windows_package._is_windows", return_value=True), patch(
            "sima_cli.install.windows_package._run",
            side_effect=PermissionError("access denied"),
        ):
            with self.assertRaises(WindowsNativeInstallError) as raised:
                install_windows_package(
                    self.metadata, str(self.root), native_only=True
                )

        message = str(raised.exception)
        self.assertIn("Could not start Windows Installer", message)
        self.assertIn("permissions", message)
        self.assertIn("sima-cli-msi-install-01-native.log", message)
        self.assertIn("do not disable security controls", message)

    def test_default_install_uses_managed_python_environment(self):
        responses = [
            completed(),
            completed(returncode=3),
            completed(),
            completed(stdout="0.6.1\n"),
            completed(),
        ]
        with patch("sima_cli.install.windows_package._is_windows", return_value=True), patch(
            "sima_cli.install.windows_package._managed_python_executable",
            return_value=str(self.python),
        ) as managed, patch(
            "sima_cli.install.windows_package._run", side_effect=responses
        ) as run:
            result = install_windows_package(self.metadata, str(self.root))

        managed.assert_called_once_with(self.metadata)
        self.assertEqual(result["python"], "installed")
        self.assertEqual(result["python_executable"], str(self.python))
        self.assertEqual(
            run.call_args_list[2].args[0],
            [str(self.python), "-m", "pip", "install", "--upgrade", str(self.wheel)],
        )

    def test_managed_python_environment_is_created_with_compatible_bootstrap(self):
        home = self.root / "home"
        environment = home / "pyneatpcie"
        managed_python = environment / "Scripts" / "python.exe"

        def invoke(command, **_kwargs):
            if command[-3:-1] == ["-m", "venv"]:
                managed_python.parent.mkdir(parents=True)
                managed_python.write_bytes(b"managed python fixture")
                return completed()
            return python_facts()

        with patch("sima_cli.install.windows_package.Path.home", return_value=home), patch(
            "sima_cli.install.windows_package._python_bootstrap_candidates",
            return_value=[[str(self.python)]],
        ), patch("sima_cli.install.windows_package._run", side_effect=invoke) as run:
            result = _managed_python_executable(self.metadata)

        self.assertEqual(result, str(managed_python))
        self.assertEqual(
            run.call_args_list[1].args[0],
            [str(self.python), "-m", "venv", str(environment)],
        )

    def test_existing_managed_python_environment_is_reused(self):
        home = self.root / "home"
        managed_python = home / "pyneatpcie" / "Scripts" / "python.exe"
        managed_python.parent.mkdir(parents=True)
        managed_python.write_bytes(b"managed python fixture")

        with patch("sima_cli.install.windows_package.Path.home", return_value=home), patch(
            "sima_cli.install.windows_package._run", return_value=python_facts()
        ) as run:
            result = _managed_python_executable(self.metadata)

        self.assertEqual(result, str(managed_python))
        self.assertEqual(run.call_count, 1)

    def test_default_install_blocks_before_msi_without_compatible_python(self):
        with patch("sima_cli.install.windows_package._is_windows", return_value=True), patch(
            "sima_cli.install.windows_package._python_bootstrap_candidates", return_value=[]
        ), patch("sima_cli.install.windows_package._run") as run:
            with self.assertRaisesRegex(WindowsPrerequisiteError, "compatible Python 3.14"):
                install_windows_package(self.metadata, str(self.root))

        run.assert_not_called()

    def test_native_only_rejects_python_override(self):
        with patch("sima_cli.install.windows_package._is_windows", return_value=True), patch(
            "sima_cli.install.windows_package._run"
        ) as run:
            with self.assertRaisesRegex(
                click.ClickException,
                "--native-only cannot be combined with --python-executable",
            ):
                install_windows_package(
                    self.metadata,
                    str(self.root),
                    python_executable=str(self.python),
                    native_only=True,
                )

        run.assert_not_called()

    def test_matching_python_package_is_verified_without_reinstall(self):
        responses = [python_facts(), completed(), completed(stdout="0.6.1\n"), completed()]
        with patch("sima_cli.install.windows_package._is_windows", return_value=True), patch(
            "sima_cli.install.windows_package._run", side_effect=responses
        ) as run:
            result = install_windows_package(
                self.metadata, str(self.root), python_executable=str(self.python)
            )

        self.assertEqual(result["python"], "already-installed")
        self.assertEqual(run.call_count, 4)
        self.assertNotIn("pip", [part for call in run.call_args_list for part in call.args[0]])

    def test_matching_broken_python_package_is_reinstalled_and_verified(self):
        responses = [
            python_facts(),
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
            run.call_args_list[4].args[0],
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
            python_facts(),
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
        pip_command = run.call_args_list[3].args[0]
        self.assertEqual(
            pip_command,
            [str(self.python), "-m", "pip", "install", "--upgrade", str(self.wheel)],
        )

    def test_different_python_version_is_replaced_with_requested_version(self):
        responses = [
            python_facts(),
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
        self.assertEqual(run.call_args_list[3].args[0][-2:], ["--upgrade", str(self.wheel)])

    def test_pip_failure_reports_partial_installation(self):
        responses = [
            python_facts(), completed(), completed(returncode=3), completed(returncode=1)
        ]
        with patch("sima_cli.install.windows_package._is_windows", return_value=True), patch(
            "sima_cli.install.windows_package._run", side_effect=responses
        ):
            with self.assertRaisesRegex(WindowsPythonInstallError, "native installation was kept"):
                install_windows_package(
                    self.metadata, str(self.root), python_executable=str(self.python)
                )

    def test_restart_result_stops_before_python(self):
        with patch("sima_cli.install.windows_package._is_windows", return_value=True), patch(
            "sima_cli.install.windows_package._run",
            side_effect=[python_facts(), completed(returncode=3010)],
        ) as run:
            with self.assertRaises(WindowsPackageRestartRequired):
                install_windows_package(
                    self.metadata, str(self.root), python_executable=str(self.python)
                )
        self.assertEqual(run.call_count, 2)

    def test_cli_exposes_managed_python_and_native_only_options(self):
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["neat", "install", "--help"],
            env={"SIMA_CLI_CHECK_FOR_UPDATE": "0"},
        )
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("--python-executable", result.output)
        self.assertIn("managed user environment", result.output)
        self.assertIn("--native-only", result.output)

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

    def test_vulcan_install_forwards_native_only(self):
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
                native_only=True,
            )

        self.assertTrue(install.call_args.kwargs["native_only"])


if __name__ == "__main__":
    unittest.main()
