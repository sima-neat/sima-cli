@echo off
setlocal enabledelayedexpansion
title Sima-CLI Installer

rem Run system tools from the system directory: cmd looks in the current directory before PATH.
set "POWERSHELL=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
rem Started from PowerShell 7, Windows PowerShell inherits its module path and cannot load Get-AuthenticodeSignature.
set "PSModulePath="

set "WHEEL_PATH=%~1"
set "UNINSTALL="
set "UNINSTALL_FORCE="
if /i "%~1"=="uninstall" (
    set "UNINSTALL=1"
    set "WHEEL_PATH="
    if /i "%~2"=="--force" set "UNINSTALL_FORCE=1"
)
if defined UNINSTALL if not "%~2"=="" if not defined UNINSTALL_FORCE (
    echo Unknown option: %~2
    echo Usage: %~nx0 uninstall [--force]
    exit /b 2
)
if "%WHEEL_PATH%"=="" (
    set "INSTALL_FROM_PYPI=1"
) else (
    set "INSTALL_FROM_PYPI=0"
)
if "%INSTALL_FROM_PYPI%"=="0" if not exist "%WHEEL_PATH%" (
    echo Wheel not found: %WHEEL_PATH%
    echo Usage: %~nx0 [path\to\sima_cli.whl]
    echo    or: %~nx0 uninstall [--force]
    exit /b 2
)

"%SystemRoot%\System32\net.exe" session >nul 2>&1
if %errorlevel% equ 0 (
    echo Administrator privileges detected.
    set "IS_ADMIN=1"
) else (
    echo Standard user detected.
    set "IS_ADMIN=0"
)

set "INSTALL_DIR=%USERPROFILE%\.sima-cli-env"
set "VENV_PATH=%INSTALL_DIR%\Scripts"
rem PowerShell steps that edit the PATH, for install and uninstall. They contain no exclamation
rem mark, percent sign or double quote, so cmd passes them to PowerShell unchanged.
rem Edit the raw registry value so entries that reference variables stay unexpanded.
rem Stop on the first error so a failure reaches the exit code and the message after it.
set "PS_COMMON=$ErrorActionPreference = 'Stop'; $dir = $env:VENV_PATH.TrimEnd('\'); function Same($entry) { $entry.Trim().TrimEnd('\') -ieq $dir }"
set "PS_COMMON=!PS_COMMON!; $key = [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey('Environment'); $raw = [string]$key.GetValue('Path', '', 'DoNotExpandEnvironmentNames')"
rem Earlier installers added this exact per-user path to the system PATH when elevated; remove only that.
set "PS_MACHINE=; if ($env:IS_ADMIN -eq '1') { $key = [Microsoft.Win32.Registry]::LocalMachine.OpenSubKey('SYSTEM\CurrentControlSet\Control\Session Manager\Environment', $true); $raw = [string]$key.GetValue('Path', '', 'DoNotExpandEnvironmentNames'); $kept = @($raw.Split(';') | Where-Object { -not (Same $_) }); if ($kept.Count -lt $raw.Split(';').Count) { Write-Host ('Removing ' + $env:VENV_PATH + ' from the system PATH...'); $key.SetValue('Path', ($kept -join ';'), $key.GetValueKind('Path')) } }"
rem Setting an unused user variable to null only broadcasts the change, so new windows see the new PATH.
set "PS_BROADCAST=; [Environment]::SetEnvironmentVariable('SIMA_CLI_PATH_REFRESH', $null, 'User')"

rem Uninstall removes this account's environment and its PATH entries and keeps the sima-cli state
rem folder of the profile. sima-cli removes the packages it installed with Windows Installer through
rem the receipts in that folder, so while any is recorded, stop unless --force is given.
set "RECEIPTS=0"
set "UNINSTALL_FAILED="
if defined UNINSTALL (
    set "PS_RECEIPTS=$ErrorActionPreference = 'Stop'; $file = $env:USERPROFILE + '\.sima-cli\registry.json'; if (-not [IO.File]::Exists($file)) { exit 0 }; $all = Get-Content -Raw -Encoding UTF8 -LiteralPath $file | ConvertFrom-Json; $msi = @($all | Where-Object { $_.metadata.installation.type -eq 'windows-msi' }); if ($msi.Count -eq 0) { exit 0 }"
    set "PS_RECEIPTS=!PS_RECEIPTS!; Write-Host 'These packages were installed with Windows Installer and are removed with sima-cli:'; foreach ($p in $msi) { Write-Host ('    sima-cli packages remove ' + $p.package_name + ' --version ' + $p.version) }; if ($env:UNINSTALL_FORCE) { Write-Host 'With --force they stay installed. Remove them later under Settings > Apps > Installed apps.' }; exit 2"
    "%POWERSHELL%" -NoProfile -ExecutionPolicy Bypass -Command "!PS_RECEIPTS!"
    set "RECEIPTS=!errorlevel!"
)
if defined UNINSTALL if not defined UNINSTALL_FORCE if "!RECEIPTS!"=="2" (
    echo sima-cli was not removed. Remove those packages first, then run %~nx0 uninstall again,
    echo or run %~nx0 uninstall --force to remove sima-cli and leave them installed.
    set "UNINSTALL_FAILED=1"
)
if defined UNINSTALL if not defined UNINSTALL_FORCE if not "!RECEIPTS!"=="0" if not "!RECEIPTS!"=="2" (
    echo Could not read the package receipts in !USERPROFILE!\.sima-cli\registry.json, so sima-cli was not removed.
    echo To remove it anyway, run %~nx0 uninstall --force.
    set "UNINSTALL_FAILED=1"
)
if defined UNINSTALL if not defined UNINSTALL_FAILED if exist "!INSTALL_DIR!\" (
    echo Removing !INSTALL_DIR!...
    rmdir /s /q "!INSTALL_DIR!"
    if exist "!INSTALL_DIR!\" (
        echo Could not remove all of !INSTALL_DIR!. Close every window and program that uses sima-cli, then run %~nx0 uninstall again.
        set "UNINSTALL_FAILED=1"
    )
)
if defined UNINSTALL if not defined UNINSTALL_FAILED (
    set "PS_UNPATH=; $kept = @($raw.Split(';') | Where-Object { -not (Same ([Environment]::ExpandEnvironmentVariables($_))) }); if ($kept.Count -lt $raw.Split(';').Count) { Write-Host ('Removing ' + $env:VENV_PATH + ' from the user PATH...'); $key.SetValue('Path', ($kept -join ';'), $key.GetValueKind('Path')) }"
    "%POWERSHELL%" -NoProfile -ExecutionPolicy Bypass -Command "!PS_COMMON!!PS_UNPATH!!PS_MACHINE!!PS_BROADCAST!"
    if errorlevel 1 (
        echo Could not remove !VENV_PATH! from the user PATH. Remove it under Edit environment variables for your account.
        set "UNINSTALL_FAILED=1"
    )
)
if defined UNINSTALL if not defined UNINSTALL_FAILED (
    echo.
    echo sima-cli is removed. Your sign-in, package receipts and settings remain in !USERPROFILE!\.sima-cli; delete that folder to remove them too. Python is not removed.
)
if defined UNINSTALL if defined UNINSTALL_FAILED exit /b 1
if defined UNINSTALL exit /b 0

rem Use the first Python that runs: this account's sima-cli environment, so an upgrade needs no other
rem Python, then python on PATH, the py launcher's 3.14, then the two python.org 3.14 locations,
rem because that installer does not add Python to PATH by default.
rem The Microsoft Store alias, or any python that cannot run, counts as no Python.
rem python and py run in a child cmd and print the interpreter they ran, which is used by its full path
rem from then on: a python.bat or python.cmd, such as a version manager's shim, run directly from this
rem script would end it there with exit code 0.
rem The launcher gets no input, so an offer to install a missing version cannot wait for an answer.
set "PYTHON="
"%INSTALL_DIR%\Scripts\python.exe" -c "import sys" >nul 2>nul
if not errorlevel 1 set "PYTHON=%INSTALL_DIR%\Scripts\python.exe"
if not defined PYTHON for /f "delims=" %%P in ('python -c "import sys; print(sys.executable)" 2^>nul ^<nul') do set "PYTHON=%%P"
if not defined PYTHON for /f "delims=" %%P in ('py -3.14 -c "import sys; print(sys.executable)" 2^>nul ^<nul') do set "PYTHON=%%P"
set PYTHON_LOCATIONS="%LOCALAPPDATA%\Programs\Python\Python314\python.exe" "%ProgramFiles%\Python314\python.exe"
for %%P in (%PYTHON_LOCATIONS%) do if not defined PYTHON ( "%%~P" -c "import sys" >nul 2>nul && set "PYTHON=%%~P" )
rem Under cmd /c, an exit /b followed by any other command in its ( ... ) block, or after & on its line,
rem ends the script with exit code 0. So a failed step records the failure, the steps after it are
rem skipped, and the script exits at the top level.
set "PYTHON_DOWNLOAD="
set "PYTHON_FAILED="
if not defined PYTHON (
    set "PYTHON_URL=https://www.python.org/ftp/python/3.14.10/python-3.14.10-amd64.exe"
    set "PYTHON_SHA256=92480239264744fe0386a1bd8275786fefa126e3129e56a68e22b52c73329965"
    rem A directory this run creates, so no earlier file or link can stand in for the download.
    set "PYTHON_DOWNLOAD=%TEMP%\sima-cli-python-%RANDOM%%RANDOM%%RANDOM%"
    set "PYTHON_INSTALLER=!PYTHON_DOWNLOAD!\python-3.14.10-amd64.exe"
    if "%IS_ADMIN%"=="1" (
        set "PYTHON_ALL_USERS=1"
    ) else (
        set "PYTHON_ALL_USERS=0"
    )
    echo No working Python found. Installing 64-bit Python 3.14.10...
    mkdir "!PYTHON_DOWNLOAD!"
    if errorlevel 1 (
        echo Could not create a new download directory: !PYTHON_DOWNLOAD!
        set "PYTHON_FAILED=1"
        rem Not this run's directory, so it is not removed.
        set "PYTHON_DOWNLOAD="
    )
)
if not defined PYTHON if not defined PYTHON_FAILED (
    "%POWERSHELL%" -NoProfile -ExecutionPolicy Bypass -Command "Invoke-WebRequest -Uri $env:PYTHON_URL -OutFile $env:PYTHON_INSTALLER"
    if errorlevel 1 (
        echo Failed to download !PYTHON_URL!
        set "PYTHON_FAILED=1"
    )
)
if not defined PYTHON if not defined PYTHON_FAILED (
    "%POWERSHELL%" -NoProfile -ExecutionPolicy Bypass -Command "if ((Get-FileHash -LiteralPath $env:PYTHON_INSTALLER -Algorithm SHA256).Hash -ne $env:PYTHON_SHA256) { exit 1 }; $s = Get-AuthenticodeSignature -LiteralPath $env:PYTHON_INSTALLER; if ($s.Status -ne 'Valid' -or $s.SignerCertificate.GetNameInfo('SimpleName', $false) -ne 'Python Software Foundation') { exit 1 }"
    if errorlevel 1 (
        echo The downloaded Python installer does not match the pinned SHA-256 or is not validly signed by the Python Software Foundation. It was not run.
        set "PYTHON_FAILED=1"
    )
)
if not defined PYTHON if not defined PYTHON_FAILED (
    rem A batch file waits for the installer to finish.
    "!PYTHON_INSTALLER!" /quiet InstallAllUsers=!PYTHON_ALL_USERS! InstallLauncherAllUsers=!PYTHON_ALL_USERS! PrependPath=1 Include_test=0
    set "PYTHON_INSTALL_EXIT=!errorlevel!"
    rem 3010 means installed, with a restart pending.
    if not "!PYTHON_INSTALL_EXIT!"=="0" if not "!PYTHON_INSTALL_EXIT!"=="3010" (
        echo The Python installer failed with exit code !PYTHON_INSTALL_EXIT!.
        set "PYTHON_FAILED=1"
    )
)
if defined PYTHON_DOWNLOAD rmdir /s /q "!PYTHON_DOWNLOAD!"
if defined PYTHON_FAILED exit /b 1
rem An existing 3.14 can keep its own scope, so accept either location.
for %%P in (%PYTHON_LOCATIONS%) do if not defined PYTHON ( "%%~P" -c "import sys" >nul 2>nul && set "PYTHON=%%~P" )
if not defined PYTHON (
    echo Python installation failed: no Python 3.14 runs from !PYTHON_LOCATIONS!
    exit /b 1
)
"%PYTHON%" -c "import struct, sys; sys.exit(0 if sys.version_info[:2] == (3, 14) and struct.calcsize('P') == 8 else 1)" >nul 2>nul
if errorlevel 1 echo Note: the Windows PCIe host package needs 64-bit Python 3.14 from https://www.python.org/downloads/windows/

rem Also recreate an environment whose python does not run, such as one whose base Python was removed.
"%INSTALL_DIR%\Scripts\python.exe" -c "import sys" >nul 2>nul
if errorlevel 1 (
    echo Creating virtual environment in !INSTALL_DIR!...
    "!PYTHON!" -m venv --clear "%INSTALL_DIR%"
    if errorlevel 1 (
        echo Could not create the virtual environment in !INSTALL_DIR!.
        exit /b 1
    )
)

if "%INSTALL_FROM_PYPI%"=="1" (
    echo Installing/Upgrading official sima-cli release from PyPI...
) else (
    echo Installing/Upgrading sima-cli from %WHEEL_PATH%...
)
rem Run the environment's python by its path: by name, a python in the current folder would run
rem instead. Clear PYTHONHOME, as activate.bat does, so it cannot point that python elsewhere.
set "PYTHONHOME="
"%VENV_PATH%\python.exe" -m pip install --upgrade pip
if errorlevel 1 (
    echo Could not upgrade pip in !INSTALL_DIR!. See the pip output above.
    exit /b 1
)
if "%INSTALL_FROM_PYPI%"=="1" (
    "%VENV_PATH%\python.exe" -m pip install --force-reinstall --index-url https://pypi.org/simple sima-cli
) else (
    "%VENV_PATH%\python.exe" -m pip install --force-reinstall "%WHEEL_PATH%"
)
if errorlevel 1 (
    echo Could not install sima-cli into !INSTALL_DIR!. See the pip output above.
    exit /b 1
)

rem The environment is per-user, so persist it to the user PATH only, even when elevated.
set "PS_PATH=!PS_COMMON!"
rem Keep the first entry for this environment, where an earlier run put it, and drop its repeats.
set "PS_PATH=!PS_PATH!; $kept = @(); $count = 0; foreach ($entry in $raw.Split(';')) { if (Same ([Environment]::ExpandEnvironmentVariables($entry))) { $count++; if ($count -gt 1) { continue } }; $kept += $entry }"
set "PS_PATH=!PS_PATH!; $kind = 'ExpandString'; if ($null -ne $key.GetValue('Path')) { $kind = $key.GetValueKind('Path') }"
set "PS_PATH=!PS_PATH!; if ($count -eq 0) { Write-Host ('Persisting ' + $env:VENV_PATH + ' to the user PATH...'); $key.SetValue('Path', ($raw.TrimEnd(';') + ';' + $env:VENV_PATH).TrimStart(';'), $kind) } elseif ($count -eq 1) { Write-Host 'Already in the user PATH.' } else { Write-Host ('Removing ' + ($count - 1) + ' repeated ' + $env:VENV_PATH + ' entries from the user PATH...'); $key.SetValue('Path', ($kept -join ';'), $kind) }"
set "PS_PATH=!PS_PATH!!PS_MACHINE!!PS_BROADCAST!"
rem A new window searches the system PATH, then the user PATH, from the registry. When another
rem sima-cli comes first, name it and exit 2. Only warn: the entry may belong to another account
rem or program, so this installer never changes it.
set "PS_PATH=!PS_PATH!; $first = $null; foreach ($scope in 'system', 'user') { $target = 'Machine'; if ($scope -eq 'user') { $target = 'User' }; foreach ($entry in ([string][Environment]::GetEnvironmentVariable('Path', $target)).Split(';')) { $folder = $entry.Trim().Trim([char]34).TrimEnd('\'); foreach ($name in 'sima-cli.exe', 'sima-cli.bat', 'sima-cli.cmd') { if (-not $first -and $folder -and [IO.File]::Exists($folder + '\' + $name)) { $first = @($folder, $name, $scope) } } } }"
set "PS_PATH=!PS_PATH!; if ($first -and -not (Same $first[0])) { Write-Host ''; Write-Host 'WARNING: a new window will run another sima-cli, not the one just installed:'; Write-Host ('    ' + $first[0] + '\' + $first[1]); if ($first[2] -eq 'system') { Write-Host '  It is on the system PATH, which Windows searches before your user PATH.' } else { Write-Host ('  It is on your user PATH, ahead of ' + $dir + '.') }"
set "PS_PATH=!PS_PATH!; if ($first[2] -eq 'system' -and $first[0] -like '*\.sima-cli-env\Scripts') { Write-Host '  It is the sima-cli of another account. Running windows.bat in that account from an administrator window moves it to that account''s own user PATH.' } else { Write-Host ('  Uninstall that copy, or remove ' + $first[0] + ' from the ' + $first[2] + ' PATH if nothing else needs it.') }; exit 2 }"
"%POWERSHELL%" -NoProfile -ExecutionPolicy Bypass -Command "!PS_PATH!"
set "PATH_RESULT=!errorlevel!"

echo.
if "!PATH_RESULT!"=="0" (
    echo Done. Open a new Command Prompt or PowerShell and type:
    echo     sima-cli
    echo to start using the tool.
) else (
    if not "!PATH_RESULT!"=="2" echo Could not update the user PATH. Add !VENV_PATH! to it manually.
    echo sima-cli is installed. Until the PATH is fixed, run it by its full path, in PowerShell:
    echo     ^& "!VENV_PATH!\sima-cli.exe"
)
endlocal
rem sima-cli is installed and runs by its full path even when the PATH step failed or another
rem sima-cli comes first, so exit 0.
exit /b 0
