"""Identity of the Windows installer (MSI) and its WinGet package.

This is the one place to change these names. packaging/windows reads this file for the MSI
and the WinGet manifests, and the CLI uses it for its update text, so they cannot disagree.
Keep it free of imports: the packaging scripts load it without installing sima-cli.
"""

# Public and permanent once the package is in winget-pkgs.
WINGET_PACKAGE_IDENTIFIER = "SiMa-ai.sima-cli"
# The Apps list (ARP) entry and the WinGet defaultLocale manifest must show the same two names.
PUBLISHER = "SiMa.ai"
PRODUCT_NAME = "sima-cli"
# Fixed forever: Windows Installer upgrades and WinGet match installs by it.
UPGRADE_CODE = "{5D2C50B6-8A8A-48BF-BC19-42AE7FE26DBC}"
# The MSI writes this into sima-cli's .dist-info/INSTALLER (PEP 627).
INSTALLER_MARKER = "sima-cli-msi"
# The newest MSI, for PCs where WinGet does not offer the new version yet.
LATEST_MSI_URL = "https://artifacts.neat.sima.ai/sima-cli/windows/sima-cli-x64.msi"
