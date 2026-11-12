# Install Neat PCIe Host on Windows

Install the Windows PCIe host release with one `sima-cli` command. The release
manifest identifies the matching Scheduler and Core MSI packages, their install
order, versions, and checksums. `sima-cli` downloads and verifies the complete
set before passing each native package to Windows Installer.

Run the command from PowerShell after installing the supported Platform PCIe
package and x64 GStreamer MSVC runtime. The Platform package owns the signed
driver, host libraries, and `SiMaMlaDaemon` service; Neat does not replace or
update them.

```powershell
sima-cli neat install core/pciehost/windows/amd64@3.0 `
  --python-executable .\.venv\Scripts\python.exe
```

Before starting either MSI, `sima-cli` checks the Windows OpenSSH tools, the
running Platform service, GStreamer, and—when requested—the selected Python
version, architecture, and pip. It stops without changing the native install if
a prerequisite is missing. `--python-executable` is optional. When supplied,
`sima-cli` asks that exact application environment to install the matching
`pyneatpcie` wheel with pip, then checks the installed version and import. Omit
the option for a C++-only or native-only installation.

After Windows Installer completes, the CLI runs the installed host doctor and
syncs the package-matched `neat-pcie-application-builder` skill into the current
user's Codex and Claude skill directories. On the first installation, provision
passwordless SSH to the card:

```powershell
& "C:\Program Files\SimaPCIeHost\bin\pcie-setup.cmd"
```

The setup command discovers card addresses from local `10.0.N.1` management
links. Use `-Hosts 10.0.0.2` when discovery is unavailable. OpenSSH prompts for
the card password only when its public key is not already enrolled; the command
does not store the password. Its wrapper applies `ExecutionPolicy Bypass` only
to that installed script process so the unsigned first-release utility works
under Windows PowerShell's default local policy. It does not change the user's
or machine's execution policy, and a policy enforced by an administrator still
takes precedence.

Repeating the command repairs or upgrades the native package set. A matching,
working Python package is verified and left in place. If a native package or pip
step fails, correct the reported problem and run the same command again; the
installer reports any native packages that completed before the failure.
Windows Installer error `1625` means the package was rejected by the host's
application-control policy. Use an approved signed release or ask the Windows
administrator to review that policy; do not disable Windows security controls.
If Windows Installer cannot be started, `sima-cli` reports the attempted package,
permissions guidance, and the intended installer-log location.

Remove Scheduler and Core through Windows Installed apps or `msiexec`. Remove
the Python package separately from its application environment:

```powershell
.\.venv\Scripts\python.exe -m pip uninstall pyneatpcie
```
