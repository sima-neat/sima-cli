import gzip
import io
import os
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sima_cli.update.query import _list_available_firmware_versions_external
from sima_cli.update.updater import _extract_required_files, _resolve_firmware_url


def _add_tar_file(tar, name, content):
    data = content.encode("utf-8")
    info = tarfile.TarInfo(name)
    info.size = len(data)
    tar.addfile(info, io.BytesIO(data))


class FirmwareExtractionTests(unittest.TestCase):
    def test_bootimg_decompresses_elxr_disk_image(self):
        with tempfile.TemporaryDirectory() as tmp:
            compressed_image = Path(tmp) / "elxr-palette-modalix-2.1.2-arm64.img.gz"
            with gzip.open(compressed_image, "wb") as image:
                image.write(b"disk image")

            extracted = _extract_required_files(
                str(compressed_image),
                board="modalix",
                update_type="bootimg",
                flavor="headless",
            )

            image_path = compressed_image.with_suffix("")
            self.assertEqual(extracted, [str(compressed_image), str(image_path)])
            self.assertEqual(image_path.read_bytes(), b"disk image")

    def test_netboot_extracts_all_archive_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "netboot.tar"
            with tarfile.open(archive, "w") as tar:
                _add_tar_file(tar, "Image", "kernel")
                _add_tar_file(tar, "nested/extra.cfg", "extra")
                _add_tar_file(tar, "unexpected.bin", "payload")

            extracted = _extract_required_files(
                str(archive),
                board="modalix",
                update_type="netboot",
                flavor="headless",
            )

            extracted_names = {os.path.relpath(path, tmp) for path in extracted}
            self.assertEqual(extracted_names, {"Image", "nested/extra.cfg", "unexpected.bin"})
            self.assertEqual((Path(tmp) / "nested" / "extra.cfg").read_text(), "extra")

    @patch("sima_cli.update.updater.get_environment_type", return_value=("host", "mac"))
    def test_standard_update_keeps_required_file_filter(self, _mock_env):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "standard.tar"
            with tarfile.open(archive, "w") as tar:
                _add_tar_file(tar, "troot-upgrade-simaai-ev.swu", "troot")
                _add_tar_file(tar, "unrelated.txt", "skip")

            extracted = _extract_required_files(
                str(archive),
                board="modalix",
                update_type="standard",
                flavor="headless",
            )

            extracted_names = {os.path.relpath(path, tmp) for path in extracted}
            self.assertEqual(extracted_names, {"troot-upgrade-simaai-ev.swu"})
            self.assertFalse((Path(tmp) / "unrelated.txt").exists())


class ElxrBootImageResolutionTests(unittest.TestCase):
    @patch("sima_cli.update.query.load_resource_config")
    def test_external_bootimg_uses_palette_disk_image(self, mock_config):
        mock_config.return_value = {
            "public": {"download": {"download_url": "https://docs.sima.ai/pkg_downloads/"}}
        }

        urls = _list_available_firmware_versions_external(
            "modalix", "2.1.2", swtype="elxr", update_type="bootimg"
        )

        self.assertEqual(
            urls,
            [
                "https://docs.sima.ai/pkg_downloads/SDK2.1.2/devkit/modalix/elxr/"
                "elxr-palette-modalix-2.1.2-arm64.img.gz"
            ],
        )

    @patch("sima_cli.update.updater.load_resource_config")
    def test_internal_prerelease_bootimg_uses_palette_disk_image(self, mock_config):
        mock_config.return_value = {
            "internal": {
                "download": {"download_url": "artifactory/"},
                "artifactory": {"url": "https://artifacts.eng.sima.ai"},
            }
        }

        url = _resolve_firmware_url(
            "2.1.3_pre-release_master_B1423",
            "modalix",
            internal=True,
            swtype="elxr",
            update_type="bootimg",
        )

        self.assertEqual(
            url,
            "https://artifacts.eng.sima.ai/artifactory/soc-images/elxr/modalix/"
            "2.1.3_pre-release_master_B1423/artifacts/palette/"
            "elxr-palette-modalix-2.1.3-arm64.img.gz",
        )

    @patch("sima_cli.update.query.load_resource_config")
    def test_external_netboot_still_uses_tftp_archive(self, mock_config):
        mock_config.return_value = {
            "public": {"download": {"download_url": "https://docs.sima.ai/pkg_downloads/"}}
        }

        urls = _list_available_firmware_versions_external(
            "modalix", "2.1.2", swtype="elxr", update_type="netboot"
        )

        self.assertEqual(
            urls,
            [
                "https://docs.sima.ai/pkg_downloads/SDK2.1.2/devkit/modalix/elxr/"
                "modalix-tftp-boot-minimal.tar.gz"
            ],
        )


if __name__ == "__main__":
    unittest.main()
