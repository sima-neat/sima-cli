import json
import unittest
from pathlib import Path

from sima_cli.install.metadata_validator import MetadataValidationError, validate_metadata


def _base_metadata(platforms):
    return {
        "name": "demo",
        "version": "1.0.0",
        "release": "",
        "platforms": platforms,
        "resources": ["payload.txt"],
    }


class MetadataValidatorTests(unittest.TestCase):
    def test_accepts_strict_platform_entries(self):
        metadata = _base_metadata(
            [
                {
                    "type": "host",
                    "os": ["mac", "linux"],
                    "versions": {"linux": [">=22.04"]},
                    "arch": ["amd64"],
                },
                {
                    "type": "board",
                    "compatible_with": ["modalix"],
                    "version": ">=2.1.0,<=2.1.2",
                },
                {"type": "palette", "version": "2.0.0"},
            ]
        )

        self.assertTrue(validate_metadata(metadata))

    def test_host_version_must_reference_declared_os(self):
        with self.assertRaisesRegex(MetadataValidationError, "must also be listed in 'os'"):
            validate_metadata(
                _base_metadata(
                    [
                        {
                            "type": "host",
                            "os": ["ubuntu"],
                            "versions": {"linux": ["==24.04"]},
                        }
                    ]
                )
            )

    def test_host_version_must_be_valid_spec(self):
        with self.assertRaisesRegex(MetadataValidationError, "Invalid host version spec"):
            validate_metadata(
                _base_metadata(
                    [
                        {
                            "type": "host",
                            "os": ["ubuntu"],
                            "versions": {"ubuntu": ["~24.04"]},
                        }
                    ]
                )
            )

    def test_host_arch_must_be_valid(self):
        with self.assertRaisesRegex(MetadataValidationError, "Invalid host architecture"):
            validate_metadata(_base_metadata([{"type": "host", "os": ["linux"], "arch": ["riscv64"]}]))

    def test_host_requires_non_empty_os_list(self):
        with self.assertRaisesRegex(MetadataValidationError, "'os' is required for host"):
            validate_metadata(_base_metadata([{"type": "host"}]))

        with self.assertRaisesRegex(MetadataValidationError, "'os' must be a non-empty list"):
            validate_metadata(_base_metadata([{"type": "host", "os": []}]))

    def test_board_requires_non_empty_compatible_with_list(self):
        with self.assertRaisesRegex(MetadataValidationError, "'compatible_with' must be a non-empty list"):
            validate_metadata(_base_metadata([{"type": "board", "compatible_with": []}]))

    def test_board_version_must_be_valid_spec(self):
        with self.assertRaisesRegex(MetadataValidationError, "Invalid board version spec"):
            validate_metadata(
                _base_metadata(
                    [
                        {
                            "type": "board",
                            "compatible_with": ["modalix"],
                            "version": "~2.1",
                        }
                    ]
                )
            )

    def test_palette_version_must_be_exact_version(self):
        with self.assertRaisesRegex(MetadataValidationError, "Invalid palette version"):
            validate_metadata(_base_metadata([{"type": "palette", "version": ">=2.0.0"}]))

    def test_installation_type_is_absent_script_or_windows_msi(self):
        def with_installation(installation):
            metadata = _base_metadata([{"type": "host", "os": ["linux"]}])
            metadata["installation"] = installation
            return metadata

        script = {"script": "./install.sh", "post-message": "Done."}
        self.assertTrue(validate_metadata(with_installation(script)))
        self.assertTrue(validate_metadata(with_installation({**script, "type": "script"})))
        windows = with_installation({"type": "windows-msi", "msi": "payload.msi"})
        windows["resources"] = ["payload.msi"]
        windows["resources-checksum"] = {"payload.msi": "a" * 64}
        self.assertTrue(validate_metadata(windows))

        # Every package type is refused, not only Windows ones: a document that sets
        # another type is not installed with its script.
        for install_type in ("bash", "shell", "debian-packages", "", None, ["script"], 1):
            with self.subTest(install_type=install_type):
                with self.assertRaisesRegex(
                    MetadataValidationError, "Unsupported installation type"
                ):
                    validate_metadata(with_installation({**script, "type": install_type}))
        for installation in (None, "./install.sh", ["./install.sh"]):
            with self.subTest(installation=installation):
                with self.assertRaisesRegex(
                    MetadataValidationError, "'installation' must be an object"
                ):
                    validate_metadata(with_installation(installation))

    def windows_wheelhouse_metadata(self, wheelhouse):
        resources = [
            "core.msi",
            "pyneatpcie-0.6.1-cp314-cp314-win_amd64.whl",
            "wheelhouse/numpy-2.5.4-cp314-cp314-win_amd64.whl",
            "wheelhouse/opencv_python-5.0.0.93-cp37-abi3-win_amd64.whl",
        ]
        return {
            **_base_metadata([{"type": "host", "os": ["windows"], "arch": ["amd64"]}]),
            "resources": resources,
            "resources-checksum": {resource: "a" * 64 for resource in resources},
            "installation": {
                "type": "windows-msi",
                "msi": "core.msi",
                "python": {
                    "wheel": "pyneatpcie-0.6.1-cp314-cp314-win_amd64.whl",
                    "distribution": "pyneatpcie",
                    "version": "0.6.1",
                    "import": "pyneatpcie",
                    "wheelhouse": wheelhouse,
                },
            },
        }

    PINNED_WHEELHOUSE = [
        {"wheel": "wheelhouse/numpy-2.5.4-cp314-cp314-win_amd64.whl",
         "distribution": "numpy", "version": "2.5.4"},
        {"wheel": "wheelhouse/opencv_python-5.0.0.93-cp37-abi3-win_amd64.whl",
         "distribution": "opencv-python", "version": "5.0.0.93"},
    ]

    def assert_wheelhouse_rejected(self, wheelhouse, message):
        with self.assertRaisesRegex(MetadataValidationError, message):
            validate_metadata(self.windows_wheelhouse_metadata(wheelhouse))

    def test_windows_wheelhouse_accepts_pinned_inventory(self):
        metadata = self.windows_wheelhouse_metadata(self.PINNED_WHEELHOUSE)
        self.assertTrue(validate_metadata(metadata))

    def test_windows_wheelhouse_rejects_unlisted_wheel(self):
        entry = {
            **self.PINNED_WHEELHOUSE[0],
            "wheel": "wheelhouse/numpy-2.6.0-cp314-cp314-win_amd64.whl",
        }
        self.assert_wheelhouse_rejected(
            [entry], r"wheelhouse\[0\]\.wheel' must be listed in 'resources'"
        )

    def test_windows_wheelhouse_rejects_unsafe_version(self):
        # The version is written into a pip requirements line.
        entry = {**self.PINNED_WHEELHOUSE[0], "version": "1.0 --index-url x"}
        self.assert_wheelhouse_rejected(
            [entry], r"wheelhouse\[0\]\.version' must be an exact version"
        )

    def test_windows_wheelhouse_rejects_duplicate_distribution(self):
        renamed = {**self.PINNED_WHEELHOUSE[1], "distribution": "OpenCV_Python"}
        self.assert_wheelhouse_rejected(
            [self.PINNED_WHEELHOUSE[1], renamed], "must name each distribution once"
        )
        itself = {**self.PINNED_WHEELHOUSE[0], "distribution": "PyNeatPCIe"}
        self.assert_wheelhouse_rejected([itself], "must name each distribution once")

    def test_windows_wheelhouse_rejects_unknown_key(self):
        entry = {**self.PINNED_WHEELHOUSE[0], "index-url": "https://example.invalid/simple"}
        self.assert_wheelhouse_rejected(
            [entry], r"'installation\.python\.wheelhouse\[0\]' has unsupported fields: index-url$"
        )
        missing = {"wheel": entry["wheel"], "distribution": "numpy"}
        self.assert_wheelhouse_rejected(
            [missing], r"wheelhouse\[0\]\.version' must be a non-empty string"
        )

    def test_windows_wheelhouse_rejects_empty_list(self):
        self.assert_wheelhouse_rejected(
            [], "'installation.python.wheelhouse' must be a non-empty list"
        )

    def test_metadata_fixture_files_match_validator_expectations(self):
        fixture_dir = Path(__file__).parent / "pkg-metadata"

        for fixture in sorted(fixture_dir.glob("valid-*.json")):
            with self.subTest(fixture=fixture.name):
                validate_metadata(json.loads(fixture.read_text(encoding="utf-8")))

        for fixture in sorted(fixture_dir.glob("invalid-*.json")):
            with self.subTest(fixture=fixture.name):
                with self.assertRaises(MetadataValidationError):
                    validate_metadata(json.loads(fixture.read_text(encoding="utf-8")))


if __name__ == "__main__":
    unittest.main()
