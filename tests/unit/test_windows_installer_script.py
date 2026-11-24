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


PREFIX = re.compile(
    r"""(?ix)\s+|[@()]|else\b|do\b|call\b|for\b.*?\bin\s*\(.*?\)
    |if\s+(?:/i\s+)?(?:not\s+)?(?:errorlevel\s+\d+|defined\s+\S+|exist\s+(?:"[^"]*"|\S+)
        |(?:"[^"]*"|\S+?)\s*==\s*(?:"[^"]*"|\S+)|\S+\s+(?:equ|neq|lss|leq|gtr|geq)\s+\S+)"""
)


def interpreters_run_by_name(script):
    """Return each line that runs python, py or pip by its bare name, without call, outside a for /f capture.

    cmd looks for a bare name in the current directory, then on PATH, and takes a .bat or .cmd
    as well as an .exe. A batch file started from a batch file without call never returns, so
    the script ends there with that file's exit code. Through call it returns, and the command
    of for /f ('...') runs in a child cmd, which returns.
    """
    found = []
    for line in script.splitlines():
        text = re.sub(r"\('.*?'\)", "()", line)
        statements, current, quoted, escaped = [], "", False, False
        for char in text:
            if escaped or char == "^":
                escaped = not escaped
            elif char == '"':
                quoted = not quoted
            elif char in "&|" and not quoted:
                statements.append(current)
                current = ""
                continue
            current += char
        for statement in statements + [current]:
            called = False
            while True:
                prefix = PREFIX.match(statement)
                if not prefix or not prefix.group(0):
                    break
                called = called or prefix.group(0).lower() == "call"
                statement = statement[prefix.end():]
            command = re.match(r'"([^"]*)"|\S*', statement)
            name = command.group(1) if command.group(1) is not None else command.group(0)
            if not called and re.fullmatch(r"(?i)(python3?|py|pip3?)(\.exe|\.bat|\.cmd)?", name):
                found.append(line.strip())
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

    VERIFIED_CAPTURE = 'do ( "%%P" -c "import sys" >nul 2>nul && set "PYTHON=%%P" )'
    PATH_PROBE = (
        "for /f \"delims=\" %%P in ('python -c \"import sys; print(sys.executable)\" 2^>nul ^<nul') "
        + VERIFIED_CAPTURE
    )

    ENVIRONMENT_PROBE = (
        '"%INSTALL_DIR%\\Scripts\\python.exe" -c "import sys" >nul 2>nul\n'
        'if not errorlevel 1 set "PYTHON=%INSTALL_DIR%\\Scripts\\python.exe"\n'
    )

    def setUp(self):
        self.script = SCRIPT.read_text(encoding="utf-8")

    def test_installs_pinned_64_bit_python_314(self):
        urls = re.findall(r"https://www\.python\.org/ftp/python/\S+?\.exe", self.script)
        self.assertEqual(len(urls), 1)
        self.assertRegex(urls[0], r"/ftp/python/3\.14\.(\d+)/python-3\.14\.\1-amd64\.exe$")

    def test_store_alias_or_broken_python_counts_as_missing(self):
        self.assertNotIn("where python", self.script)
        self.assertIn(self.PATH_PROBE, self.script)

    def test_python_on_path_runs_in_a_child_cmd_and_then_by_full_path(self):
        # A python.bat or python.cmd, such as pyenv-win's shim or one in the current folder, ended
        # the script at its first Python probe with exit code 0, and nothing was installed.
        self.assertEqual(interpreters_run_by_name(self.script), [])
        self.assertIn('call "%%PYTHON%%" -m venv --clear "%%INSTALL_DIR%%"', self.script)
        # On a runner: a pyenv-win style python.bat first on PATH, and no environment yet.
        step = WORKFLOW.read_text(encoding="utf-8").split("- name: Test repo installer script on native Windows\n", 1)[1]
        shim = step.split("# A python that is a batch file, as pyenv-win's shim first on PATH", 1)[1]
        shim = shim.split("# A failed pip step fails the script.", 1)[0]
        self.assertIn('Set-Content -LiteralPath (Join-Path $pathShims "python.bat") -Value "@`"', shim)
        self.assertIn('Remove-Item -LiteralPath (Join-Path $env:USERPROFILE ".sima-cli-env") -Recurse -Force', shim)
        self.assertIn('$env:PATH = "$pathShims;$env:PATH"\n', shim)
        self.assertIn("Invoke-Installer $wheel\n            $env:PATH = $savedPath\n", shim)
        self.assertIn('Assert-Output "Creating virtual environment in $env:USERPROFILE\\.sima-cli-env..."', shim)
        self.assertIn("if ($LASTEXITCODE -ne 0 -or $version -cne $expected)", shim)
        self.assertEqual(
            interpreters_run_by_name(
                'python -c "import sys" >nul 2>nul\nif 1==1 (\n    py -3.14 -m venv x\n)\n'
                "if 1==1 pip install x\nif not defined PYTHON python -V\nset \"X=1\" & \"python.exe\" -V\n"
            ),
            ['python -c "import sys" >nul 2>nul', "py -3.14 -m venv x", "if 1==1 pip install x",
             "if not defined PYTHON python -V", 'set "X=1" & "python.exe" -V'],
        )
        self.assertEqual(
            interpreters_run_by_name(
                "rem python\necho Python 3.14 needs python.exe\n\"!PYTHON!\" -m pip install x\n"
                "for /f %%P in ('python -c \"print(1)\"') do set \"P=%%P\"\n"
                '"%VENV_PATH%\\python.exe" -m pip install --upgrade pip\nrem python -V\n'
                'echo Run it: ^& python -V\nif "%X%"=="1" (\n    "!PYTHON!" -m venv x\n) else (\n    echo pip\n)\n'
                'call python -V\nif not defined PYTHON (\n    call python -c "import sys" >nul 2>nul <nul\n)\n'
            ),
            [],
        )

    def test_existing_python_is_found_before_installing(self):
        # The python.org installer leaves Python off PATH by default, so PATH alone
        # misses the usual per-user install and the script reinstalled over it.
        probes = [
            self.ENVIRONMENT_PROBE,
            self.PATH_PROBE,
            "('py -3.14 -c \"import sys; print(sys.executable)\" 2^>nul ^<nul') " + self.VERIFIED_CAPTURE,
            'set PYTHON_LOCATIONS="%LOCALAPPDATA%\\Programs\\Python\\Python314\\python.exe" '
            '"%ProgramFiles%\\Python314\\python.exe"',
            self.LOCATION_PROBE,
        ]
        positions = [self.script.index(probe) for probe in probes]
        self.assertEqual(positions, sorted(positions))
        install = self.script.index("if not defined PYTHON (\n")
        self.assertLess(positions[-1], install)
        self.assertLess(install, self.script.index("Invoke-WebRequest"))

    def test_printed_interpreter_path_is_used_only_if_it_runs(self):
        # Python prints its path in the ANSI code page and cmd reads it in the console code page, so a
        # path with, for example, an accented user name came back changed and the venv step failed.
        captures = [line for line in self.script.splitlines() if "print(sys.executable)" in line]
        self.assertEqual(len(captures), 2)
        for line in captures:
            self.assertTrue(line.endswith(" " + self.VERIFIED_CAPTURE), line)
        # Last, before the download, a python on PATH is used by name, through call.
        fallback = (
            'if not defined PYTHON (\n    call python -c "import sys" >nul 2>nul <nul\n'
            '    if not errorlevel 1 set "PYTHON=python"\n)\n'
        )
        self.assertLess(self.script.index(self.LOCATION_PROBE), self.script.index(fallback))
        self.assertLess(self.script.index(fallback), self.script.index('set "PYTHON_URL='))
        # So the interpreter, a full path or that name, always runs through call.
        self.assertNotRegex(self.script, r'(?m)^\s*"[!%]PYTHON[!%]"')
        self.assertIn('    call "%%PYTHON%%" -m venv --clear "%%INSTALL_DIR%%"\n', self.script)
        self.assertIn("\n    call \"%%PYTHON%%\" -c \"import struct, sys;", self.script)
        # On a runner: a python first on PATH whose path has a character the console code page lacks.
        step = WORKFLOW.read_text(encoding="utf-8").split("- name: Test repo installer script on native Windows\n", 1)[1]
        case = step.split("# A python on PATH whose path has a character", 1)[1].split("# A failed pip step", 1)[0]
        self.assertIn("[Console]::OutputEncoding.CodePage", case)
        self.assertIn("(Get-Culture).TextInfo.ANSICodePage", case)
        self.assertIn("(Get-Culture).TextInfo.OEMCodePage", case)
        self.assertIn('$accented = Join-Path $env:RUNNER_TEMP "Jos$([char]0xE9)\\python"', case)
        self.assertIn('Remove-Item -LiteralPath (Join-Path $env:USERPROFILE ".sima-cli-env") -Recurse -Force', case)
        self.assertIn(
            '$env:PATH = "$accented\\Scripts;$env:SystemRoot\\System32;$env:SystemRoot\\System32\\WindowsPowerShell\\v1.0"',
            case,
        )
        self.assertIn('Assert-Output "Creating virtual environment in $env:USERPROFILE\\.sima-cli-env..."', case)
        self.assertIn('Assert-Output "Done. Open a new Command Prompt or PowerShell and type:"', case)
        self.assertIn("if ($LASTEXITCODE -ne 0 -or $version -cne $expected)", case)

    def test_call_expands_variables_only_when_it_parses_the_line_again(self):
        # call parses its line a second time: a path expanded before it lost a % and had each ^
        # doubled, so a profile or Python path with either character broke the venv step.
        calls = [
            line for line in self.script.splitlines()
            if re.search(r"(?i)(^|[\s(&])call\s", line) and not re.match(r"(?i)\s*rem\b", line)
        ]
        self.assertEqual(len(calls), 3, calls)
        for line in calls:
            self.assertNotRegex(line, r"(?<!%)%[A-Za-z_]\w*%(?!%)|![A-Za-z_]\w*!", line)
        # On a runner: a python whose path has a caret, a percent sign and a space, and no environment yet.
        step = WORKFLOW.read_text(encoding="utf-8").split("- name: Test repo installer script on native Windows\n", 1)[1]
        case = step.split("# A python whose path has a caret, a percent sign and a space", 1)[1]
        case = case.split("# A failed pip step", 1)[0]
        self.assertIn('$special = Join-Path $env:RUNNER_TEMP "caret^ and 100% sign\\python"', case)
        self.assertIn('Remove-Item -LiteralPath (Join-Path $env:USERPROFILE ".sima-cli-env") -Recurse -Force', case)
        self.assertIn(
            '$env:PATH = "$special\\Scripts;$env:SystemRoot\\System32;$env:SystemRoot\\System32\\WindowsPowerShell\\v1.0"',
            case,
        )
        self.assertIn("Invoke-Installer $wheel\n", case)
        self.assertIn("if ($installerCode -ne 0) {", case)
        self.assertIn('Assert-Output "Creating virtual environment in $env:USERPROFILE\\.sima-cli-env..."', case)
        self.assertIn('Assert-Output "Done. Open a new Command Prompt or PowerShell and type:"', case)
        self.assertIn("if ($LASTEXITCODE -ne 0 -or $version -cne $expected)", case)

    def test_working_environment_is_used_before_looking_for_another_python(self):
        # An upgrade on a PC without python on PATH downloaded and installed Python 3.14, which the
        # working environment did not need, before the script found that environment.
        environment = self.script.index(self.ENVIRONMENT_PROBE)
        self.assertLess(self.script.index('\nset "PYTHON="\n'), environment)
        self.assertIn(self.ENVIRONMENT_PROBE + "if not defined PYTHON " + self.PATH_PROBE + "\n", self.script)
        self.assertLess(environment, self.script.index("Invoke-WebRequest"))

    def test_installed_python_is_accepted_from_either_location(self):
        # The installer can keep an existing 3.14's scope, whatever the elevation.
        run = self.script.index(RUN_INSTALLER)
        after = self.script[run:]
        self.assertIn(self.LOCATION_PROBE, after.split("echo Python installation failed", 1)[0])
        self.assertNotIn('set "PYTHON=%ProgramFiles%', self.script)
        self.assertNotIn('set "PYTHON=%LOCALAPPDATA%', self.script)

    def test_python_installer_downloads_without_a_progress_bar(self):
        # Windows PowerShell 5.1 slows a large Invoke-WebRequest download while it draws progress.
        self.assertIn(
            "-Command \"$ProgressPreference = 'SilentlyContinue'; "
            'Invoke-WebRequest -Uri $env:PYTHON_URL -OutFile $env:PYTHON_INSTALLER"\n',
            self.script,
        )

    def test_python_installer_runs_only_after_signature_check(self):
        check = self.script.index("Get-AuthenticodeSignature -LiteralPath $env:PYTHON_INSTALLER")
        run = self.script.index(RUN_INSTALLER)
        self.assertLess(check, run)
        self.assertIn("$s.Status -ne 'Valid'", self.script)
        self.assertIn("GetNameInfo('SimpleName', $false) -ne 'Python Software Foundation'", self.script)
        failure = self.script[check:run]
        self.assertIn('set "PYTHON_FAILED=1"', failure)

    def test_windows_powershell_does_not_inherit_the_powershell_7_module_path(self):
        # Started from PowerShell 7.3 or later, cmd passes PowerShell 7's PSModulePath on, Windows
        # PowerShell cannot load Get-AuthenticodeSignature, and a genuine Python installer was refused.
        clear = self.script.index('\nset "PSModulePath="\n')
        self.assertLess(self.script.index("\nsetlocal enabledelayedexpansion\n"), clear)
        self.assertLess(clear, self.script.index('"%POWERSHELL%"'))
        # The hosted check runs the script's lines before its arguments, so the variable is cleared there.
        self.assertLess(clear, self.script.index('\nset "WHEEL_PATH=%~1"\n'))

    def test_ci_runs_the_python_installer_check_as_the_script_runs_it(self):
        # The bootstrap never runs on a runner that has Python, so its download and check run alone,
        # from pwsh through cmd, as on a customer PC whose terminal is PowerShell 7.
        workflow = WORKFLOW.read_text(encoding="utf-8")
        name = "- name: Test the repo installer script's Python installer check on native Windows\n"
        step = workflow.split(name, 1)[1].split("\n      - name:", 1)[0]
        self.assertIn("        shell: pwsh\n", step)
        for pattern in (
            '^set "WHEEL_PATH=',
            '^\\s*set "PYTHON_URL=',
            '^\\s*set "PYTHON_SHA256=',
            "Invoke-WebRequest -Uri",
            "Get-AuthenticodeSignature -LiteralPath",
        ):
            self.assertIn(f"Get-Line '{pattern}'", step)
            self.assertEqual(len(re.findall(pattern, self.script, re.MULTILINE)), 1, pattern)
        self.assertIn("$prepare = $script[0..([Array]::IndexOf($script, (Get-Line '^set \"WHEEL_PATH=')) - 1)]", step)
        self.assertIn("cmd.exe /c $bat | Out-Host", step)
        # python.org is outside this repository, so the download alone is tried twice.
        download = "$code = Invoke-Lines download $downloadLines"
        self.assertEqual(step.count(download), 2)
        self.assertIn(
            download + "\n          # One retry, so a passing python.org or network fault does not fail an unrelated change.\n"
            "          if ($code -ne 0) { Start-Sleep -Seconds 15; " + download + " }\n"
            "          if ($code -ne 0) { throw \"The download line of windows.bat failed",
            step,
        )
        self.assertIn(
            'if ($code -ne 0) { throw "The check in windows.bat refused the pinned Python installer: exit code $code" }',
            step,
        )
        self.assertIn('if ($code -ne 1) { throw "The check in windows.bat returned $code for a modified Python installer" }', step)
        self.assertIn('"set `"PYTHON_SHA256=$hash`""', step)
        # The canary only logs whether the fault occurs on the runner.
        canary = step.split("# Canary, not a test:", 1)[1].split("$global:LASTEXITCODE = 0", 1)[0]
        self.assertIn("Where-Object { $_ -notmatch '^set \"PSModulePath=' }", canary)
        self.assertNotIn("throw", canary.split("Invoke-Lines canary", 1)[1])
        self.assertTrue(step.rstrip().endswith("$global:LASTEXITCODE = 0"))

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
        self.assertLess(failed, self.script.index('-m venv --clear "%%INSTALL_DIR%%"'))

    def test_python_installs_for_all_users_only_when_elevated(self):
        self.assertIn("InstallAllUsers=!PYTHON_ALL_USERS! InstallLauncherAllUsers=!PYTHON_ALL_USERS!", self.script)
        elevated, standard = self.script.split('if "%IS_ADMIN%"=="1" (', 1)[1].split(") else (", 1)
        self.assertIn('set "PYTHON_ALL_USERS=1"', elevated)
        self.assertIn('set "PYTHON_ALL_USERS=0"', standard.split(")", 1)[0])

    def test_reports_python_other_than_64_bit_314(self):
        self.assertIn("sys.version_info[:2] == (3, 14) and struct.calcsize('P') == 8", self.script)
        self.assertIn("needs 64-bit Python 3.14 from https://www.python.org/downloads/windows/", self.script)
        # Only for the Python a new environment is created from: a working environment is the
        # first choice, so after installing 3.14 as told, the note followed the old one on every run.
        create = self.script.split(
            '"%INSTALL_DIR%\\Scripts\\python.exe" -c "import sys" >nul 2>nul\nif errorlevel 1 (\n', 1
        )[1].split("    echo Creating virtual environment in !INSTALL_DIR!...\n", 1)[0]
        self.assertIn("    call \"%%PYTHON%%\" -c \"import struct, sys;", create)
        self.assertIn("    if errorlevel 1 echo Note: the Windows PCIe host package needs 64-bit Python 3.14", create)
        self.assertEqual(self.script.count("needs 64-bit Python 3.14"), 1)
        step = WORKFLOW.read_text(encoding="utf-8").split("- name: Test repo installer script on native Windows\n", 1)[1]
        working = step.split("# Run from a folder with a python.bat and a python.cmd that do nothing", 1)[1]
        working = working.split("# A python on PATH whose path has a character", 1)[0]
        self.assertIn('if ($installerOutput.Contains("needs 64-bit Python 3.14")) { throw', working)

    def test_environment_that_does_not_run_is_recreated(self):
        # A leftover environment whose base Python was removed was reused, so pip failed.
        probe = self.script.index('"%INSTALL_DIR%\\Scripts\\python.exe" -c "import sys" >nul 2>nul\nif errorlevel 1 (')
        create = self.script.index('"%%PYTHON%%" -m venv --clear "%%INSTALL_DIR%%"')
        self.assertLess(probe, create)
        self.assertNotIn('if not exist "%INSTALL_DIR%"', self.script)
        self.assertIn(
            "    if errorlevel 1 (\n"
            "        echo Could not create the virtual environment in !INSTALL_DIR!.\n"
            "        exit /b 1\n",
            self.script[create:],
        )

    def test_created_environment_must_run_before_pip(self):
        # A python that exits 0 without running Python, such as a shim that does nothing, created
        # no environment, and the script failed later with "Could not upgrade pip".
        created = (
            '    call "%%PYTHON%%" -m venv --clear "%%INSTALL_DIR%%"\n'
            '    if not errorlevel 1 "%INSTALL_DIR%\\Scripts\\python.exe" -c "import sys" >nul 2>nul\n'
            "    if errorlevel 1 (\n"
            "        echo Could not create the virtual environment in !INSTALL_DIR!.\n"
            "        exit /b 1\n"
            "    )\n)\n"
        )
        self.assertIn(created, self.script)
        self.assertLess(self.script.index(created), self.script.index("-m pip install --upgrade pip"))

    def test_failed_pip_step_exits_non_zero_before_done(self):
        done = self.script.index("echo Done.")
        for step, message in (
            ('"%VENV_PATH%\\python.exe" -m pip install --upgrade pip\n',
             "Could not upgrade pip in !INSTALL_DIR!. See the pip output above."),
            ('    "%VENV_PATH%\\python.exe" -m pip install --force-reinstall "%WHEEL_PATH%"\n)\n',
             "Could not install sima-cli into !INSTALL_DIR!. See the pip output above."),
        ):
            checked = step + f"if errorlevel 1 (\n    echo {message}\n    exit /b 1\n)\n"
            self.assertIn(checked, self.script)
            self.assertLess(self.script.index(checked), done)

    def test_pip_runs_with_the_environment_python_by_its_path(self):
        # After activate.bat, pip ran through python by name, so a python.bat in the current folder
        # ended the script with exit code 0, and a python.exe there received the install.
        self.assertEqual(interpreters_run_by_name(self.script), [])
        self.assertNotIn('\\activate.bat"', self.script)
        self.assertEqual(self.script.count('"%VENV_PATH%\\python.exe" -m pip install '), 3)
        self.assertLess(self.script.index('\nset "PYTHONHOME="\n'), self.script.index("-m pip install"))
        step = WORKFLOW.read_text(encoding="utf-8").split("- name: Test repo installer script on native Windows\n", 1)[1]
        shim = step.split("# Run from a folder with a python.bat and a python.cmd that do nothing", 1)[1]
        shim = shim.split("# A failed pip step fails the script.", 1)[0]
        self.assertIn('foreach ($name in "python.bat", "python.cmd") { Set-Content', shim)
        self.assertIn('-Value "@exit /b 0" }\n            Push-Location $cwdShims\n            Invoke-Installer $wheel\n', shim)
        self.assertIn('Assert-Output "Done. Open a new Command Prompt or PowerShell and type:"', shim)
        self.assertIn('if ($installerOutput.Contains("No working Python found"))', shim)
        self.assertIn("if ($LASTEXITCODE -ne 0 -or $version -cne $expected)", shim)

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
        run = self.script.index('"%POWERSHELL%" -NoProfile -ExecutionPolicy Bypass -Command "!PS_PATH!"')
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
        run = self.script.index('"%POWERSHELL%" -NoProfile -ExecutionPolicy Bypass -Command "!PS_PATH!"')
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

    def test_system_tools_run_from_the_system_directory(self):
        # cmd looks in the current directory before PATH, so a powershell.exe, net.exe or
        # findstr.exe next to the downloaded windows.bat would otherwise run instead.
        self.assertIn('set "POWERSHELL=%SystemRoot%\\System32\\WindowsPowerShell\\v1.0\\powershell.exe"', self.script)
        self.assertNotRegex(self.script, r"(?im)(^\s*|\|\s*)(powershell|net|findstr)(\.exe)?\s")
        self.assertEqual(self.script.count('"%POWERSHELL%" -NoProfile -ExecutionPolicy Bypass -Command '), 5)
        self.assertIn('"%SystemRoot%\\System32\\net.exe" session >nul 2>&1', self.script)

    def test_does_not_claim_to_change_the_callers_path(self):
        # A PATH set inside setlocal never reaches the window that ran the script, so "Adding ... to
        # current session PATH" printed on every run and changed nothing.
        self.assertNotIn("current session PATH", self.script)
        self.assertNotRegex(self.script, r'(?im)^\s*set "?PATH=')
        self.assertNotIn("echo %PATH%", self.script)

    def test_messages_survive_delayed_expansion(self):
        # Under enabledelayedexpansion a "!" that does not close a !variable! is dropped
        # with the text up to the next one, so messages must not contain a lone "!".
        self.assertIn("setlocal enabledelayedexpansion", self.script)
        for line in self.script.splitlines():
            if line.strip().lower().startswith("echo"):
                text = re.sub(r"![A-Za-z_][A-Za-z0-9_]*!", "", line)
                self.assertNotIn("!", text, line)
        self.assertIn("echo Done. Open a new Command Prompt or PowerShell and type:", self.script)

    def test_ci_runs_script_from_old_installer_state_and_uninstalls(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        step = workflow.split("- name: Test repo installer script on native Windows\n", 1)[1]
        step = step.split("\n      - name:", 1)[0]
        self.assertIn("$installer = (Resolve-Path scripts\\install\\windows.bat).Path", step)
        self.assertIn("cmd.exe /c $installer @args", step)
        # The runner's PATH values are restored however the step ends.
        body, restore = step.split("} finally {", 1)
        self.assertIn("try {", body)
        self.assertIn('$entry[0].SetValue("Path", $entry[1], $entry[2])', restore)
        # Old installers' state: this user's environment repeated in the user PATH and on the
        # system PATH, a pip sima-cli ahead of it in the user PATH, and another account's
        # environment on the system PATH, which must stay.
        self.assertIn('Set-RawPath $userKey "$userBase;$userStub;$scripts;$($scripts.ToUpper())\;$scripts"', body)
        self.assertIn('Set-RawPath $machineKey "$machineBase;$scripts"', body)
        self.assertIn('Assert-Output "Removing 2 repeated $scripts entries from the user PATH..."', body)
        self.assertIn('if ((Get-RawPath $userKey) -cne "$userBase;$userStub;$scripts")', body)
        self.assertIn('if ((Get-RawPath $machineKey) -cne $machineBase)', body)
        self.assertIn('Assert-Output "WARNING: a new window will run another sima-cli, not the one just installed:', body)
        self.assertIn('Set-RawPath $machineKey "$machineStub;$machineBase"', body)
        self.assertIn('if ((Get-RawPath $machineKey) -cne "$machineStub;$machineBase")', body)
        # A broken environment is recreated, and a pip failure fails the script.
        self.assertIn("'^home = .*', 'home = C:\\sima-cli-missing-python'", body)
        # Exactly the pip failure: a wrong path (exit 2) or a failed pip self-upgrade would also be non-zero.
        self.assertIn("Invoke-Installer $invalidWheel\n", body)
        self.assertIn('if ($installerCode -ne 1) { throw "windows.bat returned $installerCode although pip failed" }', body)
        self.assertIn(
            'Assert-Output "Could not install sima-cli into $env:USERPROFILE\\.sima-cli-env. See the pip output above."', body
        )
        # Every run of the installed sima-cli checks its exit code.
        runs = [match.end() for match in re.finditer(r'& "\$scripts\\sima-cli\.exe" --version\)?[^\n]*\n', body)]
        self.assertEqual(len(runs), 6)
        for end in runs:
            self.assertRegex(body[end:].split("\n", 1)[0], r"^\s*if \(\$LASTEXITCODE -ne 0[ )]")
        # A refusal inside the Python bootstrap block fails the script: with no Python to
        # find, a download directory that cannot be created stops it.
        refusal = body.split("# A refused Python bootstrap fails the script", 1)[1]
        self.assertIn('$env:PATH = "$env:SystemRoot\\System32;$env:SystemRoot\\System32\\WindowsPowerShell\\v1.0"', refusal)
        self.assertIn("$env:TEMP = $blocker", refusal)
        self.assertIn('if ($code -eq 0) { throw "windows.bat reported success although it refused to install Python" }', refusal)
        self.assertIn('$env:LOCALAPPDATA = Join-Path $env:RUNNER_TEMP "no-python"\n            $env:ProgramFiles = $env:LOCALAPPDATA', refusal)
        # The environment of the earlier runs would otherwise be its Python.
        self.assertIn("$env:ProgramFiles = $env:LOCALAPPDATA\n            $env:USERPROFILE = $env:LOCALAPPDATA\n", refusal)
        self.assertIn("USERPROFILE = $env:USERPROFILE }", refusal)
        self.assertIn('($output -match "^Could not create a new download directory: ")', refusal)
        # Uninstall stops on an MSI receipt, then removes the environment and PATH entries.
        uninstall = refusal.split("# Uninstall stops while", 1)[1]
        self.assertIn("if ($installerCode -ne 1)", uninstall)
        self.assertIn('if (Test-Path -LiteralPath (Join-Path $env:USERPROFILE ".sima-cli-env"))', uninstall)
        self.assertIn("if ((Get-RawPath $userKey) -cne $userBase)", uninstall)
        self.assertIn("Invoke-Installer uninstall --force", uninstall)
        # GitHub ends a PowerShell step with the last native exit code.
        self.assertLess(step.index("although pip failed"), step.index("$global:LASTEXITCODE = 0"))
        self.assertLess(step.index("} finally {"), step.index("$global:LASTEXITCODE = 0"))

if __name__ == "__main__":
    unittest.main()
