"""Static contract for the Windows installer (MSI) build, whose WiX stages only run on Windows."""

import importlib.util
import re
import tempfile
import unittest
import xml.etree.ElementTree as ElementTree
from pathlib import Path

from sima_cli.utils import windows_installer


ROOT = Path(__file__).resolve().parents[2]
PACKAGING = ROOT / "packaging/windows"


def load_build_msi():
    spec = importlib.util.spec_from_file_location("build_msi", PACKAGING / "build_msi.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class WindowsInstallerPackagingTests(unittest.TestCase):
    def setUp(self):
        self.build_msi = load_build_msi()
        self.wxs = (PACKAGING / "sima-cli.wxs").read_text(encoding="utf-8")

    def test_msi_version_is_the_release_part_and_only_final_versions_are_releases(self):
        for version, expected, release in (
            ("2.1.24", "2.1.24", True),
            ("2.1.24+codex.1074.windows.1d1543d5cd34", "2.1.24", False),
            ("2.1.24rc1", "2.1.24", False),
            ("2.1.24.post1", "2.1.24", False),
        ):
            with self.subTest(version=version):
                self.assertEqual(self.build_msi.msi_version(version), expected)
                self.assertIs(self.build_msi.is_release(version), release)
        for version in ("2.1", "2.1.24.1", "1!2.1.24", "256.0.0", "1.0.65536", "not-a-version"):
            with self.subTest(version=version), self.assertRaises(SystemExit):
                self.build_msi.msi_version(version)

    def test_bundled_python_is_the_release_windows_bat_installs(self):
        script = (ROOT / "scripts/install/windows.bat").read_text(encoding="utf-8")
        installer = re.search(r"/ftp/python/(3\.14\.\d+)/python-\1-amd64\.exe", script)
        self.assertEqual(self.build_msi.PYTHON_VERSION, installer.group(1))
        self.assertEqual(
            self.build_msi.PYTHON_EMBED_URL,
            f"https://www.python.org/ftp/python/{installer.group(1)}/python-{installer.group(1)}-embed-amd64.zip",
        )
        self.assertRegex(self.build_msi.PYTHON_EMBED_SHA256, r"^[0-9a-f]{64}$")

    def test_upgrade_code_is_fixed_and_defined_once(self):
        # Windows Installer and WinGet match installed copies by it; it can never change.
        self.assertEqual(windows_installer.UPGRADE_CODE, "{5D2C50B6-8A8A-48BF-BC19-42AE7FE26DBC}")
        self.assertEqual(self.build_msi.IDENTITY["UPGRADE_CODE"], windows_installer.UPGRADE_CODE)
        sources = [path for path in PACKAGING.rglob("*") if path.is_file() and "__pycache__" not in path.parts]
        for path in sources:
            with self.subTest(path=path.name):
                self.assertNotIn("5D2C50B6", path.read_text(encoding="utf-8"))
        self.assertEqual(self.wxs.count('UpgradeCode="$(var.UpgradeCode)"'), 1)

    def test_wxs_is_well_formed_and_uses_only_variables_the_build_passes(self):
        ElementTree.fromstring(self.wxs.encode("utf-8"))
        used = set(re.findall(r"\$\(var\.(\w+)\)", self.wxs))
        # BundleDir is used by the files heat harvests, not by sima-cli.wxs itself.
        self.assertEqual(used | {"BundleDir"}, set(self.build_msi.CANDLE_VARIABLES))
        self.assertNotRegex(self.wxs, r"\$\((?!var\.)\w+\.")
        self.assertNotIn("<?define", self.wxs)

    def test_wxs_installs_per_user_and_prepends_its_bin_folder_to_the_user_path(self):
        wix = "{http://schemas.microsoft.com/wix/2006/wi}"
        product = ElementTree.fromstring(self.wxs.encode("utf-8")).find(f"{wix}Product")
        package = product.find(f"{wix}Package")
        self.assertEqual(package.get("InstallerVersion"), "500")
        self.assertEqual(package.get("InstallPrivileges"), "limited")
        self.assertIsNone(package.get("InstallScope"))
        properties = {item.get("Id"): item.get("Value") for item in product.findall(f"{wix}Property")}
        self.assertEqual(properties["ALLUSERS"], "2")
        self.assertEqual(properties["MSIINSTALLPERUSER"], "1")
        upgrade = product.find(f"{wix}MajorUpgrade")
        self.assertEqual(upgrade.get("DowngradeErrorMessage"), "A newer version of sima-cli is already installed.")
        self.assertEqual(product.get("Manufacturer"), "$(var.Publisher)")
        self.assertEqual(product.get("Name"), "$(var.ProductName)")
        environment = product.find(f".//{wix}Environment")
        self.assertEqual(
            {key: environment.get(key) for key in ("Name", "Value", "Part", "Action", "System", "Permanent")},
            {"Name": "PATH", "Value": "[INSTALLFOLDER]bin", "Part": "first", "Action": "set",
             "System": "no", "Permanent": "no"},
        )
        component = next(item for item in product.iter(f"{wix}Component") if environment in list(item))
        key_path = component.find(f"{wix}RegistryValue")
        self.assertEqual((key_path.get("Root"), key_path.get("KeyPath")), ("HKCU", "yes"))
        self.assertNotIn("CustomAction", self.wxs)
        folders = product.find(f"{wix}Directory").find(f"{wix}Directory")
        self.assertEqual(folders.get("Id"), "ProgramFiles64Folder")
        self.assertEqual(folders.find(f"{wix}Directory").get("Id"), "INSTALLFOLDER")

    def test_bundle_paths_longer_than_the_limit_fail_the_build(self):
        with tempfile.TemporaryDirectory() as directory:
            bundle = Path(directory)
            (bundle / "runtime").mkdir()
            (bundle / "runtime" / "short.py").write_text("", encoding="utf-8")
            self.assertEqual(self.build_msi.check_path_lengths(bundle), len("runtime\\short.py"))
            name = "x" * (self.build_msi.MAX_BUNDLE_PATH - len("runtime\\") + 1)
            (bundle / "runtime" / name).write_text("", encoding="utf-8")
            with self.assertRaises(SystemExit):
                self.build_msi.check_path_lengths(bundle)

    def test_requirements_are_exact_pins_including_the_windows_pcie_modules(self):
        lines = [
            line for line in (PACKAGING / "requirements.txt").read_text(encoding="utf-8").splitlines()
            if line and not line.startswith("#")
        ]
        for line in lines:
            self.assertRegex(line, r"^[A-Za-z0-9_.-]+==[0-9][A-Za-z0-9.]*$")
        names = {line.split("==")[0].lower() for line in lines}
        self.assertTrue({"wmi", "pywin32"} <= names)
        self.assertNotIn("sima-cli", names)
        self.assertFalse({"pip", "setuptools"} & names)
        build = (PACKAGING / "build-requirements.txt").read_text(encoding="utf-8")
        self.assertRegex(build, r"(?m)^distlib==\d")

    def test_installer_marker_matches_the_cli_check(self):
        self.assertEqual(self.build_msi.IDENTITY["INSTALLER_MARKER"], "sima-cli-msi")
        self.assertEqual(self.build_msi.LAUNCHER_SHEBANG, b"#!<launcher_dir>\\..\\runtime\\python.exe\n")


if __name__ == "__main__":
    unittest.main()
