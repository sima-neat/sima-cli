"""The WinGet manifest generator writes the three files winget-pkgs requires, offline-checked.

The checks follow the 1.12.0 JSON schemas' required fields and patterns, without fetching them.
"""

import datetime
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

import yaml

from sima_cli.utils import windows_installer


ROOT = Path(__file__).resolve().parents[2]
PACKAGING = ROOT / "packaging/windows"
PRODUCT_CODE = "{0B7F3C2A-1D4E-4F56-8A9B-C0D1E2F3A4B5}"
SHA256 = "ab" * 32
URL = "https://artifacts.neat.sima.ai/sima-cli/windows/2.1.24/sima-cli-2.1.24-x64.msi"
# From the schemas: 2 to 8 segments of 1 to 32 characters, none of them a dot, space or \/:*?"<>|.
IDENTIFIER = r'^[^.\s\\/:*?"<>|\x01-\x1f]{1,32}(\.[^.\s\\/:*?"<>|\x01-\x1f]{1,32}){1,7}$'
REQUIRED = {
    "version": {"PackageIdentifier", "PackageVersion", "DefaultLocale", "ManifestType", "ManifestVersion"},
    "installer": {"PackageIdentifier", "PackageVersion", "Installers", "ManifestType", "ManifestVersion"},
    "defaultLocale": {"PackageIdentifier", "PackageVersion", "PackageLocale", "Publisher", "PackageName",
                      "License", "ShortDescription", "ManifestType", "ManifestVersion"},
}


def load_generator():
    spec = importlib.util.spec_from_file_location("winget_manifest", PACKAGING / "winget_manifest.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class WingetManifestTests(unittest.TestCase):
    def setUp(self):
        self.generator = load_generator()
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.installer_json = self.root / "installer.json"
        self.write_installer_json()

    def tearDown(self):
        self.directory.cleanup()

    def write_installer_json(self, **changes):
        record = {
            "version": "2.1.24", "msi_version": "2.1.24", "release": True,
            "file": "sima-cli-2.1.24-x64.msi", "sha256": SHA256, "product_code": PRODUCT_CODE,
            "upgrade_code": windows_installer.UPGRADE_CODE, "python": "3.14.10",
        }
        record.update(changes)
        self.installer_json.write_text(json.dumps(record), encoding="utf-8")

    def generate(self, url=URL):
        paths = self.generator.write_manifests(self.installer_json, url, self.root / "winget", "2026-12-01")
        return {path.name: yaml.safe_load(path.read_text(encoding="utf-8")) for path in paths}

    def test_writes_the_three_files_winget_pkgs_requires(self):
        identifier = windows_installer.WINGET_PACKAGE_IDENTIFIER
        manifests = self.generate()
        self.assertEqual(set(manifests), {
            f"{identifier}.yaml", f"{identifier}.installer.yaml", f"{identifier}.locale.en-US.yaml",
        })
        by_type = {manifest["ManifestType"]: manifest for manifest in manifests.values()}
        self.assertEqual(set(by_type), set(REQUIRED))
        for manifest_type, fields in REQUIRED.items():
            with self.subTest(manifest_type=manifest_type):
                manifest = by_type[manifest_type]
                self.assertLessEqual(fields, set(manifest))
                self.assertEqual(manifest["PackageIdentifier"], identifier)
                self.assertEqual(manifest["PackageVersion"], "2.1.24")
                self.assertEqual(manifest["ManifestVersion"], "1.12.0")
                self.assertTrue(all(value not in (None, "", []) for value in manifest.values()))
        self.assertEqual(by_type["version"]["DefaultLocale"], by_type["defaultLocale"]["PackageLocale"])

    def test_identifier_fits_the_schema_and_names_the_files(self):
        identifier = windows_installer.WINGET_PACKAGE_IDENTIFIER
        self.assertRegex(identifier, IDENTIFIER)
        self.assertLessEqual(len(identifier), 128)
        for name in self.generate():
            self.assertTrue(name.startswith(identifier + "."), name)

    def test_installer_entry_is_the_built_per_user_msi(self):
        installer = self.generate()[f"{windows_installer.WINGET_PACKAGE_IDENTIFIER}.installer.yaml"]
        self.assertEqual(installer["InstallerType"], "wix")
        self.assertEqual(installer["Scope"], "user")
        self.assertLessEqual(set(installer["InstallModes"]), {"interactive", "silent", "silentWithProgress"})
        self.assertEqual(installer["UpgradeBehavior"], "install")
        self.assertEqual(installer["Commands"], ["sima-cli"])
        self.assertEqual(installer["InstallerSwitches"], {"InstallLocation": 'INSTALLFOLDER="<INSTALLPATH>"'})
        self.assertEqual(str(installer["ReleaseDate"]), "2026-12-01")
        [entry] = installer["Installers"]
        self.assertEqual(entry["Architecture"], "x64")
        self.assertEqual(entry["InstallerUrl"], URL)
        self.assertRegex(entry["InstallerSha256"], r"^[A-Fa-f0-9]{64}$")
        self.assertEqual(entry["InstallerSha256"].lower(), SHA256)
        self.assertEqual(entry["ProductCode"], PRODUCT_CODE)
        self.assertEqual(entry["AppsAndFeaturesEntries"], [
            {"ProductCode": PRODUCT_CODE, "UpgradeCode": windows_installer.UPGRADE_CODE},
        ])

    def test_locale_names_match_the_apps_list_entry_of_the_msi(self):
        # WinGet correlates the installed MSI by these; sima-cli.wxs takes them from the same constants.
        locale = self.generate()[f"{windows_installer.WINGET_PACKAGE_IDENTIFIER}.locale.en-US.yaml"]
        self.assertEqual(locale["Publisher"], windows_installer.PUBLISHER)
        self.assertEqual(locale["PackageName"], windows_installer.PRODUCT_NAME)
        self.assertEqual(locale["License"], "MIT")
        self.assertLessEqual(len(locale["ShortDescription"]), 256)
        for field in ("PublisherUrl", "PublisherSupportUrl", "PackageUrl", "LicenseUrl"):
            self.assertRegex(locale[field], r"^https://")
        for tag in locale["Tags"]:
            self.assertRegex(tag, r"^\S{1,40}$")
        wxs = (PACKAGING / "sima-cli.wxs").read_text(encoding="utf-8")
        self.assertIn('Name="$(var.ProductName)"', wxs)
        self.assertIn('Manufacturer="$(var.Publisher)"', wxs)

    def test_package_version_is_the_msi_product_version_of_a_branch_build(self):
        self.write_installer_json(version="2.1.24+feat.windows.0123456789ab", msi_version="2.1.24", release=False)
        manifests = self.generate()
        self.assertEqual({manifest["PackageVersion"] for manifest in manifests.values()}, {"2.1.24"})

    def test_local_test_url_is_accepted_and_malformed_inputs_are_refused(self):
        installer = self.generate("http://127.0.0.1:8080/sima-cli-2.1.24-x64.msi")[
            f"{windows_installer.WINGET_PACKAGE_IDENTIFIER}.installer.yaml"]
        self.assertEqual(installer["Installers"][0]["InstallerUrl"], "http://127.0.0.1:8080/sima-cli-2.1.24-x64.msi")
        for changes in ({"sha256": "abc"}, {"product_code": "not-a-guid"}, {"msi_version": "2.1.24+x"},
                        {"upgrade_code": PRODUCT_CODE}):
            with self.subTest(changes=changes), self.assertRaises(SystemExit):
                self.write_installer_json(**changes)
                self.generate()
        self.write_installer_json()
        with self.assertRaises(SystemExit):
            self.generate("ftp://example.com/sima-cli.msi")

    def test_release_date_defaults_to_today(self):
        path = self.generator.write_manifests(self.installer_json, URL, self.root / "today")[1]
        installer = yaml.safe_load(path.read_text(encoding="utf-8"))
        self.assertEqual(str(installer["ReleaseDate"]), datetime.datetime.now(datetime.timezone.utc).date().isoformat())


if __name__ == "__main__":
    unittest.main()
