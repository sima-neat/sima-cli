import base64
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import ANY, MagicMock, call, patch

import click
from click.testing import CliRunner

from sima_cli.cli import main
from sima_cli.install import metadata_installer
from sima_cli.install.metadata_validator import MetadataValidationError, validate_metadata
from sima_cli.install.windows_package import (
    ELEVATED_INSTALLER_EXIT_CODE_MISSING,
    ELEVATED_INSTALLER_HASH_MISMATCH,
    ELEVATED_INSTALLER_LAUNCH_FAILED,
    WindowsNativeInstallError,
    WindowsPackageRestartRequired,
    WindowsPrerequisiteError,
    WindowsPythonInstallError,
    WindowsSetupError,
    _WindowsPnpDriverVersionError,
    _bootstrap_python,
    _check_windows_service,
    _managed_python_executable,
    _msi_product_code,
    _python_bootstrap_candidates,
    _windows_pnp_driver_version,
    install_windows_package,
    uninstall_windows_package,
)
from sima_cli.install.registry import PackageRegistry, packages as packages_group
from sima_cli.playbooks.manager import SkillError
from sima_cli.vulcan.commands import install_vulcan_package


# Off Windows the system directory falls back to the default installation path.
MSIEXEC = "C:\\Windows\\System32\\msiexec.exe"
POWERSHELL = "C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe"
PRODUCT_CODES = {
    "simaai": "{11111111-1111-1111-1111-111111111111}",
    "sima": "{22222222-2222-2222-2222-222222222222}",
}
OPENSSH = {
    "name": "Windows OpenSSH client",
    "type": "executable",
    "value": "ssh.exe",
    "guidance": "Install Windows OpenSSH Client.",
}
PLATFORM_DRIVER = {
    "name": "SiMa Platform MLA driver",
    "type": "windows-pnp-driver",
    "value": "SiMa.ai MLA Device",
    "version": "3.0.0.2",
    "guidance": "Install the supported Platform package.",
}
GSTREAMER = {
    "name": "GStreamer 1.28.6 MSVC x64 runtime",
    "type": "command-version",
    "candidates": [{"environment": "TEST_GSTREAMER", "relative": "bin\\gst-inspect-1.0.exe"}],
    "arguments": ["--version"],
    "version": "1.28.6",
    "version-pattern": r"(?m)^gst-inspect-1\.0 version ([0-9]+(?:\.[0-9]+){2})$",
    "guidance": "Install GStreamer 1.28.6 MSVC x64.",
}


def completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr=stderr)


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def direct_url(wheel_sha256):
    return completed(stdout=json.dumps({"archive_info": {"hashes": {"sha256": wheel_sha256}}}))


def python_facts(version="3.14", arch="AMD64", tag="cp314-cp314-win_amd64", wheel_supported=True):
    return completed(stdout=json.dumps({
        "version": version, "arch": arch, "tag": tag, "wheel_supported": wheel_supported,
    }) + "\n")


def elevated_request(environment):
    """Decode the request the CLI embeds in the elevated PowerShell command line."""
    encoded = environment["SIMA_CLI_ELEVATED_ARGUMENTS"].split()[-1]
    script = base64.b64decode(encoded).decode("utf-16-le")
    request = re.search(r"FromBase64String\('([^']+)'\)", script).group(1)
    return json.loads(base64.b64decode(request)), script


def fake_elevation(exit_codes, wrapper_code=0, errors=None):
    """Stand in for the one elevated process: report each msiexec result in order.

    Like the elevated script, it stops after the first exit code outside the
    request's continue_codes and writes the results to the CLI's result file.
    """
    requests = []

    def run(command, **kwargs):
        request, script = elevated_request(kwargs["env"])
        requests.append({"request": request, "script": script, "command": command})
        result_path = Path(request["result"])
        assert result_path.is_file() and result_path.read_bytes() == b""
        results = []
        for index, (package, code) in enumerate(zip(request["packages"], exit_codes)):
            results.append({
                "owner": package["owner"],
                "exit_code": code,
                "error": (errors or {}).get(index, ""),
            })
            if code not in request["continue_codes"]:
                break
        if wrapper_code == 0:
            result_path.write_text(json.dumps({"packages": results}), encoding="utf-8")
        return completed(returncode=wrapper_code)

    return run, requests


PINNED_RETRY = (
    'sima-cli neat install core/pciehost/windows/amd64@v0.6.1:0123456789ab '
    '--python-executable "C:\\Program Files\\Python314\\python.exe"'
)


class WindowsPackageTests(unittest.TestCase):
    def setUp(self):
        self.administrator = patch(
            "sima_cli.install.windows_package._is_windows_administrator",
            return_value=True,
        )
        self.administrator.start()
        self.addCleanup(self.administrator.stop)
        windows = patch("sima_cli.install.windows_package._is_windows", return_value=True)
        windows.start()
        self.addCleanup(windows.stop)
        product_codes = patch(
            "sima_cli.install.windows_package._msi_product_code",
            side_effect=lambda msi: PRODUCT_CODES[msi.name.split("-")[0]],
        )
        product_codes.start()
        self.addCleanup(product_codes.stop)
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
                self.msi.name: sha256(self.msi),
                self.wheel.name: sha256(self.wheel),
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

    def use_scheduler_and_core(self):
        if "native-packages" in self.metadata["installation"]:
            return
        self.metadata["resources"].insert(0, self.scheduler_msi.name)
        self.metadata["resources-checksum"][self.scheduler_msi.name] = sha256(self.scheduler_msi)
        self.metadata["installation"].pop("msi")
        self.metadata["installation"]["native-packages"] = [
            {"owner": "scheduler", "msi": self.scheduler_msi.name, "version": "0.6.1"},
            {"owner": "core", "msi": self.msi.name, "version": "0.6.1"},
        ]

    def non_elevated_session(self, run):
        """Patch an interactive, non-elevated session whose elevation is served by run."""
        stack = ExitStack()
        stack.enter_context(patch(
            "sima_cli.install.windows_package._is_windows_administrator", return_value=False
        ))
        stack.enter_context(patch(
            "sima_cli.install.windows_package._interactive_console", return_value=True
        ))
        stack.enter_context(patch("sima_cli.install.windows_package._run", side_effect=run))
        return stack

    def installed_gstreamer(self):
        """Create the declared gst-inspect fixture; return the environment that finds it."""
        gst_inspect = self.root / "gstreamer" / "bin" / "gst-inspect-1.0.exe"
        gst_inspect.parent.mkdir(parents=True)
        gst_inspect.write_bytes(b"fixture")
        return {"TEST_GSTREAMER": str(gst_inspect.parent.parent)}

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
        powershell = run.call_args.args[0][-1]
        self.assertIn("foreach ($match in $matches)", powershell)
        self.assertIn("Sort-Object -Unique", powershell)
        self.assertIn("$missingVersionCount -gt 0", powershell)
        self.assertIn("$versions.Count -ne 1", powershell)
        self.assertNotIn("$matches.Count -ne 1", powershell)

    def test_driver_version_query_rejects_missing_mixed_and_versionless_devices(self):
        # The query reports each failure on stderr with exit code 3.
        cases = [
            (
                "No Windows PnP devices named 'SiMa.ai MLA Device' were found.",
                "No Windows PnP devices",
            ),
            (
                "Windows PnP devices named 'SiMa.ai MLA Device' report mixed "
                "DriverVersion values: 3.0.0.1, 3.0.0.2.",
                "mixed DriverVersion values: 3.0.0.1, 3.0.0.2",
            ),
            (
                "1 of 2 Windows PnP devices named 'SiMa.ai MLA Device' "
                "do not report DriverVersion.",
                "1 of 2 Windows PnP devices.*do not report DriverVersion",
            ),
        ]
        for stderr, message in cases:
            with self.subTest(message=message), patch(
                "sima_cli.install.windows_package._run",
                return_value=completed(returncode=3, stderr=stderr),
            ), self.assertRaisesRegex(_WindowsPnpDriverVersionError, message):
                _windows_pnp_driver_version("SiMa.ai MLA Device")

    def test_metadata_rejects_an_unsupported_prerequisite_type(self):
        self.metadata["installation"]["prerequisites"] = [{
            "name": "GStreamer",
            "type": "file",
            "candidates": [{"environment": "TEST_GSTREAMER", "relative": "bin\\gst.dll"}],
            "guidance": "Install GStreamer.",
        }]
        with self.assertRaisesRegex(MetadataValidationError, r"prerequisites\[0\]\.type"):
            validate_metadata(self.metadata)

    def test_metadata_rejects_msi_not_in_resources(self):
        self.metadata["resources"].remove(self.msi.name)
        self.metadata["resources-checksum"].pop(self.msi.name)
        with self.assertRaisesRegex(MetadataValidationError, "installation.msi"):
            validate_metadata(self.metadata)

    def test_metadata_requires_complete_checksums_for_windows_install(self):
        self.metadata["resources-checksum"].pop(self.wheel.name)
        with self.assertRaisesRegex(MetadataValidationError, "exactly match"):
            validate_metadata(self.metadata)

    def test_metadata_rejects_selectable_resources_for_windows_install(self):
        self.metadata["selectable-resources"] = [
            {
                "name": "Optional tutorials",
                "resource": "tutorials.zip",
                "checksum": "c" * 64,
            }
        ]
        with self.assertRaisesRegex(
            MetadataValidationError, "does not support 'selectable-resources'"
        ):
            validate_metadata(self.metadata)

    def test_metadata_rejects_unsafe_installed_path(self):
        self.metadata["installation"]["verification"] = {
            "command": {"environment": "ProgramFiles", "relative": "..\\doctor.cmd"}
        }
        with self.assertRaisesRegex(MetadataValidationError, "safe relative Windows path"):
            validate_metadata(self.metadata)

    def test_metadata_rejects_a_rooted_installed_path_without_a_drive(self):
        # Joined to C:\Program Files, a rooted path keeps only the drive: C:\Users\Public.
        for relative in ("\\Users\\Public\\x.cmd", "/Users/Public/x.cmd"):
            spec = {"environment": "ProgramFiles", "relative": relative}
            fields = {
                "verification": {"command": spec},
                "setup": {"command": spec},
                "skills": [{"id": "neat-pcie-application-builder", **spec}],
                "prerequisites": [{**GSTREAMER, "candidates": [spec]}],
            }
            for field, value in fields.items():
                with self.subTest(field=field, relative=relative):
                    metadata = dict(
                        self.metadata, installation={**self.metadata["installation"], field: value}
                    )
                    with self.assertRaisesRegex(
                        MetadataValidationError, "safe relative Windows path"
                    ):
                        validate_metadata(metadata)

    def full_windows_installation(self):
        """Use every installation field Core's create_windows_metadata.py writes."""
        self.use_scheduler_and_core()
        program_files = {"environment": "ProgramFiles", "relative": "SimaPCIeHost\\bin\\x.cmd"}
        service = {
            "name": "SiMa Platform PCIe service", "type": "windows-service",
            "value": "SiMaMlaDaemon", "guidance": "Start the service.",
        }
        self.metadata["installation"].update({
            "prerequisites": [OPENSSH, PLATFORM_DRIVER, service, GSTREAMER],
            "verification": {"command": dict(program_files)},
            "skills": [{"id": "neat-pcie-application-builder", **program_files}],
            "setup": {"command": dict(program_files)},
            "post-message": "Neat PCIe host installation completed.",
        })
        return self.metadata["installation"]

    def test_metadata_accepts_every_field_the_core_generator_writes(self):
        self.full_windows_installation()
        self.assertTrue(validate_metadata(self.metadata))

    def test_metadata_rejects_unknown_windows_fields(self):
        cases = {
            "installation": lambda i: i,
            "installation.native-packages[1]": lambda i: i["native-packages"][1],
            "installation.python": lambda i: i["python"],
            "installation.verification": lambda i: i["verification"],
            "installation.verification.command": lambda i: i["verification"]["command"],
            "installation.skills[0]": lambda i: i["skills"][0],
            "installation.prerequisites[0]": lambda i: i["prerequisites"][0],
            "installation.prerequisites[1]": lambda i: i["prerequisites"][1],
            "installation.prerequisites[2]": lambda i: i["prerequisites"][2],
            "installation.prerequisites[3]": lambda i: i["prerequisites"][3],
            "installation.prerequisites[3].candidates[0]":
                lambda i: i["prerequisites"][3]["candidates"][0],
        }
        self.full_windows_installation()
        valid = json.dumps(self.metadata)
        for field, select in cases.items():
            for unknown in ("prerequisite", "verfication", "script", "signer"):
                with self.subTest(field=field, unknown=unknown):
                    metadata = json.loads(valid)
                    select(metadata["installation"])[unknown] = "x"
                    with self.assertRaisesRegex(
                        MetadataValidationError,
                        rf"'{re.escape(field)}' has unsupported fields: {unknown}$",
                    ):
                        validate_metadata(metadata)

    def test_metadata_rejects_a_prerequisite_type_that_is_not_a_string(self):
        self.metadata["installation"]["prerequisites"] = [{**OPENSSH, "type": ["executable"]}]
        with self.assertRaisesRegex(MetadataValidationError, r"prerequisites\[0\]\.type"):
            validate_metadata(self.metadata)

    def test_metadata_rejects_a_python_distribution_that_is_a_path(self):
        for distribution in ("..\\..\\x", "C:\\x", "/x", "a/b"):
            with self.subTest(distribution=distribution):
                self.metadata["installation"]["python"]["distribution"] = distribution
                with self.assertRaisesRegex(
                    MetadataValidationError, r"installation\.python\.distribution"
                ):
                    validate_metadata(self.metadata)

    def test_metadata_requires_customer_skill_id(self):
        self.metadata["installation"]["skills"] = [{
            "environment": "ProgramFiles",
            "relative": "SimaPCIeHost\\skills\\neat-pcie-application-builder",
        }]
        with self.assertRaisesRegex(MetadataValidationError, r"skills\[0\].id"):
            validate_metadata(self.metadata)

    def test_metadata_rejects_duplicate_customer_skill_ids(self):
        self.metadata["installation"]["skills"] = [
            {
                "id": "neat-pcie-application-builder",
                "environment": "ProgramFiles",
                "relative": "SimaPCIeHost\\skills\\neat-pcie-application-builder",
            },
            {
                "id": "neat-pcie-application-builder",
                "environment": "ProgramFiles",
                "relative": "SimaPCIeHost\\skills\\duplicate",
            },
        ]
        with self.assertRaisesRegex(MetadataValidationError, "duplicate skill ID"):
            validate_metadata(self.metadata)

    def declare_setup(self):
        """Declare and create the setup command the Core metadata emits."""
        setup = self.root / "SimaPCIeHost" / "bin" / "pcie-setup.cmd"
        setup.parent.mkdir(parents=True)
        setup.write_text("@exit /b 0\n", encoding="utf-8")
        self.metadata["installation"]["setup"] = {
            "command": {"environment": "ProgramFiles", "relative": "SimaPCIeHost\\bin\\pcie-setup.cmd"}
        }
        return setup

    def setup_environment(self, interactive=True, **variables):
        stack = ExitStack()
        stack.enter_context(patch.dict(os.environ, {
            "ProgramFiles": str(self.root), "COMSPEC": "cmd.exe", **variables
        }))
        if "SIMAPCIE_SKIP_SETUP" not in variables:
            stack.enter_context(patch.dict(os.environ))
            os.environ.pop("SIMAPCIE_SKIP_SETUP", None)
        if interactive is not None:
            stack.enter_context(patch(
                "sima_cli.install.windows_package._interactive_console", return_value=interactive
            ))
        return stack

    def windows_stdin(self, is_console, has_window):
        """Patch a Windows stdin that isatty() accepts: the console, or the NUL device."""
        windll = MagicMock()
        windll.kernel32.GetConsoleMode.return_value = 1 if is_console else 0
        windll.kernel32.GetConsoleWindow.return_value = 0x10010 if has_window else 0
        msvcrt = MagicMock()
        msvcrt.get_osfhandle.return_value = 0x1C
        stack = ExitStack()
        stack.enter_context(patch(
            "sima_cli.install.windows_package.ctypes.windll", windll, create=True
        ))
        stack.enter_context(patch.dict(sys.modules, {"msvcrt": msvcrt}))
        stdin = stack.enter_context(patch("sima_cli.install.windows_package.sys.stdin"))
        stdin.isatty.return_value = True
        return stack

    def test_metadata_accepts_only_the_exact_setup_command_shape(self):
        self.declare_setup()
        self.assertTrue(validate_metadata(self.metadata))
        invalid = [
            {"command": {"environment": "ProgramFiles", "relative": "bin\\pcie-setup.cmd"},
             "arguments": ["-Hosts"]},
            {"command": {"environment": "ProgramFiles", "relative": "bin\\pcie-setup.cmd",
                         "arguments": "-Hosts"}},
            {"command": {"environment": "ProgramFiles", "relative": "..\\pcie-setup.cmd"}},
            {"command": {"environment": "ProgramFiles", "relative": "C:\\pcie-setup.cmd"}},
            {"command": "pcie-setup.cmd"},
            "pcie-setup.cmd",
        ]
        for setup in invalid:
            with self.subTest(setup=setup):
                self.metadata["installation"]["setup"] = setup
                with self.assertRaisesRegex(MetadataValidationError, "installation.setup"):
                    validate_metadata(self.metadata)

    def test_setup_runs_last_in_the_console_after_the_wheel(self):
        setup = self.declare_setup()
        responses = [
            python_facts(),
            completed(),
            completed(returncode=3),
            completed(),
            completed(stdout="0.6.1\n"),
            completed(),
            completed(),
        ]
        output = io.StringIO()
        with self.setup_environment(), patch(
            "sima_cli.install.windows_package._run", side_effect=responses
        ) as run, redirect_stdout(output):
            result = install_windows_package(
                self.metadata, str(self.root), python_executable=str(self.python)
            )

        self.assertEqual(result["python"], "installed")
        self.assertEqual(result["setup"], {"status": "passed", "command": str(setup)})
        last = run.call_args_list[-1]
        self.assertEqual(last.args[0], ["cmd.exe", "/d", "/c", str(setup)])
        # Not captured: OpenSSH prompts and setup output reach the user's console.
        self.assertEqual(last.kwargs, {})
        self.assertIn("pip", run.call_args_list[3].args[0])
        self.assertIn(f'Running the setup step:\n  & "{setup}"', output.getvalue())

    def test_native_only_install_still_runs_setup(self):
        setup = self.declare_setup()
        with self.setup_environment(), patch(
            "sima_cli.install.windows_package._run", return_value=completed()
        ) as run, redirect_stdout(io.StringIO()):
            result = install_windows_package(self.metadata, str(self.root), native_only=True)

        self.assertEqual(result["setup"]["status"], "passed")
        self.assertEqual(run.call_args_list[-1].args[0], ["cmd.exe", "/d", "/c", str(setup)])

    def test_setup_is_skipped_with_the_linux_variable_or_without_interactive_input(self):
        setup = self.declare_setup()
        cases = [
            ({"SIMAPCIE_SKIP_SETUP": "1"}, True, "because SIMAPCIE_SKIP_SETUP is set"),
            ({"SIMAPCIE_SKIP_SETUP": "On"}, True, "because SIMAPCIE_SKIP_SETUP is set"),
            ({}, False, "input is not interactive"),
        ]
        for variables, interactive, reason in cases:
            output = io.StringIO()
            with self.subTest(reason=reason, variables=variables), self.setup_environment(
                **variables
            ), patch(
                "sima_cli.install.windows_package._interactive_console", return_value=interactive
            ), patch(
                "sima_cli.install.windows_package._run", return_value=completed()
            ) as run, redirect_stdout(output):
                result = install_windows_package(self.metadata, str(self.root), native_only=True)

                self.assertEqual(result["setup"]["status"], "skipped")
                self.assertEqual(run.call_count, 1)
                self.assertIn(reason, output.getvalue())
                self.assertIn(f'interactive PowerShell window:\n  & "{setup}"', output.getvalue())

    def test_setup_runs_only_from_a_console_a_person_can_answer(self):
        # A caller that passes stdin=subprocess.DEVNULL gives the NUL device, which isatty()
        # accepts on Windows; the OpenSSH password prompt then waited for ever.
        setup = self.declare_setup()
        cases = [
            ("NUL device", False, True, "skipped"),
            ("console without a window", True, False, "skipped"),
            ("console", True, True, "passed"),
        ]
        for name, is_console, has_window, status in cases:
            output = io.StringIO()
            with self.subTest(name), self.setup_environment(interactive=None), \
                    self.windows_stdin(is_console, has_window), patch(
                        "sima_cli.install.windows_package._run", return_value=completed()
                    ) as run, redirect_stdout(output):
                result = install_windows_package(self.metadata, str(self.root), native_only=True)

                self.assertEqual(result["setup"]["status"], status)
                if status == "skipped":
                    self.assertEqual(run.call_count, 1)
                    self.assertIn("input is not interactive", output.getvalue())
                    self.assertIn(
                        f'interactive PowerShell window:\n  & "{setup}"', output.getvalue()
                    )
                else:
                    self.assertEqual(run.call_args.args[0], ["cmd.exe", "/d", "/c", str(setup)])

    def test_setup_failure_keeps_the_install_and_prints_the_command(self):
        setup = self.declare_setup()
        with self.setup_environment(), patch(
            "sima_cli.install.windows_package._run",
            side_effect=[completed(), completed(returncode=1)],
        ):
            with self.assertRaises(WindowsSetupError) as raised:
                install_windows_package(self.metadata, str(self.root), native_only=True)

        message = raised.exception.message
        self.assertIn("setup step failed with exit code 1", message)
        self.assertIn("The installation was kept", message)
        self.assertTrue(message.endswith(f'from PowerShell:\n  & "{setup}"'))
        self.assertEqual(raised.exception.native_packages_installed, ["native"])

    def test_setup_failure_records_setup_failed_and_exits_non_zero(self):
        self.declare_setup()
        with self.setup_environment(), patch(
            "sima_cli.install.windows_package._run",
            side_effect=[completed(), completed(returncode=1)],
        ):
            registry, output = self.install_from_windows_metadata(fails=True)

        registry.update_state.assert_called_once_with(
            self.metadata["name"], self.metadata["version"], "setup-failed"
        )
        self.assertIn("setup step failed with exit code 1", output)

    def test_cancelled_setup_records_setup_failed(self):
        # Ctrl+C at the card password prompt: the packages are installed, so the receipt
        # must not say a native package failed.
        setup = self.declare_setup()
        with self.setup_environment(), patch(
            "sima_cli.install.windows_package._run",
            side_effect=[completed(), KeyboardInterrupt()],
        ):
            try:
                registry, output = self.install_from_windows_metadata(fails=True)
            except KeyboardInterrupt:
                self.fail("cancelling the setup step escaped as KeyboardInterrupt")

        registry.update_state.assert_called_once_with(
            self.metadata["name"], self.metadata["version"], "setup-failed"
        )
        self.assertIn("the setup step was cancelled. The installation was kept.", output)
        self.assertIn(f'enter this in PowerShell:\n  & "{setup}"', output)

    def test_missing_setup_command_after_install_is_reported(self):
        setup = self.declare_setup()
        setup.unlink()
        with self.setup_environment(), patch(
            "sima_cli.install.windows_package._run", return_value=completed()
        ):
            with self.assertRaisesRegex(WindowsSetupError, "setup command is missing"):
                install_windows_package(self.metadata, str(self.root), native_only=True)

    def test_native_only_invokes_msiexec_and_does_not_invoke_pip(self):
        self.metadata["installation"]["python"]["distribution"] = "other-package"
        output = io.StringIO()
        with patch(
            "sima_cli.install.windows_package._run", return_value=completed()
        ) as run, redirect_stdout(output):
            result = install_windows_package(
                self.metadata, str(self.root), native_only=True
            )

        self.assertEqual(result["python"], "not-requested")
        self.assertIn("other-package will not be installed.", output.getvalue())
        self.assertEqual(run.call_count, 1)
        command = run.call_args.args[0]
        self.assertEqual(command[:3], [MSIEXEC, "/i", str(self.msi)])
        self.assertIn("/norestart", command)
        self.assertIn("/L*v", command)

    def test_installed_product_is_repaired_instead_of_passed_to_install_again(self):
        self.use_scheduler_and_core()
        installed = {PRODUCT_CODES["simaai"]}
        output = io.StringIO()
        with patch(
            "sima_cli.install.windows_package._msi_product_installed",
            side_effect=lambda code: code in installed,
        ), patch(
            "sima_cli.install.windows_package._run", return_value=completed()
        ) as run, redirect_stdout(output):
            install_windows_package(self.metadata, str(self.root), native_only=True)

        scheduler, core = (entry.args[0] for entry in run.call_args_list)
        self.assertEqual(scheduler[1:], [
            "/fomus", str(self.scheduler_msi), "/passive", "/norestart", "/L*v",
            str(self.root / "sima-cli-msi-install-01-scheduler.log"),
        ])
        self.assertEqual(core[1:3], ["/i", str(self.msi)])
        self.assertIn("Repairing installed scheduler native package 1/2", output.getvalue())
        self.assertIn("scheduler native package repaired successfully.", output.getvalue())
        self.assertIn("Installing core native package 2/2", output.getvalue())

    def test_product_state_comes_from_windows_installer(self):
        from sima_cli.install.windows_package import _msi_product_installed

        windll = MagicMock()
        for state, expected in ((5, True), (-1, False), (2, False), (1, False)):
            windll.msi.MsiQueryProductStateW.return_value = state
            with self.subTest(state=state), patch(
                "sima_cli.install.windows_package.ctypes.windll", windll, create=True
            ):
                self.assertIs(_msi_product_installed(PRODUCT_CODES["sima"]), expected)
        windll.msi.MsiQueryProductStateW.assert_called_with(PRODUCT_CODES["sima"])

    def test_administrator_runs_msiexec_from_the_windows_system_directory(self):
        def system_directory(buffer, _size):
            buffer.value = "D:\\WINNT\\system32"
            return len(buffer.value)

        windll = MagicMock()
        windll.kernel32.GetSystemDirectoryW.side_effect = system_directory
        with patch(
            "sima_cli.install.windows_package.ctypes.windll", windll, create=True
        ), patch(
            "sima_cli.install.windows_package._run", return_value=completed()
        ) as run:
            install_windows_package(self.metadata, str(self.root), native_only=True)

        self.assertEqual(run.call_args.args[0][0], "D:\\WINNT\\system32\\msiexec.exe")

    def test_non_elevated_session_installs_both_msis_under_one_elevation(self):
        self.use_scheduler_and_core()
        run, requests = fake_elevation([0, 0])
        with self.non_elevated_session(run):
            result = install_windows_package(self.metadata, str(self.root), native_only=True)

        self.assertEqual(len(requests), 1)
        command = requests[0]["command"]
        self.assertEqual(command[:4], [
            POWERSHELL, "-NoLogo", "-NoProfile", "-NonInteractive"
        ])
        self.assertNotIn(str(self.msi), " ".join(command))
        self.assertIn("$startInfo.Verb = 'runas'", command[-1])
        request = requests[0]["request"]
        self.assertEqual(request["continue_codes"], [0])
        self.assertEqual(
            [(package["owner"], package["msi"], package["sha256"]) for package in request["packages"]],
            [
                ("scheduler", str(self.scheduler_msi), sha256(self.scheduler_msi)),
                ("core", str(self.msi), sha256(self.msi)),
            ],
        )
        self.assertTrue(request["packages"][0]["arguments"].startswith(
            f"/i {self.scheduler_msi} /passive /norestart /L*v "
        ))
        self.assertIn("sima-cli-msi-install-02-core.log", request["packages"][1]["arguments"])
        # The result file lives in a directory the CLI created and removes afterwards.
        self.assertFalse(Path(request["result"]).parent.exists())
        script = requests[0]["script"]
        self.assertIn("[IO.FileShare]::Read", script)
        self.assertIn("$request.continue_codes -notcontains $result.exit_code) { break }", script)
        self.assertEqual(
            [(package["owner"], package["product_code"]) for package in result["native_packages"]],
            [("scheduler", PRODUCT_CODES["simaai"]), ("core", PRODUCT_CODES["sima"])],
        )

    def test_windows_powershell_does_not_inherit_the_powershell_7_module_path(self):
        # Started from PowerShell 7, Windows PowerShell 5.1 would autoload PowerShell 7
        # modules from the inherited PSModulePath. Windows matches names in any case.
        inherited = {
            "PSModulePath": "C:\\Program Files\\PowerShell\\7\\Modules",
            "psmodulepath": "C:\\Users\\me\\Documents\\PowerShell\\Modules",
            "SIMA_CLI_TEST_KEPT": "kept",
        }
        run, _requests = fake_elevation([0])
        environments = []

        def capture(command, **kwargs):
            environments.append(kwargs["env"])
            if "SIMA_CLI_ELEVATED_ARGUMENTS" in kwargs["env"]:
                return run(command, **kwargs)
            return completed(stdout="Running\n")

        with patch.dict(os.environ, inherited), self.non_elevated_session(capture):
            install_windows_package(self.metadata, str(self.root), native_only=True)
            _check_windows_service("SiMaMlaDaemon")

        self.assertEqual(len(environments), 2)
        for environment in environments:
            self.assertEqual(
                [name for name in environment if name.upper() == "PSMODULEPATH"], []
            )
            self.assertEqual(environment["SIMA_CLI_TEST_KEPT"], "kept")
        self.assertIn("SIMA_CLI_ELEVATED_ARGUMENTS", environments[0])

    def test_elevated_scheduler_failure_stops_before_core(self):
        self.use_scheduler_and_core()
        run, requests = fake_elevation([1603, 0])
        with self.non_elevated_session(run):
            registry, output = self.install_from_windows_metadata(fails=True)

        self.assertEqual(len(requests), 1)
        self.assertIn("Windows Installer failed for scheduler with exit code 1603", output)
        self.assertIn("Packages completed earlier in this run: none", output)
        self.assertNotIn("core native package installed", output)
        registry.update_state.assert_called_once_with(
            self.metadata["name"], self.metadata["version"], "failed"
        )

    def test_elevated_core_failure_after_scheduler_records_partial_native_failure(self):
        self.use_scheduler_and_core()
        run, requests = fake_elevation([0, 1603])
        with self.non_elevated_session(run):
            registry, output = self.install_from_windows_metadata(fails=True)

        self.assertEqual(len(requests), 1)
        self.assertIn("scheduler native package installed successfully", output)
        self.assertIn("Windows Installer failed for core with exit code 1603", output)
        self.assertIn("Packages completed earlier in this run: scheduler", output)
        registry.update_state.assert_called_once_with(
            self.metadata["name"], self.metadata["version"], "partial-native-failed"
        )

    def test_elevated_restart_result_stops_before_core(self):
        self.use_scheduler_and_core()
        run, _requests = fake_elevation([3010, 0])
        with self.non_elevated_session(run):
            registry, output = self.install_from_windows_metadata(fails=True, exit_code=3010)

        self.assertIn("Native packages installed in this run: scheduler.", output)
        self.assertIn("Not yet run: native packages core", output)
        registry.update_state.assert_called_once_with(
            self.metadata["name"], self.metadata["version"], "restart-required"
        )

    def test_elevated_process_without_core_result_reports_unknown_result(self):
        # Scheduler reported success, then the elevated process ended without a
        # Core result; Core's outcome is unknown and Scheduler stays recorded.
        self.use_scheduler_and_core()
        run, _requests = fake_elevation([0])
        with self.non_elevated_session(run):
            registry, output = self.install_from_windows_metadata(fails=True)

        self.assertIn("Windows Installer for core ended without reporting an exit code", output)
        registry.update_state.assert_called_once_with(
            self.metadata["name"], self.metadata["version"], "partial-native-failed"
        )

    def test_elevated_hash_mismatch_refuses_the_msi(self):
        self.use_scheduler_and_core()
        run, _requests = fake_elevation(
            [ELEVATED_INSTALLER_HASH_MISMATCH], errors={0: "found " + "0" * 64}
        )
        with self.non_elevated_session(run), self.assertRaises(WindowsNativeInstallError) as raised:
            install_windows_package(self.metadata, str(self.root), native_only=True)

        message = str(raised.exception)
        self.assertIn(f"'{self.scheduler_msi.name}' no longer matched its declared SHA-256", message)
        self.assertIn("when the elevated installer checked it", message)
        self.assertIn("found " + "0" * 64, message)
        self.assertIn("it was not started", message)
        self.assertEqual(raised.exception.native_packages_installed, [])

    def test_msi_modified_before_elevation_is_not_elevated(self):
        self.use_scheduler_and_core()
        self.msi.write_bytes(b"replaced msi")
        run, requests = fake_elevation([0, 0])
        with self.non_elevated_session(run), self.assertRaisesRegex(
            WindowsNativeInstallError, "declared SHA-256"
        ):
            install_windows_package(self.metadata, str(self.root), native_only=True)

        self.assertEqual(requests, [])

    def test_receipt_records_product_codes_before_any_msi_starts(self):
        self.use_scheduler_and_core()
        recorded_before_run = []

        def run(command, **kwargs):
            recorded_before_run.append(metadata_installer.registry.update_fields.called)
            return completed()

        with patch(
            "sima_cli.install.windows_package._run", side_effect=run
        ):
            registry, _output = self.install_from_windows_metadata()

        self.assertEqual(recorded_before_run, [True, True])
        registry.update_fields.assert_called_once_with(
            self.metadata["name"],
            self.metadata["version"],
            {
                "native_packages": [
                    {"owner": "scheduler", "version": "0.6.1",
                     "product_code": PRODUCT_CODES["simaai"]},
                    {"owner": "core", "version": "0.6.1",
                     "product_code": PRODUCT_CODES["sima"]},
                ],
                "python_executable": None,
            },
        )

    def test_unreadable_product_code_blocks_before_msi(self):
        with patch(
            "sima_cli.install.windows_package._run",
            return_value=completed(returncode=1, stderr="The package could not be opened."),
        ) as run, self.assertRaisesRegex(
            WindowsNativeInstallError, "ProductCode of .*could not be opened.*No MSI was started"
        ):
            _msi_product_code(self.msi)
        self.assertEqual(run.call_args.kwargs["env"]["SIMA_CLI_MSI_PATH"], str(self.msi))

        with patch(
            "sima_cli.install.windows_package._run",
            return_value=completed(stdout="{abcdef01-2345-6789-abcd-ef0123456789}"),
        ):
            self.assertEqual(
                _msi_product_code(self.msi), "{ABCDEF01-2345-6789-ABCD-EF0123456789}"
            )

    def windows_receipt(self, registry_path, native_packages):
        self.use_scheduler_and_core()
        registry = PackageRegistry(registry_path)
        registry.create_entry(self.metadata["name"], "0.6.1", self.metadata, "")
        registry.update_fields(self.metadata["name"], "0.6.1", {
            "native_packages": native_packages,
            "python_executable": str(self.python),
        })
        registry.update_state(self.metadata["name"], "0.6.1", "installed")
        return registry

    def remove_windows_receipt(self, native_packages, run):
        """Run `packages remove` against a temporary registry; return (exit code, output, receipts)."""
        registry_path = self.root / "registry.json"
        registry_path.unlink(missing_ok=True)
        self.windows_receipt(registry_path, native_packages)
        return self.run_packages_remove(registry_path, "0.6.1", run)

    def run_packages_remove(self, registry_path, version, run):
        output = io.StringIO()
        # CliRunner replaces sys.stdin, so drive the command directly to keep a TTY.
        with self.non_elevated_session(run), patch(
            "sima_cli.install.registry.PackageRegistry",
            lambda: PackageRegistry(registry_path),
        ), redirect_stdout(output), redirect_stderr(output), self.assertRaises(
            SystemExit
        ) as raised:
            packages_group.main(
                ["remove", self.metadata["name"], "--version", version, "--yes"],
                prog_name="packages",
            )
        receipts = json.loads(registry_path.read_text(encoding="utf-8"))
        return raised.exception.code, output.getvalue(), receipts

    def recorded_cohort(self):
        return [
            {"owner": "scheduler", "version": "0.6.1", "product_code": PRODUCT_CODES["simaai"]},
            {"owner": "core", "version": "0.6.1", "product_code": PRODUCT_CODES["sima"]},
        ]

    def test_uninstall_removes_core_then_scheduler_under_one_elevation(self):
        run, requests = fake_elevation([0, 0])

        exit_code, output, receipts = self.remove_windows_receipt(self.recorded_cohort(), run)

        self.assertEqual(exit_code, 0, output)
        self.assertEqual(len(requests), 1)
        request = requests[0]["request"]
        self.assertEqual(request["continue_codes"], [0, 1605, 3010])
        self.assertEqual(
            [(package["owner"], package["arguments"].split()[:2]) for package in request["packages"]],
            [
                ("core", ["/x", PRODUCT_CODES["sima"]]),
                ("scheduler", ["/x", PRODUCT_CODES["simaai"]]),
            ],
        )
        self.assertEqual([package["msi"] for package in request["packages"]], [None, None])
        self.assertIn("Removing native packages: core, scheduler", output)
        self.assertIn(f"& \"{self.python}\" -m pip uninstall -y pyneatpcie", output)
        self.assertIn("Removed local registry receipt", output)
        self.assertEqual(receipts, [])

    def test_removing_a_failed_upgrade_keeps_the_scheduler_the_installed_release_uses(self):
        # Approval for 0.6.2 was cancelled after its receipt recorded the shared Scheduler.
        registry_path = self.root / "registry.json"
        registry = self.windows_receipt(registry_path, self.recorded_cohort())
        new_core = "{33333333-3333-3333-3333-333333333333}"
        registry.create_entry(self.metadata["name"], "0.6.2", self.metadata, "")
        registry.update_fields(self.metadata["name"], "0.6.2", {"native_packages": [
            {"owner": "scheduler", "version": "0.6.1", "product_code": PRODUCT_CODES["simaai"]},
            {"owner": "core", "version": "0.6.2", "product_code": new_core},
        ]})
        registry.update_state(self.metadata["name"], "0.6.2", "failed")

        run, requests = fake_elevation([1605])
        exit_code, output, receipts = self.run_packages_remove(registry_path, "0.6.2", run)

        self.assertEqual(exit_code, 0, output)
        self.assertEqual(
            [package["arguments"].split()[:2] for package in requests[0]["request"]["packages"]],
            [["/x", new_core]],
        )
        self.assertIn(
            "Keeping native packages another receipt uses: scheduler (still recorded by "
            f"{self.metadata['name']}@0.6.1)",
            output,
        )
        self.assertEqual([receipt["version"] for receipt in receipts], ["0.6.1"])

        run, requests = fake_elevation([0, 0])
        exit_code, output, receipts = self.run_packages_remove(registry_path, "0.6.1", run)

        self.assertEqual(exit_code, 0, output)
        self.assertEqual(
            [package["arguments"].split()[:2] for package in requests[0]["request"]["packages"]],
            [["/x", PRODUCT_CODES["sima"]], ["/x", PRODUCT_CODES["simaai"]]],
        )
        self.assertNotIn("Keeping native packages", output)
        self.assertEqual(receipts, [])

    def test_uninstall_treats_an_absent_package_as_removed(self):
        run, _requests = fake_elevation([1605, 0])

        exit_code, output, receipts = self.remove_windows_receipt(self.recorded_cohort(), run)

        self.assertEqual(exit_code, 0, output)
        self.assertIn("core native package is not installed; nothing to remove.", output)
        self.assertIn("scheduler native package removed.", output)
        self.assertEqual(receipts, [])

    def test_uninstall_core_failure_keeps_scheduler_and_records_remove_failed(self):
        run, _requests = fake_elevation([1603, 0])

        exit_code, output, receipts = self.remove_windows_receipt(self.recorded_cohort(), run)

        self.assertNotEqual(exit_code, 0)
        self.assertIn("Windows Installer failed to remove core with exit code 1603", output)
        self.assertIn("Not attempted: scheduler", output)
        self.assertEqual([receipt["state"] for receipt in receipts], ["remove-failed"])

    def test_uninstall_maps_decline_launch_failure_and_unknown_result(self):
        cases = [
            (fake_elevation([], wrapper_code=1223)[0], "administrator approval was cancelled"),
            (
                lambda *_args, **_kwargs: completed(
                    returncode=ELEVATED_INSTALLER_LAUNCH_FAILED, stderr="no shell\n"
                ),
                "Could not start Windows Installer to remove core with administrator "
                "approval: no shell.",
            ),
            (fake_elevation([0])[0], "did not report a result for removing scheduler"),
        ]
        for run, message in cases:
            with self.subTest(message=message):
                exit_code, output, receipts = self.remove_windows_receipt(self.recorded_cohort(), run)

                self.assertNotEqual(exit_code, 0)
                self.assertIn(message, output)
                self.assertEqual([receipt["state"] for receipt in receipts], ["remove-failed"])

    def test_uninstall_restart_required_removes_both_and_exits_3010(self):
        run, _requests = fake_elevation([3010, 0])

        exit_code, output, receipts = self.remove_windows_receipt(self.recorded_cohort(), run)

        self.assertEqual(exit_code, 3010, output)
        self.assertIn("scheduler native package removed.", output)
        self.assertIn("Restart Windows to finish removing", output)
        self.assertEqual(receipts, [])

    def test_uninstall_refuses_an_invalid_recorded_product_code(self):
        cohort = self.recorded_cohort()
        cohort[1]["product_code"] = "/i \\\\server\\share\\other.msi"
        run, requests = fake_elevation([0, 0])

        exit_code, output, receipts = self.remove_windows_receipt(cohort, run)

        self.assertNotEqual(exit_code, 0)
        self.assertIn("invalid product code for core", output)
        self.assertEqual(requests, [])
        self.assertEqual([receipt["state"] for receipt in receipts], ["remove-failed"])

    def test_uninstall_without_recorded_product_codes_removes_only_the_receipt(self):
        run, requests = fake_elevation([0, 0])

        exit_code, output, receipts = self.remove_windows_receipt([], run)

        self.assertEqual(exit_code, 0, output)
        self.assertIn("records no Windows product codes", output)
        self.assertEqual(requests, [])
        self.assertEqual(receipts, [])

    def test_administrator_uninstall_runs_msiexec_in_reverse_install_order(self):
        with patch(
            "sima_cli.install.windows_package._run", return_value=completed()
        ) as run:
            restart = uninstall_windows_package({"native_packages": self.recorded_cohort()})

        self.assertFalse(restart)
        self.assertEqual(
            [command.args[0][:3] for command in run.call_args_list],
            [
                [MSIEXEC, "/x", PRODUCT_CODES["sima"]],
                [MSIEXEC, "/x", PRODUCT_CODES["simaai"]],
            ],
        )

    def test_uninstall_starts_no_installer_when_other_receipts_record_every_package(self):
        shared = {code: "other@0.6.0" for code in PRODUCT_CODES.values()}
        output = io.StringIO()
        with patch("sima_cli.install.windows_package._run") as run, redirect_stdout(output):
            restart = uninstall_windows_package({"native_packages": self.recorded_cohort()}, shared)

        self.assertFalse(restart)
        run.assert_not_called()
        self.assertIn(
            "Keeping native packages another receipt uses: core (still recorded by "
            "other@0.6.0), scheduler (still recorded by other@0.6.0)",
            output.getvalue(),
        )
        self.assertNotIn("Removing native packages", output.getvalue())

    def test_noninteractive_non_elevated_session_fails_before_starting_installer(self):
        with patch(
            "sima_cli.install.windows_package._is_windows_administrator",
            return_value=False,
        ), patch(
            "sima_cli.install.windows_package._interactive_console", return_value=False
        ), patch("sima_cli.install.windows_package._run") as run:
            with self.assertRaises(WindowsNativeInstallError) as raised:
                install_windows_package(
                    self.metadata, str(self.root), native_only=True
                )

        run.assert_not_called()
        message = str(raised.exception)
        self.assertIn("interactive PowerShell", message)
        self.assertIn("this session is not elevated and its input is not interactive", message)
        self.assertIn("ask an administrator to run it", message)

    def test_nul_stdin_non_elevated_session_fails_before_starting_installer(self):
        with patch(
            "sima_cli.install.windows_package._is_windows_administrator",
            return_value=False,
        ), self.windows_stdin(is_console=False, has_window=True), patch(
            "sima_cli.install.windows_package._run"
        ) as run:
            with self.assertRaisesRegex(WindowsNativeInstallError, "input is not interactive"):
                install_windows_package(self.metadata, str(self.root), native_only=True)

        run.assert_not_called()

    def test_cancelled_elevation_stops_the_transaction(self):
        with patch(
            "sima_cli.install.windows_package._is_windows_administrator",
            return_value=False,
        ), patch(
            "sima_cli.install.windows_package._interactive_console", return_value=True
        ), patch(
            "sima_cli.install.windows_package._run", return_value=completed(returncode=1223)
        ):
            with self.assertRaisesRegex(
                WindowsNativeInstallError, "administrator approval was cancelled"
            ):
                install_windows_package(
                    self.metadata, str(self.root), native_only=True
                )

    def test_missing_elevated_installer_exit_code_fails_the_transaction(self):
        with patch(
            "sima_cli.install.windows_package._is_windows_administrator",
            return_value=False,
        ), patch(
            "sima_cli.install.windows_package._interactive_console", return_value=True
        ), patch(
            "sima_cli.install.windows_package._run",
            return_value=completed(returncode=ELEVATED_INSTALLER_EXIT_CODE_MISSING),
        ) as run:
            with self.assertRaisesRegex(
                WindowsNativeInstallError, "without reporting an exit code"
            ):
                install_windows_package(
                    self.metadata, str(self.root), native_only=True
                )

        powershell = run.call_args.args[0][-1]
        self.assertIn("if ($null -eq $process) {", powershell)
        self.assertIn(f"exit {ELEVATED_INSTALLER_EXIT_CODE_MISSING}", powershell)

    def test_elevated_installer_launch_failure_is_not_reported_as_cancelled(self):
        # Only Win32 error 1223 from the approval prompt means the user declined;
        # any other launch error must say what failed.
        with patch(
            "sima_cli.install.windows_package._is_windows_administrator",
            return_value=False,
        ), patch(
            "sima_cli.install.windows_package._interactive_console", return_value=True
        ), patch(
            "sima_cli.install.windows_package._run",
            return_value=completed(
                returncode=ELEVATED_INSTALLER_LAUNCH_FAILED,
                stderr="The system cannot find the file specified\n",
            ),
        ) as run:
            with self.assertRaises(WindowsNativeInstallError) as raised:
                install_windows_package(
                    self.metadata, str(self.root), native_only=True
                )

        message = str(raised.exception)
        self.assertNotIn("cancelled", message)
        self.assertIn("Could not start Windows Installer for native", message)
        self.assertIn("The system cannot find the file specified", message)
        self.assertIn("msiexec.exe are available", message)
        self.assertIn("sima-cli-msi-install-01-native.log", message)
        powershell = run.call_args.args[0][-1]
        self.assertIn("$exception.NativeErrorCode -eq 1223", powershell)
        self.assertIn(f"exit {ELEVATED_INSTALLER_LAUNCH_FAILED}", powershell)
        self.assertNotIn("Start-Process", powershell)

    def test_coordinated_native_packages_install_in_manifest_order(self):
        self.use_scheduler_and_core()

        self.assertTrue(validate_metadata(self.metadata))
        with patch(
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

    def test_msi_modified_after_download_is_not_started(self):
        self.use_scheduler_and_core()

        def install_scheduler_and_replace_core(*args, **kwargs):
            self.msi.write_bytes(b"replaced msi")
            return completed()

        with patch(
            "sima_cli.install.windows_package._run",
            side_effect=install_scheduler_and_replace_core,
        ) as run:
            with self.assertRaisesRegex(WindowsNativeInstallError, "declared SHA-256"):
                install_windows_package(
                    self.metadata, str(self.root), native_only=True
                )

        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.args[0][2], str(self.scheduler_msi))

    def test_unreadable_second_msi_records_partial_native_failure(self):
        self.use_scheduler_and_core()
        compute_sha256 = metadata_installer._compute_sha256

        def locked_core_msi(path):
            if path == self.msi:
                raise PermissionError(13, "The file is locked", str(path))
            return compute_sha256(path)

        with patch(
            "sima_cli.install.windows_package._run", return_value=completed()
        ) as run, patch.object(
            metadata_installer, "_compute_sha256", side_effect=locked_core_msi
        ):
            registry, output = self.install_from_windows_metadata(fails=True)

        self.assertEqual(run.call_count, 1)
        self.assertIn(f"'{self.msi.name}' could not be read", output)
        self.assertIn("The file is locked", output)
        registry.update_state.assert_called_once_with(
            self.metadata["name"], self.metadata["version"], "partial-native-failed"
        )

    def test_coordinated_native_failure_reports_completed_packages(self):
        self.use_scheduler_and_core()
        with patch(
            "sima_cli.install.windows_package._run",
            side_effect=[completed(), completed(returncode=1603)],
        ):
            with self.assertRaisesRegex(WindowsNativeInstallError, "scheduler"):
                install_windows_package(
                    self.metadata, str(self.root), native_only=True
                )

    def test_downgrade_refusal_says_to_remove_the_newer_release(self):
        self.use_scheduler_and_core()
        core_log = self.root / "sima-cli-msi-install-02-core.log"
        lines = (
            "MSI (s) (A4:2C) [10:01:02:345]: PROPERTY CHANGE: Adding WIX_DOWNGRADE_DETECTED "
            "property. Its value is '{44444444-4444-4444-4444-444444444444}'.\r\n"
            "Property(S): WIX_DOWNGRADE_DETECTED = {44444444-4444-4444-4444-444444444444}\r\n"
        )
        # Windows Installer writes verbose logs as UTF-16 or as ANSI text.
        for encoded in (b"\xff\xfe" + lines.encode("utf-16-le"), lines.encode("ascii"),
                        lines.splitlines()[1].encode("utf-16-le")):
            def run(command, **_kwargs):
                if command[2] == str(self.msi):
                    core_log.write_bytes(encoded)
                    return completed(returncode=1603)
                return completed()

            with self.subTest(encoded=encoded[:4]), patch(
                "sima_cli.install.windows_package._run", side_effect=run
            ):
                with self.assertRaises(WindowsNativeInstallError) as raised:
                    install_windows_package(
                        self.metadata, str(self.root), native_only=True,
                        retry_command=PINNED_RETRY,
                    )

            message = raised.exception.message
            self.assertIn(
                "Windows Installer refused core 0.6.1 because a newer version of it is already "
                "installed; Windows Installer does not downgrade a package in place.", message
            )
            self.assertIn("Packages completed earlier in this run: scheduler.", message)
            self.assertIn(
                f"sima-cli packages remove {self.metadata['name']} --version <installed version>",
                message,
            )
            self.assertTrue(message.endswith(f"then rerun this exact release: {PINNED_RETRY}"))

    def test_other_install_failures_keep_the_generic_message(self):
        log = self.root / "sima-cli-msi-install-01-native.log"
        for code, text in ((1603, "Property(S): WIX_UPGRADE_DETECTED = {x}\r\n"), (1618, "")):
            def run(command, **_kwargs):
                log.write_text(text, encoding="ascii")
                return completed(returncode=code)

            with self.subTest(code=code), patch(
                "sima_cli.install.windows_package._run", side_effect=run
            ):
                with self.assertRaisesRegex(
                    WindowsNativeInstallError, f"failed for native with exit code {code}"
                ):
                    install_windows_package(self.metadata, str(self.root), native_only=True)

    def test_policy_rejected_msi_reports_policy_without_bypass_guidance(self):
        with patch(
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

    def test_unusable_selected_python_blocks_before_msi(self):
        cases = [
            ("old version", {"return_value": python_facts("3.13")}, "requires Python 3.14", 1),
            ("wrong arch", {"return_value": python_facts(arch="ARM64")}, "requires amd64", 1),
            ("launch failure", {"side_effect": OSError("access denied")}, "no MSI was started", 1),
            ("missing executable", {}, "no MSI was started", 0),
        ]
        for case, run_result, message, calls in cases:
            if case == "missing executable":
                self.python.unlink()
            with self.subTest(case), patch(
                "sima_cli.install.windows_package._run", **run_result
            ) as run:
                with self.assertRaisesRegex(WindowsPrerequisiteError, message):
                    install_windows_package(
                        self.metadata, str(self.root), python_executable=str(self.python)
                    )
                self.assertEqual(run.call_count, calls)

    def test_selected_python_without_pip_explains_how_to_restore_it(self):
        with patch(
            "sima_cli.install.windows_package._run",
            return_value=completed(1, stderr="ModuleNotFoundError: No module named 'pip'"),
        ):
            with self.assertRaises(WindowsPrerequisiteError) as raised:
                install_windows_package(
                    self.metadata, str(self.root), python_executable=str(self.python)
                )
        message = raised.exception.message
        self.assertIn("cannot run pip; no MSI was started", message)
        self.assertIn(f'"{self.python}" -m ensurepip --upgrade', message)
        self.assertIn(
            "Install Python 3.14 (amd64) for Windows from "
            "https://www.python.org/downloads/windows/",
            message,
        )

    def test_missing_declared_prerequisite_blocks_before_msi(self):
        self.metadata["installation"]["prerequisites"] = [OPENSSH]
        self.assertTrue(validate_metadata(self.metadata))
        with patch(
            "sima_cli.install.windows_package.shutil.which", return_value=None
        ), patch("sima_cli.install.windows_package._run") as run:
            with self.assertRaisesRegex(WindowsPrerequisiteError, "OpenSSH"):
                install_windows_package(self.metadata, str(self.root))
        run.assert_not_called()

    def test_service_prerequisite_is_checked_before_msi(self):
        self.metadata["installation"]["prerequisites"] = [
            {
                "name": "Platform service",
                "type": "windows-service",
                "value": "SiMaMlaDaemon",
                "guidance": "Install the Platform package.",
            },
        ]
        self.assertTrue(validate_metadata(self.metadata))
        with patch(
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
        self.metadata["installation"]["prerequisites"] = [PLATFORM_DRIVER, GSTREAMER]
        self.assertTrue(validate_metadata(self.metadata))
        with patch.dict(os.environ, self.installed_gstreamer()), patch(
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
        self.metadata["installation"]["prerequisites"] = [PLATFORM_DRIVER]
        with patch(
            "sima_cli.install.windows_package._windows_pnp_driver_version",
            return_value="3.0.0.1",
        ), patch("sima_cli.install.windows_package._run") as run:
            with self.assertRaisesRegex(
                WindowsPrerequisiteError, "found 3.0.0.1, expected 3.0.0.2"
            ):
                install_windows_package(self.metadata, str(self.root))
        run.assert_not_called()

    def test_mixed_platform_driver_versions_block_before_msi(self):
        self.metadata["installation"]["prerequisites"] = [PLATFORM_DRIVER]
        with patch(
            "sima_cli.install.windows_package._run",
            return_value=completed(
                returncode=3,
                stderr=(
                    "Windows PnP devices named 'SiMa.ai MLA Device' report mixed "
                    "DriverVersion values: 3.0.0.1, 3.0.0.2."
                ),
            ),
        ) as run:
            with self.assertRaisesRegex(
                WindowsPrerequisiteError,
                "mixed DriverVersion values: 3.0.0.1, 3.0.0.2",
            ):
                install_windows_package(self.metadata, str(self.root))

        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.args[0][0], POWERSHELL)

    def test_wrong_gstreamer_version_blocks_before_msi(self):
        self.metadata["installation"]["prerequisites"] = [GSTREAMER]
        with patch.dict(os.environ, self.installed_gstreamer()), patch(
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
            "sima_cli.install.windows_package._run",
            side_effect=[completed(), OSError("blocked")],
        ):
            with self.assertRaisesRegex(WindowsNativeInstallError, "installation was kept"):
                install_windows_package(
                    self.metadata, str(self.root), native_only=True
                )

    def test_same_owner_customer_skill_is_replaced_after_native_verification(self):
        skill = self.root / "installed" / "skill"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("# Customer skill\n", encoding="utf-8")
        self.metadata["installation"]["skills"] = [
            {
                "id": "skill",
                "environment": "TEST_PROGRAM_FILES",
                "relative": "installed\\skill",
            }
        ]
        self.assertTrue(validate_metadata(self.metadata))
        with patch.dict(os.environ, {"TEST_PROGRAM_FILES": str(self.root)}), patch(
            "sima_cli.install.windows_package._run", return_value=completed()
        ), patch(
            "sima_cli.install.windows_package.SkillManager"
        ) as manager_type:
            manager_type.return_value.install.return_value = ["skill"]
            result = install_windows_package(
                self.metadata, str(self.root), native_only=True
            )

        manager_type.return_value.check_install_ownership.assert_called_once_with(
            "skill", str(skill)
        )
        self.assertEqual(
            manager_type.return_value.install.call_args_list,
            [
                call(str(skill), dry_run=True),
                call(str(skill), replace_same_source=True),
            ],
        )
        self.assertEqual(result["skills"], ["skill"])

    def test_discovered_skill_id_mismatch_does_not_write_customer_skill(self):
        skill = self.root / "installed" / "skill"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("# Customer skill\n", encoding="utf-8")
        self.metadata["installation"]["skills"] = [{
            "id": "declared-skill",
            "environment": "TEST_PROGRAM_FILES",
            "relative": "installed\\skill",
        }]
        with patch.dict(os.environ, {"TEST_PROGRAM_FILES": str(self.root)}), patch(
            "sima_cli.install.windows_package._run", return_value=completed()
        ) as run, patch(
            "sima_cli.install.windows_package.SkillManager"
        ) as manager_type:
            manager_type.return_value.install.return_value = ["unexpected-skill"]
            with self.assertRaisesRegex(
                WindowsNativeInstallError,
                "declared skill 'declared-skill'.*unexpected-skill",
            ):
                install_windows_package(
                    self.metadata, str(self.root), native_only=True
                )

        self.assertEqual(run.call_count, 1)
        manager_type.return_value.install.assert_called_once_with(
            str(skill), dry_run=True
        )

    def test_different_owner_customer_skill_blocks_before_native_install(self):
        skill = (
            self.root
            / "SimaPCIeHost"
            / "share"
            / "sima-pcie-host"
            / "skills"
            / "neat-pcie-application-builder"
        )
        self.metadata["installation"]["skills"] = [{
            "id": "neat-pcie-application-builder",
            "environment": "TEST_PROGRAM_FILES",
            "relative": (
                "SimaPCIeHost\\share\\sima-pcie-host\\skills\\"
                "neat-pcie-application-builder"
            ),
        }]
        with patch.dict(os.environ, {"TEST_PROGRAM_FILES": str(self.root)}), patch(
            "sima_cli.install.windows_package._run", return_value=completed()
        ) as run, patch(
            "sima_cli.install.windows_package.SkillManager"
        ) as manager_type:
            manager_type.return_value.check_install_ownership.side_effect = SkillError(
                "Skill 'neat-pcie-application-builder' is already owned by "
                "'C:\\independent-skill'."
            )
            with self.assertRaisesRegex(
                WindowsPrerequisiteError, "already owned.*No native package was installed"
            ):
                install_windows_package(
                    self.metadata, str(self.root), native_only=True
                )

        manager_type.return_value.check_install_ownership.assert_called_once_with(
            "neat-pcie-application-builder", str(skill)
        )
        manager_type.return_value.install.assert_not_called()
        run.assert_not_called()

    def test_invalid_declared_skill_id_blocks_before_native_install(self):
        self.metadata["installation"]["skills"] = [{
            "id": "invalid skill",
            "environment": "TEST_PROGRAM_FILES",
            "relative": "SimaPCIeHost\\skills\\invalid skill",
        }]
        with patch.dict(os.environ, {"TEST_PROGRAM_FILES": str(self.root)}), patch(
            "sima_cli.install.windows_package._run", return_value=completed()
        ) as run, patch(
            "sima_cli.install.windows_package.SkillManager"
        ) as manager_type:
            with self.assertRaisesRegex(
                WindowsPrerequisiteError, "invalid customer skill ID"
            ):
                install_windows_package(
                    self.metadata, str(self.root), native_only=True
                )

        manager_type.return_value.check_install_ownership.assert_not_called()
        run.assert_not_called()

    def test_missing_installed_customer_skill_reports_retained_native_install(self):
        self.metadata["installation"]["skills"] = [
            {
                "id": "skill",
                "environment": "TEST_PROGRAM_FILES",
                "relative": "missing\\skill",
            }
        ]
        with patch.dict(os.environ, {"TEST_PROGRAM_FILES": str(self.root)}), patch(
            "sima_cli.install.windows_package._run", return_value=completed()
        ):
            with self.assertRaisesRegex(WindowsNativeInstallError, "customer skill is missing"):
                install_windows_package(
                    self.metadata, str(self.root), native_only=True
                )

    def test_customer_skill_registry_failure_blocks_before_native_install(self):
        skill = self.root / "installed" / "skill"
        skill.mkdir(parents=True)
        self.metadata["installation"]["skills"] = [
            {
                "id": "skill",
                "environment": "TEST_PROGRAM_FILES",
                "relative": "installed\\skill",
            }
        ]
        with patch.dict(os.environ, {"TEST_PROGRAM_FILES": str(self.root)}), patch(
            "sima_cli.install.windows_package._run", return_value=completed()
        ) as run, patch(
            "sima_cli.install.windows_package.SkillManager",
            side_effect=OSError("registry unavailable"),
        ):
            with self.assertRaisesRegex(
                WindowsPrerequisiteError, "registry could not be opened"
            ):
                install_windows_package(
                    self.metadata, str(self.root), native_only=True
                )
        run.assert_not_called()

    def test_msiexec_permission_denial_is_reported_with_log_location(self):
        with patch(
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
        with patch(
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

    def python_install(self, *parts):
        """Create an interpreter fixture at a path below the test root."""
        path = self.root.joinpath(*parts, "python.exe")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"python fixture")
        return str(path)

    def bootstrap_candidates(self, on_path=None, launcher=None, environment=None):
        """List candidates for a host whose PATH holds on_path and no other Python."""
        on_path = on_path or {}
        names = dict(on_path, **({"py.exe": launcher} if launcher else {}))
        sima_cli_python = self.python_install("sima-cli-env", "Scripts")
        with patch.dict(os.environ, environment or {}, clear=True), patch(
            "sima_cli.install.windows_package.shutil.which", side_effect=names.get
        ), patch("sima_cli.install.windows_package.sys.executable", sima_cli_python):
            return _python_bootstrap_candidates("3.14"), sima_cli_python

    def inspect_by_command(self, results):
        """Answer each interpreter inspection by the command's first two parts."""
        def run(command, **_kwargs):
            return results[" ".join(command[:2]) if command[1] == "-3.14" else command[0]]
        return run

    def test_all_users_python_314_is_a_bootstrap_candidate(self):
        all_users = self.python_install("Program Files", "Python314")
        candidates, _ = self.bootstrap_candidates(
            environment={"ProgramFiles": str(self.root / "Program Files")}
        )
        self.assertIn([all_users], candidates)

    def test_rejected_interpreters_are_listed_with_their_reasons(self):
        python_313 = self.python_install("Python313")
        store_alias = self.python_install("WindowsApps")
        with patch(
            "sima_cli.install.windows_package._python_bootstrap_candidates",
            return_value=[["py.exe", "-3.14"], [store_alias], [python_313]],
        ), patch("sima_cli.install.windows_package._run", side_effect=self.inspect_by_command({
            "py.exe -3.14": completed(returncode=103, stderr="No suitable Python runtime found\n"),
            store_alias: completed(
                returncode=9009,
                stderr="Python was not found; run without arguments to install from the "
                       "Microsoft Store, or disable this shortcut from Settings > Apps.\n",
            ),
            python_313: python_facts(version="3.13", tag="cp313-cp313-win_amd64"),
        })):
            with self.assertRaises(WindowsPrerequisiteError) as raised:
                _bootstrap_python(self.metadata["installation"]["python"], self.root / "venv")

        message = raised.exception.message
        self.assertIn("Interpreters tried:\n  - py.exe -3.14: No suitable Python runtime found\n", message)
        self.assertIn(f"  - {store_alias}: Python was not found; run without arguments", message)
        self.assertIn(
            f"  - {python_313}: Python 3.13 was found; this package requires Python 3.14.\n", message
        )
        self.assertIn("--python-executable <path>, or use --native-only. No MSI was started.", message)

    def test_store_alias_python_is_skipped_for_the_next_candidate(self):
        store_alias = self.python_install("WindowsApps")
        per_user = self.python_install("AppData", "Local", "Programs", "Python", "Python314")
        candidates, sima_cli_python = self.bootstrap_candidates(
            on_path={"python.exe": store_alias},
            environment={"LOCALAPPDATA": str(self.root / "AppData" / "Local")},
        )
        self.assertEqual(candidates, [[store_alias], [sima_cli_python], [per_user]])
        with patch("sima_cli.install.windows_package._run", side_effect=self.inspect_by_command({
            store_alias: completed(returncode=9009, stderr="Python was not found\n"),
            sima_cli_python: python_facts(version="3.12", tag="cp312-cp312-win_amd64"),
            per_user: python_facts(),
        })), patch(
            "sima_cli.install.windows_package._python_bootstrap_candidates",
            return_value=candidates,
        ):
            selected = _bootstrap_python(self.metadata["installation"]["python"], self.root / "venv")
        self.assertEqual(selected, [per_user])

    def test_conda_or_pyenv_python_first_on_path_is_used_only_when_compatible(self):
        conda = self.python_install("miniconda3", "envs", "py314")
        per_user = self.python_install("AppData", "Local", "Programs", "Python", "Python314")
        candidates, sima_cli_python = self.bootstrap_candidates(
            on_path={"python.exe": conda},
            environment={"LOCALAPPDATA": str(self.root / "AppData" / "Local")},
        )
        self.assertEqual(candidates[0], [conda])
        for conda_facts, expected in (
            (python_facts(), [conda]),
            (python_facts(version="3.13", tag="cp313-cp313-win_amd64"), [per_user]),
        ):
            with self.subTest(expected=expected), patch(
                "sima_cli.install.windows_package._run", side_effect=self.inspect_by_command({
                    conda: conda_facts,
                    sima_cli_python: python_facts(version="3.12", tag="cp312-cp312-win_amd64"),
                    per_user: python_facts(),
                })
            ), patch(
                "sima_cli.install.windows_package._python_bootstrap_candidates",
                return_value=candidates,
            ):
                self.assertEqual(
                    _bootstrap_python(self.metadata["installation"]["python"], self.root / "venv"),
                    expected,
                )

    def test_python_313_and_314_side_by_side_selects_314_through_the_launcher(self):
        python_313 = self.python_install("Python313")
        launcher = self.python_install("Windows")
        candidates, _ = self.bootstrap_candidates(
            on_path={"python.exe": python_313}, launcher=launcher
        )
        self.assertEqual(candidates[:2], [[launcher, "-3.14"], [python_313]])
        with patch("sima_cli.install.windows_package._run", return_value=python_facts()) as run, patch(
            "sima_cli.install.windows_package._python_bootstrap_candidates",
            return_value=candidates,
        ):
            selected = _bootstrap_python(self.metadata["installation"]["python"], self.root / "venv")
        self.assertEqual(selected, [launcher, "-3.14"])
        self.assertEqual(run.call_count, 1)

    def test_managed_environment_on_an_unusable_home_points_to_python_executable(self):
        home = Path("\\\\fileserver\\home\\sima")
        with patch("sima_cli.install.windows_package.Path.home", return_value=home), patch(
            "sima_cli.install.windows_package._python_bootstrap_candidates",
            return_value=[[str(self.python)]],
        ), patch("sima_cli.install.windows_package._run", side_effect=[
            python_facts(),
            completed(returncode=1, stderr="Error: [WinError 5] Access is denied"),
        ]):
            with self.assertRaises(WindowsPrerequisiteError) as raised:
                _managed_python_executable(self.metadata)

        message = raised.exception.message
        self.assertIn("Access is denied", message)
        self.assertIn("--python-executable <path>, or use --native-only. No MSI was started.", message)

    def managed_environment(self, base_home):
        home = self.root / "home"
        environment = home / "pyneatpcie"
        managed_python = environment / "Scripts" / "python.exe"
        managed_python.parent.mkdir(parents=True)
        managed_python.write_bytes(b"managed python fixture")
        (environment / "pyvenv.cfg").write_text(
            f"home = {base_home}\ninclude-system-site-packages = false\nversion = 3.13.5\n",
            encoding="utf-8",
        )
        return home, environment

    def test_reused_environment_with_an_old_python_names_the_fix(self):
        home, environment = self.managed_environment(Path(self.python_install("Python313")).parent)
        with patch("sima_cli.install.windows_package.Path.home", return_value=home), patch(
            "sima_cli.install.windows_package._run",
            return_value=python_facts(version="3.13", tag="cp313-cp313-win_amd64"),
        ):
            with self.assertRaises(WindowsPrerequisiteError) as raised:
                _managed_python_executable(self.metadata)

        message = raised.exception.message
        self.assertIn(
            f"Managed Python environment '{environment}' cannot be reused: Python 3.13 was found; "
            "this package requires Python 3.14.", message,
        )
        self.assertIn(f'Remove-Item -Recurse -Force "{environment}"', message)
        self.assertIn("--python-executable <path>", message)

    def test_reused_environment_whose_base_python_was_removed_skips_ensurepip_advice(self):
        removed = self.root / "Python313-removed"
        home, environment = self.managed_environment(removed)
        with patch("sima_cli.install.windows_package.Path.home", return_value=home), patch(
            "sima_cli.install.windows_package._run"
        ) as run:
            with self.assertRaises(WindowsPrerequisiteError) as raised:
                _managed_python_executable(self.metadata)

        message = raised.exception.message
        self.assertIn(f"was created from '{removed / 'python.exe'}', which no longer exists", message)
        self.assertIn(f'Remove-Item -Recurse -Force "{environment}"', message)
        self.assertNotIn("ensurepip", message)
        run.assert_not_called()

    def test_default_install_blocks_before_msi_without_compatible_python(self):
        with patch(
            "sima_cli.install.windows_package._python_bootstrap_candidates", return_value=[]
        ), patch("sima_cli.install.windows_package._run") as run:
            with self.assertRaisesRegex(WindowsPrerequisiteError, "compatible Python 3.14"):
                install_windows_package(self.metadata, str(self.root))

        run.assert_not_called()

    def test_native_only_rejects_python_override(self):
        with patch(
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
        responses = [
            python_facts(),
            completed(),
            completed(stdout="0.6.1\n"),
            direct_url(sha256(self.wheel)),
            completed(),
        ]
        with patch(
            "sima_cli.install.windows_package._run", side_effect=responses
        ) as run:
            result = install_windows_package(
                self.metadata, str(self.root), python_executable=str(self.python)
            )

        self.assertEqual(result["python"], "already-installed")
        self.assertEqual(run.call_count, 5)
        self.assertNotIn("pip", [part for call in run.call_args_list for part in call.args[0]])

    def test_matching_broken_python_package_is_reinstalled_and_verified(self):
        responses = [
            python_facts(),
            completed(),
            completed(stdout="0.6.1\n"),
            direct_url(sha256(self.wheel)),
            completed(returncode=1, stderr="import failed"),
            completed(),
            completed(stdout="0.6.1\n"),
            completed(),
        ]
        with patch(
            "sima_cli.install.windows_package._run", side_effect=responses
        ) as run:
            result = install_windows_package(
                self.metadata, str(self.root), python_executable=str(self.python)
            )

        self.assertEqual(result["python"], "installed")
        self.assertEqual(
            run.call_args_list[5].args[0],
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

    def test_same_python_version_from_another_build_is_force_reinstalled(self):
        responses = [
            python_facts(),
            completed(),
            completed(stdout="0.6.1\n"),
            direct_url("d" * 64),
            completed(),
            completed(stdout="0.6.1\n"),
            completed(),
        ]
        with patch(
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
                "--no-deps",
                str(self.wheel),
            ],
        )

    def test_same_python_version_with_legacy_matching_hash_is_not_reinstalled(self):
        legacy_record = completed(
            stdout=json.dumps({"archive_info": {"hash": f"sha256={sha256(self.wheel)}"}})
        )
        responses = [
            python_facts(), completed(), completed(stdout="0.6.1\n"), legacy_record, completed()
        ]
        with patch(
            "sima_cli.install.windows_package._run", side_effect=responses
        ) as run:
            result = install_windows_package(
                self.metadata, str(self.root), python_executable=str(self.python)
            )

        self.assertEqual(result["python"], "already-installed")
        self.assertNotIn("pip", [part for call in run.call_args_list for part in call.args[0]])

    def test_same_python_version_without_install_record_is_force_reinstalled(self):
        responses = [
            python_facts(),
            completed(),
            completed(stdout="0.6.1\n"),
            completed(stdout="\n"),
            completed(),
            completed(stdout="0.6.1\n"),
            completed(),
        ]
        with patch(
            "sima_cli.install.windows_package._run", side_effect=responses
        ) as run:
            result = install_windows_package(
                self.metadata, str(self.root), python_executable=str(self.python)
            )

        self.assertEqual(result["python"], "installed")
        self.assertEqual(
            run.call_args_list[4].args[0][-3:],
            ["--force-reinstall", "--no-deps", str(self.wheel)],
        )

    def test_wheel_modified_after_download_is_not_installed(self):
        responses = [python_facts(), completed(), completed(returncode=3)]

        def replace_wheel_during_msi(command, **kwargs):
            if command[0] == MSIEXEC:
                self.wheel.write_bytes(b"replaced wheel")
            return responses.pop(0)

        with patch(
            "sima_cli.install.windows_package._run", side_effect=replace_wheel_during_msi
        ) as run:
            with self.assertRaisesRegex(WindowsPythonInstallError, "declared SHA-256"):
                install_windows_package(
                    self.metadata, str(self.root), python_executable=str(self.python)
                )

        self.assertEqual(run.call_count, 3)
        self.assertNotIn("pip", [part for call in run.call_args_list for part in call.args[0]])

    def test_missing_python_package_is_installed_and_verified(self):
        responses = [
            python_facts(),
            completed(),
            completed(returncode=3),
            completed(),
            completed(stdout="0.6.1\n"),
            completed(),
        ]
        with patch(
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
        with patch(
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
        with patch(
            "sima_cli.install.windows_package._run", side_effect=responses
        ):
            with self.assertRaisesRegex(WindowsPythonInstallError, "native installation was kept"):
                install_windows_package(
                    self.metadata, str(self.root), python_executable=str(self.python)
                )

    def test_restart_result_stops_before_python(self):
        with patch(
            "sima_cli.install.windows_package._run",
            side_effect=[python_facts(), completed(returncode=3010)],
        ) as run:
            with self.assertRaises(WindowsPackageRestartRequired):
                install_windows_package(
                    self.metadata, str(self.root), python_executable=str(self.python)
                )
        self.assertEqual(run.call_count, 2)

    def install_from_windows_metadata(
        self, fails=False, exit_code=1, download_error=None, **options
    ):
        # install_from_metadata records the receipt state, then reports any
        # install error as "Failed to install" and exits with status 1; a
        # restart-required result exits with the Windows Installer restart code.
        options = options or {"native_only": True}
        output = io.StringIO()
        with redirect_stdout(output), patch.object(
            metadata_installer,
            "_download_and_validate_metadata",
            return_value=(self.metadata, None),
        ), patch.object(metadata_installer, "registry") as registry, patch.object(
            metadata_installer, "print_metadata_summary"
        ), patch.object(
            metadata_installer, "_check_whether_disk_is_big_enough", return_value=True
        ), patch.object(
            metadata_installer, "_is_platform_compatible", return_value=True
        ), patch.object(
            metadata_installer,
            "_download_assets",
            return_value=[str(self.msi)],
            side_effect=download_error,
        ) as self.download_assets, patch.object(
            metadata_installer, "_print_post_install_message"
        ):
            if not fails:
                metadata_installer.install_from_metadata(
                    "metadata", False, str(self.root), **options
                )
            else:
                with self.assertRaises(SystemExit) as raised:
                    metadata_installer.install_from_metadata(
                        "metadata", False, str(self.root), **options
                    )
                self.assertEqual(raised.exception.code, exit_code)
        return registry, output.getvalue()

    def test_windows_install_records_each_outcome_in_the_package_registry(self):
        # A failure records a partial state only when a native package completed
        # before it; otherwise nothing was installed and the receipt says failed.
        def after_native(error):
            error.native_packages_installed = ["core"]
            return error

        outcomes = [
            ({"native": "installed", "python": "installed"}, False, "installed"),
            ({"native": "installed", "python": "already-installed"}, False, "installed"),
            (WindowsNativeInstallError("approval cancelled"), True, "failed"),
            (click.ClickException("not windows"), True, "failed"),
            (after_native(WindowsNativeInstallError("msi failed")), True, "partial-native-failed"),
            (after_native(WindowsPythonInstallError("pip failed")), True, "partial-python-failed"),
            (WindowsPrerequisiteError("missing driver"), True, "prerequisite-failed"),
        ]
        for outcome, fails, state in outcomes:
            with self.subTest(state=state, outcome=outcome), patch.object(
                metadata_installer,
                "install_windows_package",
                **({"side_effect": outcome} if fails else {"return_value": outcome}),
            ) as install:
                registry, output = self.install_from_windows_metadata(fails)

                install.assert_called_once_with(
                    metadata=self.metadata,
                    install_dir=str(self.root),
                    python_executable=None,
                    native_only=True,
                    record_receipt=ANY,
                    check_prerequisites=False,
                    retry_command=None,
                )
                registry.update_state.assert_called_once_with(
                    self.metadata["name"], self.metadata["version"], state
                )
                if fails:
                    self.assertIn(f"Failed to install from metadata URL metadata: {outcome}", output)

    def test_upgrade_replaces_the_old_receipt_so_removing_it_uninstalls_nothing(self):
        # Both releases share the Scheduler MSI; the old receipt must not remove it.
        self.use_scheduler_and_core()
        registry = PackageRegistry(self.root / "registry.json")
        old_metadata = {**self.metadata, "version": "0.6.0"}
        registry.create_entry(self.metadata["name"], "0.6.0", old_metadata, self.root)
        registry.update_fields(self.metadata["name"], "0.6.0", {"native_packages": [
            {"owner": "scheduler", "version": "0.6.1", "product_code": PRODUCT_CODES["simaai"]},
            {"owner": "core", "version": "0.6.0", "product_code": "{33333333-3333-3333-3333-333333333333}"},
        ]})
        registry.update_state(self.metadata["name"], "0.6.0", "installed")
        outcomes = [
            ({"native": "installed"}, "installed"),
            (WindowsSetupError("setup failed"), "setup-failed"),
        ]
        for outcome, state in outcomes:
            with self.subTest(state=state):
                registry.create_entry(self.metadata["name"], "0.6.0", old_metadata, self.root)
                fails = isinstance(outcome, Exception)
                output = io.StringIO()
                with redirect_stdout(output), patch.object(
                    metadata_installer, "registry", registry
                ), patch.object(
                    metadata_installer, "_download_and_validate_metadata",
                    return_value=(self.metadata, None),
                ), patch.object(metadata_installer, "print_metadata_summary"), patch.object(
                    metadata_installer, "_check_whether_disk_is_big_enough", return_value=True
                ), patch.object(
                    metadata_installer, "_is_platform_compatible", return_value=True
                ), patch.object(
                    metadata_installer, "_download_assets", return_value=[str(self.msi)]
                ), patch.object(metadata_installer, "_print_post_install_message"), patch.object(
                    metadata_installer, "install_windows_package",
                    **({"side_effect": outcome} if fails else {"return_value": outcome}),
                ):
                    if fails:
                        with self.assertRaises(SystemExit):
                            metadata_installer.install_from_metadata(
                                "metadata", False, str(self.root), native_only=True
                            )
                    else:
                        metadata_installer.install_from_metadata(
                            "metadata", False, str(self.root), native_only=True
                        )

                receipts = PackageRegistry(self.root / "registry.json")._data
                self.assertEqual(
                    [(entry["version"], entry["state"]) for entry in receipts], [("0.6.1", state)]
                )
                self.assertIn(
                    f"Replaced the registry receipt of {self.metadata['name']} 0.6.0 with 0.6.1.",
                    output.getvalue(),
                )
                with patch(
                    "sima_cli.install.registry.PackageRegistry",
                    return_value=PackageRegistry(self.root / "registry.json"),
                ), patch(
                    "sima_cli.install.windows_package.uninstall_windows_package"
                ) as uninstall:
                    removed = CliRunner().invoke(
                        packages_group,
                        ["remove", self.metadata["name"], "--version", "0.6.0", "--yes"],
                    )
                self.assertEqual(removed.exit_code, 1)
                self.assertIn("not found in registry", removed.output)
                uninstall.assert_not_called()

    def test_failed_upgrade_keeps_the_old_receipt(self):
        for outcome in (WindowsNativeInstallError("msi failed"), WindowsPrerequisiteError("x")):
            with self.subTest(outcome=outcome), patch.object(
                metadata_installer, "install_windows_package", side_effect=outcome
            ):
                registry, _output = self.install_from_windows_metadata(fails=True)
            registry.remove_other_versions.assert_not_called()

    def test_cancelled_first_msi_records_failed_receipt(self):
        with patch(
            "sima_cli.install.windows_package._is_windows_administrator",
            return_value=False,
        ), patch(
            "sima_cli.install.windows_package._interactive_console", return_value=True
        ), patch(
            "sima_cli.install.windows_package._run", return_value=completed(returncode=1223)
        ):
            registry, output = self.install_from_windows_metadata(fails=True)

        self.assertIn("administrator approval was cancelled", output)
        registry.update_state.assert_called_once_with(
            self.metadata["name"], self.metadata["version"], "failed"
        )

    def test_interrupted_install_records_a_terminal_receipt(self):
        # Ctrl+C reaches the installer as KeyboardInterrupt, which is not an
        # Exception; the receipt must still leave "installing" before it propagates.
        self.use_scheduler_and_core()
        for completed_msis, state in ((0, "failed"), (1, "partial-native-failed")):
            responses = [completed()] * completed_msis
            used = {}

            def interrupt(command, **_kwargs):
                used["registry"] = metadata_installer.registry
                if responses:
                    return responses.pop(0)
                raise KeyboardInterrupt

            with self.subTest(state=state), patch(
                "sima_cli.install.windows_package._run", side_effect=interrupt
            ):
                with self.assertRaises(KeyboardInterrupt):
                    self.install_from_windows_metadata()

                used["registry"].update_state.assert_called_once_with(
                    self.metadata["name"], self.metadata["version"], state
                )

    def test_failed_or_interrupted_download_records_a_terminal_receipt(self):
        for error, raised in (
            (RuntimeError("network down"), SystemExit),
            (KeyboardInterrupt, KeyboardInterrupt),
        ):
            used = {}

            def fail_download(*_args, **_kwargs):
                used["registry"] = metadata_installer.registry
                raise error

            with self.subTest(error=error):
                with self.assertRaises(raised):
                    self.install_from_windows_metadata(download_error=fail_download)

                used["registry"].update_state.assert_called_once_with(
                    self.metadata["name"], self.metadata["version"], "failed"
                )

    def test_prerequisites_are_checked_once_per_install(self):
        self.metadata["installation"]["prerequisites"] = [OPENSSH]
        with patch(
            "sima_cli.install.windows_package.shutil.which", return_value="ssh.exe"
        ) as which, patch("sima_cli.install.windows_package._run", return_value=completed()):
            _registry, output = self.install_from_windows_metadata()

        self.assertEqual(which.call_count, 1)
        self.assertEqual(output.count("Windows host prerequisites verified."), 1)

    def test_missing_wheel_after_msi_records_partial_python_failure(self):
        # The selected Python is checked before download and again before the MSI.
        responses = [python_facts(), python_facts(), completed()]

        def remove_wheel_during_msi(command, **kwargs):
            if command[0] == MSIEXEC:
                self.wheel.unlink()
            return responses.pop(0)

        with patch(
            "sima_cli.install.windows_package._run", side_effect=remove_wheel_during_msi
        ):
            registry, output = self.install_from_windows_metadata(
                fails=True, python_executable=str(self.python)
            )

        self.assertIn("resource is missing", output)
        registry.update_state.assert_called_once_with(
            self.metadata["name"], self.metadata["version"], "partial-python-failed"
        )

    def test_missing_python_and_gstreamer_are_reported_before_download(self):
        # A clean host learns every missing prerequisite in one run, with the exact
        # versions from the metadata, before any package resource is downloaded.
        self.metadata["installation"]["prerequisites"] = [GSTREAMER]
        self.assertTrue(validate_metadata(self.metadata))
        cases = [
            ({"python_executable": None}, [], "compatible Python 3.14 amd64 installation"),
            (
                {"python_executable": str(self.python)},
                [python_facts(arch="ARM64")],
                "Python architecture arm64 was found; this package requires amd64.",
            ),
        ]
        for options, responses, python_problem in cases:
            with self.subTest(options=options), patch.dict(
                os.environ, {"TEST_GSTREAMER": str(self.root / "missing")}
            ), patch(
                "sima_cli.install.windows_package.Path.home", return_value=self.root / "home"
            ), patch(
                "sima_cli.install.windows_package._python_bootstrap_candidates", return_value=[]
            ), patch(
                "sima_cli.install.windows_package._run", side_effect=responses
            ) as run:
                registry, output = self.install_from_windows_metadata(fails=True, **options)

                self.download_assets.assert_not_called()
                self.assertEqual(run.call_count, len(responses))
                self.assertIn(
                    "- GStreamer 1.28.6 MSVC x64 runtime: found no version, expected 1.28.6. "
                    "Install GStreamer 1.28.6 MSVC x64.",
                    output,
                )
                self.assertIn(python_problem, output)
                self.assertIn(
                    "Install Python 3.14 (amd64) for Windows from "
                    "https://www.python.org/downloads/windows/",
                    output,
                )
                registry.update_state.assert_called_once_with(
                    self.metadata["name"], self.metadata["version"], "prerequisite-failed"
                )

    def test_free_threaded_python_is_rejected_before_download_and_msi(self):
        self.use_scheduler_and_core()
        with patch(
            "sima_cli.install.windows_package._run",
            return_value=python_facts(tag="cp314-cp314t-win_amd64", wheel_supported=False),
        ) as run:
            registry, output = self.install_from_windows_metadata(
                fails=True, python_executable=str(self.python)
            )

        self.download_assets.assert_not_called()
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.args[0][-1], self.wheel.name)
        self.assertIn(
            "Python 3.14 amd64 (cp314-cp314t-win_amd64) cannot install "
            "pyneatpcie-0.6.1-cp314-cp314-win_amd64.whl",
            output,
        )
        self.assertIn("No MSI was started.", output)
        registry.update_state.assert_called_once_with(
            self.metadata["name"], self.metadata["version"], "prerequisite-failed"
        )

    def test_python_accepting_the_wheel_tags_installs_both_msis_and_the_wheel(self):
        self.use_scheduler_and_core()
        responses = [
            python_facts(),  # preflight before download
            python_facts(),  # check again before the MSIs
            completed(),
            completed(),
            completed(returncode=3),
            completed(),
            completed(stdout="0.6.1\n"),
            completed(),
        ]
        with patch(
            "sima_cli.install.windows_package._run", side_effect=responses
        ) as run:
            registry, _output = self.install_from_windows_metadata(
                python_executable=str(self.python)
            )

        commands = [entry.args[0] for entry in run.call_args_list]
        for command in commands[:2]:
            self.assertEqual(command[:2], [str(self.python), "-c"])
            self.assertEqual(command[-1], self.wheel.name)
        self.assertEqual(
            [command[2] for command in commands[2:4]], [str(self.scheduler_msi), str(self.msi)]
        )
        self.assertEqual(commands[5][1:4], ["-m", "pip", "install"])
        registry.update_state.assert_called_once_with(
            self.metadata["name"], self.metadata["version"], "installed"
        )

    def test_invalid_managed_python_environment_is_reported_before_download(self):
        (self.root / "home" / "pyneatpcie").mkdir(parents=True)
        with patch(
            "sima_cli.install.windows_package.Path.home", return_value=self.root / "home"
        ), patch("sima_cli.install.windows_package._run") as run:
            registry, output = self.install_from_windows_metadata(fails=True, native_only=False)

        self.download_assets.assert_not_called()
        run.assert_not_called()
        self.assertIn("Managed Python path exists but is not a valid environment", output)
        registry.update_state.assert_called_once_with(
            self.metadata["name"], self.metadata["version"], "prerequisite-failed"
        )

    def test_restart_required_is_not_reported_as_failure(self):
        self.use_scheduler_and_core()
        with patch(
            "sima_cli.install.windows_package._run", return_value=completed(returncode=3010)
        ) as run:
            registry, output = self.install_from_windows_metadata(fails=True, exit_code=3010)

        self.assertEqual(run.call_count, 1)
        self.assertNotIn("Failed to install", output)
        self.assertIn("Restart required", output)
        self.assertIn("Native packages installed in this run: scheduler.", output)
        self.assertIn("Not yet run: native packages core", output)
        self.assertIn("rerun the same command", output)
        registry.update_state.assert_called_once_with(
            self.metadata["name"], self.metadata["version"], "restart-required"
        )

    def test_repair_that_needs_a_restart_is_reported_like_an_install(self):
        # A repair that has to replace a file in use ends with 3010, as an install does.
        self.use_scheduler_and_core()
        with patch(
            "sima_cli.install.windows_package._msi_product_installed", return_value=True
        ), patch(
            "sima_cli.install.windows_package._run", return_value=completed(returncode=3010)
        ) as run:
            registry, output = self.install_from_windows_metadata(fails=True, exit_code=3010)

        self.assertEqual(run.call_args.args[0][1], "/fomus")
        self.assertIn("Restart required", output)
        self.assertIn("Not yet run: native packages core", output)
        registry.update_state.assert_called_once_with(
            self.metadata["name"], self.metadata["version"], "restart-required"
        )

    def test_restart_required_prints_the_command_pinned_to_the_release(self):
        self.use_scheduler_and_core()
        with patch(
            "sima_cli.install.windows_package._run", return_value=completed(returncode=3010)
        ):
            _registry, output = self.install_from_windows_metadata(
                fails=True, exit_code=3010, native_only=True, retry_command=PINNED_RETRY
            )

        self.assertIn(
            f"To continue the coordinated installation, restart Windows, then rerun this "
            f"exact release: {PINNED_RETRY}",
            output,
        )
        self.assertNotIn("same command", output)

    def test_partial_install_failures_print_the_command_pinned_to_the_release(self):
        self.use_scheduler_and_core()
        cases = [
            (
                "core MSI fails after Scheduler",
                {"native_only": True},
                [completed(), completed(returncode=1603)],
                "Packages completed earlier in this run: scheduler. The release remains "
                "incomplete. Installer log: {log}. Correct the failure, then rerun this "
                "exact release: {retry}",
            ),
            (
                "pip fails after both MSIs",
                {"python_executable": str(self.python)},
                [python_facts(), python_facts(), completed(), completed(),
                 completed(returncode=3), completed(returncode=1)],
                "The native installation was kept; to retry the Python step with the same "
                "environment, rerun this exact release: {retry}",
            ),
        ]
        for case, options, responses, message in cases:
            with self.subTest(case), patch(
                "sima_cli.install.windows_package._run", side_effect=responses
            ):
                _registry, output = self.install_from_windows_metadata(
                    fails=True, retry_command=PINNED_RETRY, **options
                )

                log = self.root / "sima-cli-msi-install-02-core.log"
                self.assertIn(message.format(log=log, retry=PINNED_RETRY), output)
                self.assertNotIn("same command", output)

    def assert_windows_option_rejected(self, option, **options):
        metadata = {"name": "linux-package", "version": "1", "installation": {"type": "script"}}
        with patch.object(
            metadata_installer,
            "_download_and_validate_metadata",
            return_value=(metadata, None),
        ), patch.object(metadata_installer, "registry"), patch.object(
            metadata_installer, "_download_assets"
        ) as download:
            with self.assertRaisesRegex(click.UsageError, f"{option} applies only to Windows"):
                metadata_installer.install_from_metadata(
                    "metadata", False, str(self.root), **options
                )
        download.assert_not_called()

    def test_python_executable_is_rejected_for_non_windows_package(self):
        self.assert_windows_option_rejected(
            "--python-executable", python_executable=str(self.python)
        )

    def test_native_only_is_rejected_for_non_windows_package(self):
        self.assert_windows_option_rejected("--native-only", native_only=True)

    def test_python_options_are_rejected_for_components_without_metadata(self):
        cases = [
            ("cr:sima/tool", ["--native-only"], "--native-only"),
            ("ghcr:sima/tool", ["--python-executable", str(self.python)], "--python-executable"),
            ("hostdriver", ["--native-only"], "--native-only"),
            ("OptiView", ["--python-executable", str(self.python)], "--python-executable"),
            ("palette", ["--native-only"], "--native-only"),
        ]
        for component, options, option in cases:
            with self.subTest(component=component), patch(
                "sima_cli.cli.install_from_cr"
            ) as from_cr, patch("sima_cli.cli.install_hostdriver") as hostdriver, patch(
                "sima_cli.cli.install_optiview"
            ) as optiview, patch("sima_cli.cli.resolve_version", return_value="3.0.0"):
                result = CliRunner().invoke(
                    main, ["install", component, *options],
                    env={"SIMA_CLI_CHECK_FOR_UPDATE": "0"},
                )

                self.assertEqual(result.exit_code, 2, result.output)
                self.assertIn(f"{option} applies only to Windows MSI packages", result.output)
                for installer in (from_cr, hostdriver, optiview):
                    installer.assert_not_called()

    def test_component_install_reports_completion_once(self):
        with patch(
            "sima_cli.cli.metadata_resolver", return_value="https://example.invalid/metadata.json"
        ), patch("sima_cli.cli.install_from_metadata", return_value={"native": "installed"}):
            result = CliRunner().invoke(
                main, ["install", "pciehost/windows", "-v", "3.0.0"],
                env={"SIMA_CLI_CHECK_FOR_UPDATE": "0"},
            )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(result.output.count("Installation complete."), 1)

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

    def test_vulcan_install_forwards_selected_python_and_native_only(self):
        resolved = type(
            "Result",
            (),
            {
                "environment": "dev",
                "base_url": "https://example.invalid",
                "repository": "core",
                "package_path": "pciehost/windows/amd64",
                "ref": "v0.6.1",
                "ref_key": "v0.6.1",
                "requested_spec": "latest",
                "resolved_spec": "a" * 12,
                "metadata_url": "https://example.invalid/metadata.json",
            },
        )()
        pinned = "sima-cli neat install core/pciehost/windows/amd64@v0.6.1:" + "a" * 12
        cases = [
            ({"python_executable": str(self.python)}, f'--python-executable "{self.python}"'),
            ({"native_only": True}, "--native-only"),
        ]
        for options, option_text in cases:
            with self.subTest(options=options), patch(
                "sima_cli.vulcan.commands.resolve_install_metadata_url", return_value=resolved
            ), patch("sima_cli.vulcan.commands.install_from_metadata") as install:
                install_vulcan_package(
                    target="core/pciehost/windows/amd64@v0.6.1", environment="dev", **options
                )

                forwarded = install.call_args.kwargs
                self.assertEqual(
                    {key: forwarded.get(key) for key in ("python_executable", "native_only")},
                    {"python_executable": None, "native_only": None, **options},
                )
                self.assertEqual(
                    forwarded["retry_command"], f"{pinned} --env dev {option_text}"
                )


if __name__ == "__main__":
    unittest.main()
