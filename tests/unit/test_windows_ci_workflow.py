"""Keep the native MSI test tied to the built wheel and ahead of publication."""

from pathlib import Path
import unittest


WORKFLOW = Path(__file__).resolve().parents[2] / ".github/workflows/vulcan-ci.yml"


class WindowsCiWorkflowTests(unittest.TestCase):
    def test_msi_fixture_configures_real_x64_compiler_abi(self):
        fixture = WORKFLOW.parents[2] / "tests/e2e/windows_msi_install.py"
        source = fixture.read_text(encoding="utf-8")
        self.assertIn("project(SimaCliMsiE2E VERSION {version} LANGUAGES C)", source)
        self.assertIn('"-A", "x64"', source)
        self.assertIn('"--work-directory"', source)

    def test_msi_fixture_upgrades_refuses_a_downgrade_and_removes_through_the_cli(self):
        # Upgrade and downgrade were covered only by mocked unit tests, and removal called the
        # internal function instead of the command a customer runs.
        source = (WORKFLOW.parents[2] / "tests/e2e/windows_msi_install.py").read_text(encoding="utf-8")
        self.assertIn('old, new = "1.0.0", "1.1.0"', source)
        self.assertIn('set(CPACK_WIX_UPGRADE_GUID "{upgrade_guid}")', source)
        self.assertIn('Path(sysconfig.get_path("scripts")) / "sima-cli.exe"', source)
        self.assertIn('"install", "-m", f"{channel}/{version}/metadata.json"', source)
        order = [
            "output = install(old)\n",
            "repeated = install_windows_package(metadata[old]",
            "output = install(new)\n",
            "output = install(old, expect=1)\n",
            'sima_cli(home, "packages", "remove", PACKAGE, "--version", new, "--yes")',
        ]
        positions = [source.index(step) for step in order]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("if installed(old) or not installed(new):", source)
        self.assertNotIn("uninstall_windows_package(", source)

    def test_msi_job_consumes_built_wheel_without_source_imports(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        job = workflow.split("\n  windows-msi:\n", 1)[1].split("\n  compatibility-test:\n", 1)[0]
        self.assertIn("needs: build", job)
        # Pinned like the Scheduler's Windows build, so a runner image change cannot
        # silently change the Windows Installer that gates publication.
        self.assertIn("    runs-on: windows-2025\n", job)
        self.assertIn("name: sima-cli-dist", job)
        self.assertIn("python -m pip install $wheels[0].FullName", job)
        self.assertIn("needs.build.outputs.version", job)
        self.assertIn("python -I tests/e2e/windows_msi_install.py", job)
        self.assertNotIn("python -m pip install .", job)

    def test_publication_requires_msi_job(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        publisher = workflow.split("\n  publish-vulcan-artifacts:\n", 1)[1].split("\n  e2e-install:\n", 1)[0]
        needs = publisher.split("    needs:\n", 1)[1].split("    uses:", 1)[0]
        self.assertIn("      - windows-msi\n", needs)

    def windows_installer_job(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        return workflow.split("\n  windows-installer:\n", 1)[1].split("\n  compatibility-test:\n", 1)[0]

    def test_installer_job_builds_from_the_built_wheel_on_windows_2025(self):
        job = self.windows_installer_job()
        self.assertIn("      - build\n      - resolve-vulcan-config\n", job)
        self.assertIn("    runs-on: windows-2025\n", job)
        self.assertIn('python-version: "3.14"', job)
        self.assertIn("name: sima-cli-dist", job)
        # The host Python never gets the wheel, so no other sima-cli is on the runner's PATH.
        self.assertNotIn("pip install $wheels", job)
        self.assertNotIn("pip install .", job)
        self.assertIn("python -m pip install -r packaging/windows/build-requirements.txt", job)
        self.assertIn(
            "python -I packaging/windows/build_msi.py --wheel $wheels[0].FullName "
            "--expected-version $env:EXPECTED_CLI_VERSION", job)
        self.assertIn("EXPECTED_CLI_VERSION: ${{ needs.build.outputs.version }}", job)
        self.assertIn("--reuse-bundle --work-directory \"$out\\build\" --msi-version 0.0.1", job)

    def test_installer_job_runs_the_lifecycle_and_winget_checks_in_order(self):
        job = self.windows_installer_job()
        order = [
            "python -I packaging/windows/build_msi.py --wheel",
            "python -I tests/e2e/windows_installer.py lifecycle",
            "- name: Make WinGet available",
            "python -I packaging/windows/winget_manifest.py",
            "& $env:WINGET validate --manifest",
            "python -I tests/e2e/windows_installer.py winget",
            "name: sima-cli-windows-installer",
        ]
        positions = [job.index(step) for step in order]
        self.assertEqual(positions, sorted(positions))
        # Without WinGet the job fails instead of skipping the WinGet checks.
        self.assertIn("Add-AppxPackage -RegisterByFamilyName -MainPackage Microsoft.DesktopAppInstaller_8wekyb3d8bbwe", job)
        self.assertIn("Repair-WinGetPackageManager -AllUsers", job)
        self.assertIn('if (-not $winget) { throw "WinGet is not available', job)
        self.assertIn("ARTIFACT_BASE_URL: ${{ needs.resolve-vulcan-config.outputs.artifact_base_url }}", job)
        self.assertIn("if: always()", job.split("- name: Retain installer logs", 1)[1])

    def test_installer_job_does_not_gate_wheel_publication_yet(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        publisher = workflow.split("\n  publish-vulcan-artifacts:\n", 1)[1].split("\n  e2e-install:\n", 1)[0]
        self.assertNotIn("windows-installer", publisher)

    def test_installer_lifecycle_upgrades_refuses_a_downgrade_and_removes_cleanly(self):
        source = (WORKFLOW.parents[2] / "tests/e2e/windows_installer.py").read_text(encoding="utf-8")
        lifecycle = source.split("def lifecycle(args)", 1)[1].split("\ndef ", 1)[0]
        order = [
            'msiexec("/i", old_msi, root / "install-older.log")',
            "check_new_window(root, new[\"version\"], stub / \"sima-cli.cmd\")",
            "check_runtime()",
            'msiexec("/i", new_msi, root / "install-candidate.log")',
            'msiexec("/i", old_msi, root / "downgrade.log", expect=MSI_DOWNGRADE_REFUSED)',
            'msiexec("/x", new["product_code"], root / "uninstall.log")',
            "if (value, kind) != (path_before, kind_before):",
            "check_state()",
        ]
        positions = [lifecycle.index(step) for step in order]
        self.assertEqual(positions, sorted(positions))
        # A new window's PATH comes from the registry, and the MSI's copy must win over the stub.
        self.assertIn('run([system_tool("where.exe"), "sima-cli"]', source)
        self.assertIn('(stub / "sima-cli.cmd").write_text', source)
        self.assertIn('run([python, "-c", "import pip"], expect=None)', source)
        # Isolated runtime: the test must not depend on PYTHON* variables.
        self.assertNotIn("PYTHONIOENCODING", source)
        self.assertNotIn("import sima_cli", source)


if __name__ == "__main__":
    unittest.main()
