"""Static contract for the Windows bootstrap installer, which only runs on Windows."""

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/install/windows.bat"


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


if __name__ == "__main__":
    unittest.main()
