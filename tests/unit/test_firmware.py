import io
import os
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sima_cli.update.updater import _extract_required_files


def _add_tar_file(tar, name, content):
    data = content.encode("utf-8")
    info = tarfile.TarInfo(name)
    info.size = len(data)
    tar.addfile(info, io.BytesIO(data))


class FirmwareExtractionTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
