"""Static contract for the Windows bootstrap installer, which only runs on Windows."""

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/install/windows.bat"
WORKFLOW = ROOT / ".github/workflows/vulcan-ci.yml"


class WindowsInstallerScriptTests(unittest.TestCase):
    def setUp(self):
        self.script = SCRIPT.read_text(encoding="utf-8")

    def test_installs_pinned_64_bit_python_314(self):
        urls = re.findall(r"https://www\.python\.org/ftp/python/\S+?\.exe", self.script)
        self.assertEqual(len(urls), 1)
        self.assertRegex(urls[0], r"/ftp/python/3\.14\.(\d+)/python-3\.14\.\1-amd64\.exe$")

    def test_store_alias_or_broken_python_counts_as_missing(self):
        self.assertNotIn("where python", self.script)
        self.assertIn('python -c "import sys" >nul 2>nul', self.script)

    def test_python_installer_runs_only_after_signature_check(self):
        check = self.script.index("Get-AuthenticodeSignature -LiteralPath $env:PYTHON_INSTALLER")
        run = self.script.index('start "" /wait "!PYTHON_INSTALLER!"')
        self.assertLess(check, run)
        self.assertIn("$s.Status -ne 'Valid'", self.script)
        self.assertIn("GetNameInfo('SimpleName', $false) -ne 'Python Software Foundation'", self.script)
        failure = self.script[check:run]
        self.assertIn("exit /b 1", failure)

    def test_python_installs_for_all_users_only_when_elevated(self):
        self.assertIn("InstallAllUsers=!PYTHON_ALL_USERS! InstallLauncherAllUsers=!PYTHON_ALL_USERS!", self.script)
        elevated, standard = self.script.split('if "%IS_ADMIN%"=="1" (', 1)[1].split(") else (", 1)
        self.assertIn('set "PYTHON_ALL_USERS=1"', elevated)
        self.assertIn('set "PYTHON_ALL_USERS=0"', standard.split(")", 1)[0])

    def test_reports_python_other_than_64_bit_314(self):
        self.assertIn("sys.version_info[:2] == (3, 14) and struct.calcsize('P') == 8", self.script)
        self.assertIn("needs 64-bit Python 3.14 from https://www.python.org/downloads/windows/", self.script)

    def test_persists_environment_to_user_path_only(self):
        self.assertNotIn("setx", self.script.lower())
        self.assertNotIn("/M", self.script)
        self.assertIn("[Microsoft.Win32.Registry]::CurrentUser.CreateSubKey('Environment')", self.script)
        self.assertIn("'DoNotExpandEnvironmentNames'", self.script)
        machine = self.script.split("[Microsoft.Win32.Registry]::LocalMachine", 1)[1]
        self.assertIn("Where-Object { -not (Same $_) }", machine)
        self.assertEqual(self.script.count("$key.SetValue('Path', ($kept -join ';')"), 1)

    def test_messages_survive_delayed_expansion(self):
        # Under enabledelayedexpansion a "!" that does not close a !variable! is dropped
        # with the text up to the next one, so messages must not contain a lone "!".
        self.assertIn("setlocal enabledelayedexpansion", self.script)
        for line in self.script.splitlines():
            if line.strip().lower().startswith("echo"):
                text = re.sub(r"![A-Za-z_][A-Za-z0-9_]*!", "", line)
                self.assertNotIn("!", text, line)
        self.assertIn("echo Done. Open a new Command Prompt or PowerShell and type:", self.script)

    def test_ci_runs_script_twice_and_checks_path_scope(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        step = workflow.split("- name: Test repo installer script on native Windows\n", 1)[1]
        step = step.split("\n      - name:", 1)[0]
        self.assertIn("foreach ($run in 1, 2)", step)
        self.assertIn("cmd.exe /c scripts\\install\\windows.bat", step)
        self.assertIn('GetEnvironmentVariable("Path", "User")', step)
        self.assertIn('GetEnvironmentVariable("Path", "Machine")', step)


if __name__ == "__main__":
    unittest.main()
