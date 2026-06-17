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
