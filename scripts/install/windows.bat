@echo off
setlocal enabledelayedexpansion
title Sima-CLI Installer

set "WHEEL_PATH=%~1"
if "%WHEEL_PATH%"=="" (
    set "INSTALL_FROM_PYPI=1"
) else (
    set "INSTALL_FROM_PYPI=0"
)
if "%INSTALL_FROM_PYPI%"=="0" if not exist "%WHEEL_PATH%" (
    echo Wheel not found: %WHEEL_PATH%
    echo Usage: %~nx0 [path\to\sima_cli.whl]
    exit /b 2
)

net session >nul 2>&1
if %errorlevel% equ 0 (
    echo Administrator privileges detected.
    set "IS_ADMIN=1"
) else (
    echo Standard user detected.
    set "IS_ADMIN=0"
)

rem Use the first Python that runs: python on PATH, the py launcher's 3.14, then the two
rem python.org 3.14 locations, because that installer does not add Python to PATH by default.
rem The Microsoft Store alias, or any python that cannot run, counts as no Python.
rem The launcher gets no input, so an offer to install a missing version cannot wait for an answer.
set "PYTHON="
python -c "import sys" >nul 2>nul
if not errorlevel 1 set "PYTHON=python"
if not defined PYTHON for /f "delims=" %%P in ('py -3.14 -c "import sys; print(sys.executable)" 2^>nul ^<nul') do set "PYTHON=%%P"
set PYTHON_LOCATIONS="%LOCALAPPDATA%\Programs\Python\Python314\python.exe" "%ProgramFiles%\Python314\python.exe"
for %%P in (%PYTHON_LOCATIONS%) do if not defined PYTHON ( "%%~P" -c "import sys" >nul 2>nul && set "PYTHON=%%~P" )
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
        exit /b 1
    )
    powershell -NoProfile -ExecutionPolicy Bypass -Command "Invoke-WebRequest -Uri $env:PYTHON_URL -OutFile $env:PYTHON_INSTALLER"
    if errorlevel 1 (
        echo Failed to download !PYTHON_URL!
        rmdir /s /q "!PYTHON_DOWNLOAD!"
        exit /b 1
    )
    powershell -NoProfile -ExecutionPolicy Bypass -Command "if ((Get-FileHash -LiteralPath $env:PYTHON_INSTALLER -Algorithm SHA256).Hash -ne $env:PYTHON_SHA256) { exit 1 }; $s = Get-AuthenticodeSignature -LiteralPath $env:PYTHON_INSTALLER; if ($s.Status -ne 'Valid' -or $s.SignerCertificate.GetNameInfo('SimpleName', $false) -ne 'Python Software Foundation') { exit 1 }"
    if errorlevel 1 (
        echo The downloaded Python installer does not match the pinned SHA-256 or is not validly signed by the Python Software Foundation. It was not run.
        rmdir /s /q "!PYTHON_DOWNLOAD!"
        exit /b 1
    )
    start "" /wait "!PYTHON_INSTALLER!" /quiet InstallAllUsers=!PYTHON_ALL_USERS! InstallLauncherAllUsers=!PYTHON_ALL_USERS! PrependPath=1 Include_test=0
    rmdir /s /q "!PYTHON_DOWNLOAD!"
    rem An existing 3.14 can keep its own scope, so accept either location.
    for %%P in (%PYTHON_LOCATIONS%) do if not defined PYTHON ( "%%~P" -c "import sys" >nul 2>nul && set "PYTHON=%%~P" )
    if not defined PYTHON (
        echo Python installation failed: no Python 3.14 runs from !PYTHON_LOCATIONS!
        exit /b 1
    )
)
"%PYTHON%" -c "import struct, sys; sys.exit(0 if sys.version_info[:2] == (3, 14) and struct.calcsize('P') == 8 else 1)" >nul 2>nul
if errorlevel 1 echo Note: the Windows PCIe host package needs 64-bit Python 3.14 from https://www.python.org/downloads/windows/

set "INSTALL_DIR=%USERPROFILE%\.sima-cli-env"
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
call "%INSTALL_DIR%\Scripts\activate.bat"
python -m pip install --upgrade pip
if errorlevel 1 (
    echo Could not upgrade pip in !INSTALL_DIR!. See the pip output above.
    exit /b 1
)
if "%INSTALL_FROM_PYPI%"=="1" (
    python -m pip install --force-reinstall --index-url https://pypi.org/simple sima-cli
) else (
    python -m pip install --force-reinstall "%WHEEL_PATH%"
)
if errorlevel 1 (
    echo Could not install sima-cli into !INSTALL_DIR!. See the pip output above.
    exit /b 1
)

set "VENV_PATH=%INSTALL_DIR%\Scripts"
echo %PATH% | findstr /I /C:"%VENV_PATH%" >nul
if errorlevel 1 (
    echo Adding %VENV_PATH% to current session PATH...
    set "PATH=%PATH%;%VENV_PATH%"
)

rem The environment is per-user, so persist it to the user PATH only, even when elevated.
rem Edit the raw registry value so entries that reference variables stay unexpanded.
rem Stop on the first error so a failure reaches the exit code and the message below.
set "PS_PATH=$ErrorActionPreference = 'Stop'; $dir = $env:VENV_PATH.TrimEnd('\'); function Same($entry) { $entry.Trim().TrimEnd('\') -ieq $dir }"
set "PS_PATH=!PS_PATH!; $key = [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey('Environment'); $raw = [string]$key.GetValue('Path', '', 'DoNotExpandEnvironmentNames')"
set "PS_PATH=!PS_PATH!; if ($raw.Split(';') | Where-Object { Same ([Environment]::ExpandEnvironmentVariables($_)) }) { Write-Host 'Already in the user PATH.' } else { $kind = 'ExpandString'; if ($null -ne $key.GetValue('Path')) { $kind = $key.GetValueKind('Path') }; Write-Host ('Persisting ' + $env:VENV_PATH + ' to the user PATH...'); $key.SetValue('Path', ($raw.TrimEnd(';') + ';' + $env:VENV_PATH).TrimStart(';'), $kind) }"
rem Earlier installers added this exact per-user path to the system PATH when elevated; remove only that.
set "PS_PATH=!PS_PATH!; if ($env:IS_ADMIN -eq '1') { $key = [Microsoft.Win32.Registry]::LocalMachine.OpenSubKey('SYSTEM\CurrentControlSet\Control\Session Manager\Environment', $true); $raw = [string]$key.GetValue('Path', '', 'DoNotExpandEnvironmentNames'); $kept = @($raw.Split(';') | Where-Object { -not (Same $_) }); if ($kept.Count -lt $raw.Split(';').Count) { Write-Host ('Removing ' + $env:VENV_PATH + ' from the system PATH...'); $key.SetValue('Path', ($kept -join ';'), $key.GetValueKind('Path')) } }"
rem Setting an unused user variable to null only broadcasts the change, so new windows see the new PATH.
set "PS_PATH=!PS_PATH!; [Environment]::SetEnvironmentVariable('SIMA_CLI_PATH_REFRESH', $null, 'User')"
powershell -NoProfile -ExecutionPolicy Bypass -Command "!PS_PATH!"
if errorlevel 1 echo Could not update the user PATH. Add %VENV_PATH% to it manually.

echo.
echo Done. Open a new Command Prompt or PowerShell and type:
echo     sima-cli
echo to start using the tool.
endlocal
