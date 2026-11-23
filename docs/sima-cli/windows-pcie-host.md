# Install Neat PCIe Host on Windows

Install the Windows PCIe host release with one `sima-cli` command. The release
manifest identifies the matching Scheduler and Core MSI packages, their install
order, versions, and checksums. `sima-cli` downloads and verifies the complete
set before passing each native package to Windows Installer.

This page covers only the `sima-cli` command. For prerequisites, card
enrollment, verification, and recovery, follow
[Install PCIe Host](/getting-started/neat-library/pcie-host).

Run the command from PowerShell after installing the supported Platform PCIe
package and x64 GStreamer MSVC runtime. From a PowerShell window that is not
elevated, you approve one Windows administrator prompt for the machine-wide
Scheduler and Core MSI packages: one elevated step installs Scheduler and then
Core, and does not start Core if Scheduler fails. Only that step is elevated;
the CLI, managed Python environment, and customer skills remain in the current
user's session and profile. Run the command from an interactive PowerShell window so Windows can
present that approval prompt; a window opened with Run as administrator needs
no prompt.

```powershell
sima-cli neat install core/pciehost/windows/amd64@v<Neat version>
```

Replace `<Neat version>` with a Neat release that publishes the Windows host
packages; it is the same release tag as the Ubuntu host packages use.

Before starting either MSI, `sima-cli` checks the Windows OpenSSH tools, the
running Platform service, GStreamer, and a compatible Python version,
architecture, pip, and wheel tags (a free-threaded Python does not accept the
wheel). It stops without changing the native install if a prerequisite is
missing. By default, `sima-cli` creates or reuses
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
package path.

Repeating the command runs the same checks and passes each MSI to Windows
Installer again in the same order. The Python package is left in place only when
it was installed from this release's wheel and still imports; otherwise
`sima-cli` reinstalls the wheel. If a native package or pip step fails, correct
the reported problem; if Windows Installer requests a restart, the command exits
with code `3010` and you restart Windows. Then run the retry command that
`sima-cli` prints: it names the exact release this run resolved, so the retry
cannot pick up a different one. The installer reports any native packages that
completed before the failure.
Windows Installer error `1625` means the package was rejected by the host's
application-control policy. The Neat MSI packages and the DLLs and Python
extension module in the first release are not code-signed. `sima-cli` downloads
them over HTTPS from the release channel, verifies each package's SHA-256
checksum after download and again before starting Windows Installer or pip
(from a window that is not elevated, both before the approval prompt and inside
the elevated step), and stops if a package no longer matches. A corporate application-control policy may block
unsigned binaries; ask the Windows administrator to review that policy.
`sima-cli` does not disable any Windows security control.
If Windows Installer cannot be started, `sima-cli` reports the attempted package,
permissions guidance, and the intended installer-log location.

When it installs the release, `sima-cli` records each MSI's product code in its
registry receipt. One command uses those codes to uninstall Core and then
Scheduler in one elevated step, under one administrator prompt, and then removes
the receipt:

```powershell
sima-cli packages list
sima-cli packages remove gh:sima-neat/core/pciehost/windows/amd64 --version INSTALLED_VERSION --yes
```

Replace `INSTALLED_VERSION` with the exact value shown by `packages list`. A
package that is already absent counts as removed. If Windows Installer fails,
Scheduler is not removed while Core remains, the receipt is kept with the state
`remove-failed`, and the same command can be run again. Exit code `3010` means
both packages were removed and Windows must restart to finish.

The command does not delete the current user's Python package, managed Python
environment, or synced customer skill; it prints the commands that remove them:

```powershell
& "$env:USERPROFILE\pyneatpcie\Scripts\python.exe" -m pip uninstall -y pyneatpcie
sima-cli playbooks remove neat-pcie-application-builder --yes
```
