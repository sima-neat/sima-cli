import unittest
from tempfile import TemporaryDirectory
from unittest.mock import patch, MagicMock
import os

from sima_cli.download import (
    download_file_from_url,
    download_folder_from_url,
)

class TestDownloader(unittest.TestCase):

    @patch("sima_cli.download.downloader.requests.Session")
    def test_download_file_success(self, mock_session_cls):
        # Simulate HEAD response
        mock_head_response = MagicMock()
        mock_head_response.headers = {'content-length': '9'}
        mock_head_response.raise_for_status = lambda: None

        # Simulate GET response
        mock_get_response = MagicMock()
        mock_get_response.iter_content = lambda chunk_size: [b"test data"]
        mock_get_response.headers = {'content-length': '9'}
        mock_get_response.raise_for_status = lambda: None
        mock_get_response.__enter__.return_value = mock_get_response

        mock_session = mock_session_cls.return_value
        mock_session.head.return_value = mock_head_response
        mock_session.get.return_value = mock_get_response

        with TemporaryDirectory() as dest_folder:
            url = "https://127.0.0.1/sima/file.tar"
            downloaded_path = download_file_from_url(url, dest_folder)

            self.assertTrue(os.path.exists(downloaded_path))
            with open(downloaded_path, "rb") as f:
                self.assertEqual(f.read(), b"test data")

    def test_invalid_url_raises(self):
        with self.assertRaises(ValueError):
            download_file_from_url("https://example.com/", "somewhere")

    @patch("sima_cli.download.downloader.login")
    @patch("sima_cli.download.downloader.requests.Session")
    def test_check_url_available_uses_external_login_only_for_docs_hosts(self, mock_session_cls, mock_login):
        from sima_cli.download.downloader import check_url_available

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_session_cls.return_value.head.return_value = mock_response

        self.assertTrue(check_url_available("https://attacker.example/docs.sima.ai/file.tar"))

        mock_login.assert_not_called()
        mock_session_cls.return_value.head.assert_called_once()

    @patch("sima_cli.download.downloader.login")
    def test_check_url_available_uses_external_login_for_exact_docs_host(self, mock_login):
        from sima_cli.download.downloader import check_url_available

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_login.return_value.head.return_value = mock_response

        self.assertTrue(check_url_available("https://docs.sima.ai/pkg_downloads/file.tar"))

        mock_login.assert_called_once_with("external")

    @patch("sima_cli.download.downloader.download_github_asset")
    @patch("sima_cli.download.downloader.requests.Session")
    def test_download_file_uses_github_asset_only_for_exact_github_host(
        self, mock_session_cls, mock_download_github_asset
    ):
        mock_head_response = MagicMock()
        mock_head_response.headers = {'content-length': '4'}
        mock_head_response.raise_for_status = lambda: None

        mock_get_response = MagicMock()
        mock_get_response.iter_content = lambda chunk_size: [b"data"]
        mock_get_response.headers = {'content-length': '4'}
        mock_get_response.raise_for_status = lambda: None
        mock_get_response.__enter__.return_value = mock_get_response

        mock_session = mock_session_cls.return_value
        mock_session.head.return_value = mock_head_response
        mock_session.get.return_value = mock_get_response

        with TemporaryDirectory() as dest_folder:
            downloaded_path = download_file_from_url(
                "https://attacker.example/github.com/owner/repo/releases/download/v1/file.tar",
                dest_folder,
            )
            self.assertTrue(os.path.exists(downloaded_path))

        mock_download_github_asset.assert_not_called()

    @patch("sima_cli.download.downloader.requests.Session")
    def test_skip_already_downloaded_file(self, mock_session_cls):
        with TemporaryDirectory() as dest_folder:
            test_file_path = os.path.join(dest_folder, "file.txt")

            # Create a complete file manually
            with open(test_file_path, "wb") as f:
                f.write(b"123456789")

            mock_head_response = MagicMock()
            mock_head_response.headers = {'content-length': '9'}
            mock_head_response.raise_for_status = lambda: None
            mock_session = mock_session_cls.return_value
            mock_session.head.return_value = mock_head_response

            url = "https://127.0.0.1/sima/file.txt"
            returned_path = download_file_from_url(url, dest_folder)

            self.assertEqual(returned_path, test_file_path)
            mock_session.get.assert_not_called()

    @patch("sima_cli.download.downloader.requests.get")
    def test_list_directory_files_parses_links(self, mock_get):
        from sima_cli.download.downloader import _list_directory_files

        # Simulated directory listing HTML
        mock_html = '''
            <a href="../">Parent</a>
            <a href="model1.onnx">model1.onnx</a>
            <a href="readme.txt">readme.txt</a>
            <a href="subfolder/">subfolder/</a>
        '''
        mock_get_response = MagicMock()
        mock_get_response.text = mock_html
        mock_get_response.headers = {"Content-Type": "text/html"}
        mock_get_response.raise_for_status = lambda: None
        mock_get_response.__enter__.return_value = mock_get_response
        mock_get.return_value = mock_get_response

        result = _list_directory_files("http://host/folder/")
        self.assertEqual(result, [
            "http://host/folder/model1.onnx",
            "http://host/folder/readme.txt"
        ])

    @patch("sima_cli.download.downloader.download_file_from_url")
    @patch("sima_cli.download.downloader._list_directory_files")
    def test_download_folder_from_url(self, mock_list_files, mock_download_file):
        mock_list_files.return_value = [
            "http://server/file1.txt",
            "http://server/file2.txt"
        ]
        mock_download_file.side_effect = (
            lambda url, dest, **_: os.path.join(dest, os.path.basename(url))
        )

        with TemporaryDirectory() as dest:
            downloaded = download_folder_from_url("http://server/", dest)

            self.assertEqual(len(downloaded), 2)
            self.assertIn(os.path.join(dest, "file1.txt"), downloaded)

if __name__ == "__main__":
    unittest.main()
