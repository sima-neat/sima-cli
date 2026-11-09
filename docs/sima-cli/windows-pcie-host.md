# Install Neat PCIe Host on Windows

Install the Windows PCIe host release with one `sima-cli` command. The release
manifest identifies the matching Scheduler and Core MSI packages, their install
order, versions, and checksums. `sima-cli` downloads and verifies the complete
set before passing each native package to Windows Installer.

Run the command from PowerShell after installing the supported Platform PCIe
driver and service:

```powershell
sima-cli neat install core/pciehost/windows/amd64@3.0 `
  --python-executable .\.venv\Scripts\python.exe
```

`--python-executable` is optional. When supplied, `sima-cli` asks that exact
Python environment to install the matching `pyneatpcie` wheel with pip, then
checks the installed version and import. Omit the option for a C++-only or
native-only installation.

Repeating the command repairs or upgrades the native package set. A matching,
working Python package is verified and left in place. If a native package or pip
step fails, correct the reported problem and run the same command again; the
installer reports any native packages that completed before the failure.

Remove Scheduler and Core through Windows Installed apps or `msiexec`. Remove
the Python package separately from its application environment:

```powershell
.\.venv\Scripts\python.exe -m pip uninstall pyneatpcie
```
