# Install Neat PCIe Host on Windows

Install the Windows PCIe host release with one `sima-cli` command. The release
manifest identifies the matching Scheduler and Core MSI packages, their install
order, versions, and checksums. `sima-cli` downloads and verifies the complete
set before passing each native package to Windows Installer.

Run the command from PowerShell after installing the supported Platform PCIe
package and x64 GStreamer MSVC runtime. The Platform package owns the signed
driver, host libraries, and `SiMaMlaDaemon` service; Neat does not replace or
update them. Windows may request administrator approval for the machine-wide
Scheduler and Core MSI packages. Only those native installers are elevated;
the CLI, managed Python environment, and customer skills remain in the current
user's session and profile. A standard user must run the command from an
interactive PowerShell window so Windows can present that approval prompt.

```powershell
sima-cli neat install core/pciehost/windows/amd64@3.0
```

Before starting either MSI, `sima-cli` checks the Windows OpenSSH tools, the
running Platform service, GStreamer, and a compatible Python version,
architecture, and pip. It stops without changing the native install if a
prerequisite is missing. By default, `sima-cli` creates or reuses
`%USERPROFILE%\pyneatpcie`, installs the matching `pyneatpcie` wheel there, and
checks its version and import. Use `--python-executable <path>` to select a
different application environment or `--native-only` for a C++-only install.

After Windows Installer completes, the CLI runs the installed host doctor and
syncs the package-matched `neat-pcie-application-builder` skill into the current
user's Codex and Claude skill directories. Before either MSI runs, the CLI also
checks that this skill ID is unclaimed or is already registered to the same
installed package path. A skill from another source, or an unregistered skill
directory with the same ID, is left untouched and stops the installation.
Repeating the package install may replace only the skill registered to that same
package path. On the first installation, provision passwordless SSH to the card:

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

Repeating the command runs the same checks and passes each MSI to Windows
Installer again in the same order. The Python package is left in place only when
it was installed from this release's wheel and still imports; otherwise
`sima-cli` reinstalls the wheel. If a native package or pip step fails, correct
the reported problem and run the same command again; the installer reports any
native packages that completed before the failure.
Windows Installer error `1625` means the package was rejected by the host's
application-control policy. The Neat MSI packages and the DLLs and Python
extension module in the first release are not code-signed. `sima-cli` downloads
them over HTTPS from the release channel, verifies each package's SHA-256
checksum after download and again before starting Windows Installer or pip, and
stops if a package no longer matches. A corporate application-control policy may block
unsigned binaries; ask the Windows administrator to review that policy.
`sima-cli` does not disable any Windows security control.
If Windows Installer cannot be started, `sima-cli` reports the attempted package,
permissions guidance, and the intended installer-log location.

Remove Scheduler and Core through Windows Installed apps or `msiexec`. Their
MSIs do not own the current user's Python package, synced customer skill, or
CLI registry receipt. Remove those separately from PowerShell:

```powershell
& "$env:USERPROFILE\pyneatpcie\Scripts\python.exe" -m pip uninstall -y pyneatpcie
sima-cli playbooks remove neat-pcie-application-builder --yes
sima-cli packages list
sima-cli packages remove gh:sima-neat/core/pciehost/windows/amd64 --version INSTALLED_VERSION --yes
```

Replace `INSTALLED_VERSION` with the exact value shown by `packages list`.
The final command removes only the local CLI receipt; it does not uninstall
files and should run after the owning installers above.
