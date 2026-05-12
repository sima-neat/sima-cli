from unittest.mock import patch

from click.testing import CliRunner

from sima_cli.upgrade import selfupdate as selfupdate_module


def test_selfupdate_dev_runs_downloaded_installer_on_non_windows():
    runner = CliRunner()
    response = _Response(b"print('installer')\n")

    with patch.object(selfupdate_module, "_is_windows", return_value=False), \
         patch("sima_cli.upgrade.selfupdate.tempfile.mkdtemp", return_value="/tmp/sima-dev"), \
         patch("builtins.open", create=True), \
         patch("sima_cli.upgrade.selfupdate.urllib.request.urlopen", return_value=response) as urlopen, \
         patch("sima_cli.upgrade.selfupdate.subprocess.run") as run:
        result = runner.invoke(selfupdate_module.selfupdate, ["--dev"], obj={})

    assert result.exit_code == 0
    req = urlopen.call_args.args[0]
    assert req.full_url == selfupdate_module.DEV_INSTALLER_URL
    assert req.get_header("User-agent") == "sima-cli-selfupdate/1"
    run.assert_called_once()
    assert run.call_args.args[0][-2:] == ["/tmp/sima-dev/sima-cli-install.py", "--current-env"]
    assert run.call_args.kwargs["check"]


def test_selfupdate_dev_prints_windows_instructions():
    runner = CliRunner()

    with patch.object(selfupdate_module, "_is_windows", return_value=True), \
         patch("sima_cli.upgrade.selfupdate.urllib.request.urlopen") as urlopen, \
         patch("sima_cli.upgrade.selfupdate.subprocess.run") as run:
        result = runner.invoke(selfupdate_module.selfupdate, ["--dev"], obj={})

    assert result.exit_code == 0
    assert "Invoke-WebRequest" in result.output
    assert "python .\\sima-cli-install.py --current-env" in result.output
    urlopen.assert_not_called()
    run.assert_not_called()


def test_selfupdate_dev_rejects_other_update_modes():
    runner = CliRunner()

    result = runner.invoke(selfupdate_module.selfupdate, ["--dev", "-v", "2.1.5"], obj={})

    assert result.exit_code == 1
    assert "Cannot use --dev with -v or -m" in result.output


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return self.payload
