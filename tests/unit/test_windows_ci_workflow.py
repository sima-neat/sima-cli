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


if __name__ == "__main__":
    unittest.main()
