"""Keep the native MSI test tied to the built wheel and ahead of publication."""

from pathlib import Path
import unittest


WORKFLOW = Path(__file__).resolve().parents[2] / ".github/workflows/vulcan-ci.yml"


class WindowsCiWorkflowTests(unittest.TestCase):
    def test_msi_fixture_configures_real_x64_compiler_abi(self):
        fixture = WORKFLOW.parents[2] / "tests/e2e/windows_msi_install.py"
        source = fixture.read_text(encoding="utf-8")
        self.assertIn("project(SimaCliMsiE2E VERSION 1.0.0 LANGUAGES C)", source)
        self.assertIn('"-A", "x64"', source)
        self.assertIn('"--work-directory"', source)

    def test_msi_job_consumes_built_wheel_without_source_imports(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        job = workflow.split("\n  windows-msi:\n", 1)[1].split("\n  compatibility-test:\n", 1)[0]
        self.assertIn("needs: build", job)
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
