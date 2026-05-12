from unittest.mock import patch

from click.testing import CliRunner

from sima_cli.upgrade import selfupdate as selfupdate_module


def test_selfupdate_dev_runs_downloaded_installer_on_non_windows():
    runner = CliRunner()

    with patch.object(selfupdate_module, "_is_windows", return_value=False), \
         patch("sima_cli.upgrade.selfupdate.tempfile.mkdtemp", return_value="/tmp/sima-dev"), \
         patch("sima_cli.upgrade.selfupdate.urllib.request.urlretrieve") as retrieve, \
         patch("sima_cli.upgrade.selfupdate.subprocess.run") as run:
        result = runner.invoke(selfupdate_module.selfupdate, ["--dev"], obj={})

    assert result.exit_code == 0
    retrieve.assert_called_once_with(
        selfupdate_module.DEV_INSTALLER_URL,
        "/tmp/sima-dev/sima-cli-install.py",
    )
    run.assert_called_once()
    assert run.call_args.args[0][-1] == "/tmp/sima-dev/sima-cli-install.py"
    assert run.call_args.kwargs["check"]


def test_selfupdate_dev_prints_windows_instructions():
    runner = CliRunner()

    with patch.object(selfupdate_module, "_is_windows", return_value=True), \
         patch("sima_cli.upgrade.selfupdate.urllib.request.urlretrieve") as retrieve, \
         patch("sima_cli.upgrade.selfupdate.subprocess.run") as run:
        result = runner.invoke(selfupdate_module.selfupdate, ["--dev"], obj={})

    assert result.exit_code == 0
    assert "Invoke-WebRequest" in result.output
    assert "python .\\sima-cli-install.py" in result.output
    retrieve.assert_not_called()
    run.assert_not_called()


def test_selfupdate_dev_rejects_other_update_modes():
    runner = CliRunner()

    result = runner.invoke(selfupdate_module.selfupdate, ["--dev", "-v", "2.1.5"], obj={})

    assert result.exit_code == 1
    assert "Cannot use --dev with -v or -m" in result.output
