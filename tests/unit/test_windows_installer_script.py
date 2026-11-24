"""Static contract for the Windows bootstrap installer, which only runs on Windows."""

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/install/windows.bat"
WORKFLOW = ROOT / ".github/workflows/vulcan-ci.yml"
RUN_INSTALLER = '"!PYTHON_INSTALLER!" /quiet'


def exits_not_last_in_statement(script):
    """Return each exit /b line that has another command after it in the same statement.

    Run as cmd /c script.bat, an exit /b followed by any command in its ( ... ) block, at any
    nesting level and even a rem, or after & on its line, ends the script with exit code 0;
    ERRORLEVEL is still set, so call script.bat sees the code but cmd /c does not.
    """
    lines = [line.strip() for line in script.splitlines()]
    depths, depth = [], 0
    for text in lines:
        if text.startswith(")"):
            depth -= 1
        depths.append(depth)
        if text.endswith("("):
            depth += 1
    assert depth == 0, depth
    found = []
    for index, text in enumerate(lines):
        match = re.search(r"\bexit\s+/b\b(.*)$", text, re.IGNORECASE)
        if not match or re.match(r"rem\b", text, re.IGNORECASE):
            continue
        if not re.fullmatch(r"\s*[^\s&|()]*\s*", match.group(1)):
            found.append(text)
            continue
        open_blocks = depths[index] + (1 if text.endswith("(") else 0)
        for after in lines[index + 1:]:
            if not open_blocks:
                break
            if after == ")":
                open_blocks -= 1
            elif after:
                found.append(text)
                break
    return found


def powershell(script, name):
    """Return the PowerShell command cmd assembles in variable name, with the pieces it uses."""
    text = "".join(re.findall(rf'^\s*set "{name}=(?:!{name}!)?(.*)"$', script, re.MULTILINE))
    return re.sub(r"!(PS_[A-Z]+)!", lambda match: powershell(script, match.group(1)), text)


class WindowsInstallerScriptTests(unittest.TestCase):
    LOCATION_PROBE = (
        'for %%P in (%PYTHON_LOCATIONS%) do if not defined PYTHON '
        '( "%%~P" -c "import sys" >nul 2>nul && set "PYTHON=%%~P" )'
    )

    def setUp(self):
        self.script = SCRIPT.read_text(encoding="utf-8")

    def test_installs_pinned_64_bit_python_314(self):
        urls = re.findall(r"https://www\.python\.org/ftp/python/\S+?\.exe", self.script)
        self.assertEqual(len(urls), 1)
        self.assertRegex(urls[0], r"/ftp/python/3\.14\.(\d+)/python-3\.14\.\1-amd64\.exe$")

    def test_store_alias_or_broken_python_counts_as_missing(self):
        self.assertNotIn("where python", self.script)
        self.assertIn('python -c "import sys" >nul 2>nul', self.script)

    def test_existing_python_is_found_before_installing(self):
        # The python.org installer leaves Python off PATH by default, so PATH alone
        # misses the usual per-user install and the script reinstalled over it.
        probes = [
            'python -c "import sys" >nul 2>nul\nif not errorlevel 1 set "PYTHON=python"',
            "('py -3.14 -c \"import sys; print(sys.executable)\" 2^>nul ^<nul') do set \"PYTHON=%%P\"",
            'set PYTHON_LOCATIONS="%LOCALAPPDATA%\\Programs\\Python\\Python314\\python.exe" '
            '"%ProgramFiles%\\Python314\\python.exe"',
            self.LOCATION_PROBE,
        ]
        positions = [self.script.index(probe) for probe in probes]
        self.assertEqual(positions, sorted(positions))
        install = self.script.index("if not defined PYTHON (\n")
        self.assertLess(positions[-1], install)
        self.assertLess(install, self.script.index("Invoke-WebRequest"))

    def test_installed_python_is_accepted_from_either_location(self):
        # The installer can keep an existing 3.14's scope, whatever the elevation.
        run = self.script.index(RUN_INSTALLER)
        after = self.script[run:]
        self.assertIn(self.LOCATION_PROBE, after.split("echo Python installation failed", 1)[0])
        self.assertNotIn('set "PYTHON=%ProgramFiles%', self.script)
        self.assertNotIn('set "PYTHON=%LOCALAPPDATA%', self.script)

    def test_python_installer_runs_only_after_signature_check(self):
        check = self.script.index("Get-AuthenticodeSignature -LiteralPath $env:PYTHON_INSTALLER")
        run = self.script.index(RUN_INSTALLER)
        self.assertLess(check, run)
        self.assertIn("$s.Status -ne 'Valid'", self.script)
        self.assertIn("GetNameInfo('SimpleName', $false) -ne 'Python Software Foundation'", self.script)
        failure = self.script[check:run]
        self.assertIn('set "PYTHON_FAILED=1"', failure)

    def test_python_installer_matches_pinned_sha256_before_it_runs(self):
        url = re.search(r"/ftp/python/[^/]+/(python-[^/\s\"]+\.exe)", self.script).group(1)
        self.assertRegex(self.script, r'set "PYTHON_SHA256=[0-9a-f]{64}"\n')
        check = self.script.index(
            "(Get-FileHash -LiteralPath $env:PYTHON_INSTALLER -Algorithm SHA256).Hash "
            "-ne $env:PYTHON_SHA256) { exit 1 }"
        )
        self.assertLess(check, self.script.index(RUN_INSTALLER))
        self.assertIn(f'set "PYTHON_INSTALLER=!PYTHON_DOWNLOAD!\\{url}"', self.script)

    def test_python_installer_downloads_into_a_directory_this_run_creates(self):
        # Not a predictable %TEMP% file another process could plant or replace.
        self.assertNotIn("%TEMP%\\python-", self.script)
        self.assertIn('set "PYTHON_DOWNLOAD=%TEMP%\\sima-cli-python-%RANDOM%%RANDOM%%RANDOM%"', self.script)
        create = self.script.index('mkdir "!PYTHON_DOWNLOAD!"')
        # A directory that already existed is not this run's, so it is left alone.
        self.assertIn(
            "    if errorlevel 1 (\n"
            "        echo Could not create a new download directory: !PYTHON_DOWNLOAD!\n"
            '        set "PYTHON_FAILED=1"\n',
            self.script[create:],
        )
        self.assertIn('        set "PYTHON_DOWNLOAD="\n', self.script[create:].split("powershell", 1)[0])
        self.assertLess(create, self.script.index("Invoke-WebRequest"))
        self.assertNotIn('del "!PYTHON_INSTALLER!"', self.script)
        self.assertEqual(self.script.count('rmdir /s /q "!PYTHON_DOWNLOAD!"'), 1)
        self.assertIn('set "PYTHON_DOWNLOAD="\nset "PYTHON_FAILED="\nif not defined PYTHON (\n', self.script)
        cleanup = self.script.index('if defined PYTHON_DOWNLOAD rmdir /s /q "!PYTHON_DOWNLOAD!"\n')
        self.assertLess(self.script.index(RUN_INSTALLER), cleanup)

    def test_every_exit_ends_its_statement(self):
        # On Windows 11, cmd /c windows.bat returned 0 after the Python bootstrap refused to
        # continue, because more commands followed each exit /b 1 in that block.
        self.assertEqual(exits_not_last_in_statement(self.script), [])
        self.assertEqual(
            exits_not_last_in_statement("if 1==1 (\n    exit /b 1\n)\nexit /b 1\nif 1==1 exit /b 1\n"), []
        )
        for script in (
            "if 1==1 (\n    if 1==1 (\n        exit /b 1\n    )\n    rem more\n)\n",
            "if 1==1 (\n    exit /b 1\n) else (\n    echo more\n)\n",
            "if 1==1 exit /b 1 & rem more\n",
        ):
            self.assertEqual(len(exits_not_last_in_statement(script)), 1, script)

    def test_failed_python_bootstrap_step_exits_non_zero(self):
        # Each later step is skipped once one fails, and the script then exits at the top level.
        gate = "if not defined PYTHON if not defined PYTHON_FAILED (\n"
        self.assertEqual(self.script.count(gate), 3)
        self.assertEqual(self.script.count('set "PYTHON_FAILED=1"'), 4)
        run = self.script.index(RUN_INSTALLER)
        self.assertLess(self.script.rindex(gate), run)
        self.assertIn(
            '    if not "!PYTHON_INSTALL_EXIT!"=="0" if not "!PYTHON_INSTALL_EXIT!"=="3010" (\n'
            "        echo The Python installer failed with exit code !PYTHON_INSTALL_EXIT!.\n"
            '        set "PYTHON_FAILED=1"\n',
            self.script[run:],
        )
        failed = self.script.index("\nif defined PYTHON_FAILED exit /b 1\n")
        self.assertLess(run, failed)
        self.assertLess(failed, self.script.index("echo Python installation failed"))
        self.assertLess(failed, self.script.index('-m venv --clear "%INSTALL_DIR%"'))

    def test_python_installs_for_all_users_only_when_elevated(self):
        self.assertIn("InstallAllUsers=!PYTHON_ALL_USERS! InstallLauncherAllUsers=!PYTHON_ALL_USERS!", self.script)
        elevated, standard = self.script.split('if "%IS_ADMIN%"=="1" (', 1)[1].split(") else (", 1)
        self.assertIn('set "PYTHON_ALL_USERS=1"', elevated)
        self.assertIn('set "PYTHON_ALL_USERS=0"', standard.split(")", 1)[0])

    def test_reports_python_other_than_64_bit_314(self):
        self.assertIn("sys.version_info[:2] == (3, 14) and struct.calcsize('P') == 8", self.script)
        self.assertIn("needs 64-bit Python 3.14 from https://www.python.org/downloads/windows/", self.script)

    def test_environment_that_does_not_run_is_recreated(self):
        # A leftover environment whose base Python was removed was reused, so pip failed.
        probe = self.script.index('"%INSTALL_DIR%\\Scripts\\python.exe" -c "import sys" >nul 2>nul\nif errorlevel 1 (')
        create = self.script.index('"!PYTHON!" -m venv --clear "%INSTALL_DIR%"')
        self.assertLess(probe, create)
        self.assertNotIn('if not exist "%INSTALL_DIR%"', self.script)
        self.assertIn(
            "    if errorlevel 1 (\n"
            "        echo Could not create the virtual environment in !INSTALL_DIR!.\n"
            "        exit /b 1\n",
            self.script[create:],
        )

    def test_failed_pip_step_exits_non_zero_before_done(self):
        done = self.script.index("echo Done.")
        for step, message in (
            ("python -m pip install --upgrade pip\n",
             "Could not upgrade pip in !INSTALL_DIR!. See the pip output above."),
            ('    python -m pip install --force-reinstall "%WHEEL_PATH%"\n)\n',
             "Could not install sima-cli into !INSTALL_DIR!. See the pip output above."),
        ):
            checked = step + f"if errorlevel 1 (\n    echo {message}\n    exit /b 1\n)\n"
            self.assertIn(checked, self.script)
            self.assertLess(self.script.index(checked), done)

    def test_persists_environment_to_user_path_only(self):
        self.assertNotIn("setx", self.script.lower())
        self.assertNotIn("/M", self.script)
        self.assertIn("[Microsoft.Win32.Registry]::CurrentUser.CreateSubKey('Environment')", self.script)
        self.assertIn("'DoNotExpandEnvironmentNames'", self.script)
        machine = powershell(self.script, "PS_MACHINE")
        self.assertIn("[Microsoft.Win32.Registry]::LocalMachine", machine)
        self.assertIn("Where-Object { -not (Same $_) }", machine)
        self.assertEqual(machine.count("$key.SetValue('Path', ($kept -join ';')"), 1)
        self.assertIn(machine, powershell(self.script, "PS_PATH"))

    def test_repeated_user_path_entries_keep_the_first(self):
        # Earlier installers added the environment again on each run. The first entry stays
        # where it is, so it still wins over later entries, and only its repeats are removed.
        user = powershell(self.script, "PS_PATH").split("[Microsoft.Win32.Registry]::LocalMachine", 1)[0]
        self.assertIn("[Microsoft.Win32.Registry]::CurrentUser", user)
        self.assertIn(
            "foreach ($entry in $raw.Split(';')) { if (Same ([Environment]::ExpandEnvironmentVariables($entry))) "
            "{ $count++; if ($count -gt 1) { continue } }; $kept += $entry }",
            user,
        )
        self.assertIn("} elseif ($count -eq 1) { Write-Host 'Already in the user PATH.' } else {", user)
        self.assertIn("$key.SetValue('Path', ($kept -join ';'), $kind)", user)

    def test_path_update_failure_is_reported(self):
        # Without Stop, a failed .NET call only ends its statement and the exit code
        # comes from the last statement, so the failure message never printed.
        self.assertTrue(powershell(self.script, "PS_PATH").startswith("$ErrorActionPreference = 'Stop'; "))
        run = self.script.index('powershell -NoProfile -ExecutionPolicy Bypass -Command "!PS_PATH!"')
        self.assertEqual(self.script[run:].splitlines()[1], 'set "PATH_RESULT=!errorlevel!"')

    def test_path_step_outcome_is_explicit(self):
        # A failed PATH step printed "Done. ... type: sima-cli" and then exited 1. The install
        # itself succeeded, so the script exits 0 and prints how to run it without the PATH.
        outcome = self.script.split('set "PATH_RESULT=!errorlevel!"\n', 1)[1]
        done, failed = outcome.split(") else (\n", 1)
        self.assertIn('if "!PATH_RESULT!"=="0" (\n    echo Done. Open a new Command Prompt or PowerShell', done)
        self.assertNotIn("Done.", failed)
        self.assertIn("echo Could not update the user PATH. Add !VENV_PATH! to it manually.", failed)
        self.assertIn('    echo     ^& "!VENV_PATH!\\sima-cli.exe"\n)\n', failed)
        self.assertEqual(self.script.splitlines()[-1], "exit /b 0")
        self.assertLess(self.script.rindex("\nendlocal\n"), self.script.rindex("\nexit /b 0"))

    def test_warns_when_a_new_window_would_run_another_sima_cli(self):
        # A new window searches the system PATH, then the user PATH. A sima-cli of another
        # account on the system PATH ran instead of the one just installed, and the script
        # still printed Done.
        run = self.script.index('powershell -NoProfile -ExecutionPolicy Bypass -Command "!PS_PATH!"')
        check = powershell(self.script, "PS_PATH").split("'SIMA_CLI_PATH_REFRESH', $null, 'User')", 1)[1]
        self.assertIn(
            "foreach ($scope in 'system', 'user') { $target = 'Machine'; if ($scope -eq 'user') { $target = 'User' }; "
            "foreach ($entry in ([string][Environment]::GetEnvironmentVariable('Path', $target)).Split(';'))",
            check,
        )
        self.assertIn("foreach ($name in 'sima-cli.exe', 'sima-cli.bat', 'sima-cli.cmd')", check)
        self.assertIn("if ($first -and -not (Same $first[0])) {", check)
        self.assertIn("'WARNING: a new window will run another sima-cli, not the one just installed:'", check)
        self.assertTrue(check.endswith("; exit 2 }"), check[-80:])
        # Warn only: the entry may belong to another account or program.
        self.assertNotIn("SetValue", check)
        outcome = self.script[run:]
        self.assertIn(
            'if not "!PATH_RESULT!"=="2" echo Could not update the user PATH.', outcome.split(") else (\n", 1)[1]
        )
        self.assertNotIn("Done.", outcome.split(") else (\n", 1)[1])

    def test_uninstall_removes_this_environment_and_its_path_entries(self):
        self.assertIn(
            'if /i "%~1"=="uninstall" (\n    set "UNINSTALL=1"\n    set "WHEEL_PATH="\n'
            '    if /i "%~2"=="--force" set "UNINSTALL_FORCE=1"\n)\n',
            self.script,
        )
        # Uninstall needs no Python, so it ends before the Python bootstrap.
        done = self.script.index("\nif defined UNINSTALL exit /b 0\n")
        self.assertLess(done, self.script.index('set "PYTHON="\n'))
        self.assertLess(self.script.index("\nif defined UNINSTALL if defined UNINSTALL_FAILED exit /b 1\n"), done)
        uninstall = self.script[self.script.index('if /i "%~1"=="uninstall"'):done]
        receipts = uninstall.index('-Command "!PS_RECEIPTS!"')
        remove = uninstall.index('rmdir /s /q "!INSTALL_DIR!"')
        path = uninstall.index('-Command "!PS_COMMON!!PS_UNPATH!!PS_MACHINE!!PS_BROADCAST!"')
        self.assertLess(receipts, remove)
        self.assertLess(remove, path)
        unpath = powershell(self.script, "PS_UNPATH")
        self.assertIn("Where-Object { -not (Same ([Environment]::ExpandEnvironmentVariables($_))) }", unpath)
        self.assertIn("$key.SetValue('Path', ($kept -join ';'), $key.GetValueKind('Path'))", unpath)
        # The state folder, with sign-in and package receipts, stays.
        self.assertEqual(self.script.count("rmdir /s /q"), 2)
        self.assertIn("remain in !USERPROFILE!\\.sima-cli; delete that folder to remove them too.", uninstall)

    def test_uninstall_stops_while_windows_installer_packages_are_recorded(self):
        # sima-cli removes those packages through its receipts, so it must still be there.
        receipts = powershell(self.script, "PS_RECEIPTS")
        self.assertIn("$file = $env:USERPROFILE + '\\.sima-cli\\registry.json'", receipts)
        self.assertIn("$msi = @($all | Where-Object { $_.metadata.installation.type -eq 'windows-msi' })", receipts)
        self.assertIn("('    sima-cli packages remove ' + $p.package_name + ' --version ' + $p.version)", receipts)
        self.assertIn("if ($env:UNINSTALL_FORCE) {", receipts)
        self.assertIn("Settings > Apps > Installed apps", receipts)
        self.assertTrue(receipts.endswith("; exit 2"))
        refused = 'if defined UNINSTALL if not defined UNINSTALL_FORCE if "!RECEIPTS!"=="2" (\n'
        block = self.script.split(refused, 1)[1].split("\n)\n", 1)[0]
        self.assertIn("or run %~nx0 uninstall --force to remove sima-cli and leave them installed.", block)
        self.assertIn('set "UNINSTALL_FAILED=1"', block)
        unreadable = (
            'if defined UNINSTALL if not defined UNINSTALL_FORCE if not "!RECEIPTS!"=="0" '
            'if not "!RECEIPTS!"=="2" (\n'
        )
        removal = 'if defined UNINSTALL if not defined UNINSTALL_FAILED if exist "!INSTALL_DIR!\\" (\n'
        self.assertLess(self.script.index(refused), self.script.index(removal))
        self.assertLess(self.script.index(unreadable), self.script.index(removal))

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
        # A broken environment is recreated, and a pip failure fails the script.
        self.assertIn("'^home = .*', 'home = C:\\sima-cli-missing-python'", step)
        self.assertIn("cmd.exe /c scripts\\install\\windows.bat $invalidWheel", step)
        self.assertIn('if ($LASTEXITCODE -eq 0) { throw "windows.bat reported success although pip failed" }', step)
        # A refusal inside the Python bootstrap block fails the script: with no Python to
        # find, a download directory that cannot be created stops it.
        refusal = step.split("# A refused Python bootstrap fails the script", 1)[1]
        self.assertIn('$env:PATH = "$env:SystemRoot\\System32;$env:SystemRoot\\System32\\WindowsPowerShell\\v1.0"', refusal)
        self.assertIn("$env:TEMP = $blocker", refusal)
        self.assertIn('if ($code -eq 0) { throw "windows.bat reported success although it refused to install Python" }', refusal)
        self.assertIn('$env:LOCALAPPDATA = Join-Path $env:RUNNER_TEMP "no-python"\n          $env:ProgramFiles = $env:LOCALAPPDATA', refusal)
        self.assertIn('($output -match "^Could not create a new download directory: ")', refusal)
        # GitHub ends a PowerShell step with the last native exit code.
        self.assertLess(step.index("although pip failed"), step.index("$global:LASTEXITCODE = 0"))
        self.assertLess(refusal.index("although it refused"), refusal.index("$global:LASTEXITCODE = 0"))


if __name__ == "__main__":
    unittest.main()
