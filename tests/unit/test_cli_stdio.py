import unittest
from unittest.mock import patch

from sima_cli.cli import _configure_stdio


class _Stream:
    def __init__(self):
        self.errors = None
        self.line_buffering = None

    def reconfigure(self, **kwargs):
        self.errors = kwargs.get("errors")
        self.line_buffering = kwargs.get("line_buffering")


class TestCliStdio(unittest.TestCase):
    def test_configure_stdio_replaces_unencodable_output(self):
        stdout = _Stream()
        stderr = _Stream()

        with patch("sima_cli.cli.sys.stdout", stdout), patch("sima_cli.cli.sys.stderr", stderr):
            _configure_stdio()

        self.assertEqual(stdout.errors, "replace")
        self.assertEqual(stderr.errors, "replace")

    def test_configure_stdio_flushes_each_line_without_a_tty(self):
        stdout = _Stream()
        stderr = _Stream()

        with patch("sima_cli.cli.sys.stdout", stdout), patch("sima_cli.cli.sys.stderr", stderr):
            _configure_stdio()

        # Without a TTY Python block-buffers stdout; prompts must still appear immediately.
        self.assertTrue(stdout.line_buffering)
        self.assertTrue(stderr.line_buffering)


if __name__ == "__main__":
    unittest.main()
