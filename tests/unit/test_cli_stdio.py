import unittest
from unittest.mock import patch

from sima_cli.cli import _configure_stdio_errors


class _Stream:
    def __init__(self):
        self.errors = None

    def reconfigure(self, **kwargs):
        self.errors = kwargs.get("errors")


class TestCliStdio(unittest.TestCase):
    def test_configure_stdio_replaces_unencodable_output(self):
        stdout = _Stream()
        stderr = _Stream()

        with patch("sima_cli.cli.sys.stdout", stdout), patch("sima_cli.cli.sys.stderr", stderr):
            _configure_stdio_errors()

        self.assertEqual(stdout.errors, "replace")
        self.assertEqual(stderr.errors, "replace")


if __name__ == "__main__":
    unittest.main()
