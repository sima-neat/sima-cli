"""Artifact access errors must not imply an interrupted firmware installation."""
from unittest.mock import MagicMock, patch

import click
import pytest
import requests
from click.testing import CliRunner

from sima_cli.update import swu, swu_artifacts


@click.command()
def update_command():
    swu.update_system('3.0', 'modalix', ip='192.0.2.1', internal=True, auto_confirm=True)


@pytest.mark.parametrize('stage', ['query', 'size', 'download'])
@pytest.mark.parametrize('status,expected', [
    (401, 'Artifactory rejected authentication (HTTP 401)'),
    (403, 'Access to Artifactory was denied (HTTP 403)'),
    (404, 'not found on Artifactory (HTTP 404)'),
    (500, 'Artifactory returned HTTP 500'),
])
def test_http_error_reports_cause_and_never_installs(stage, status, expected):
    response = requests.Response()
    response.status_code = status
    # Deliberately include a secret URL in the exception: it must not be echoed.
    error = requests.HTTPError('https://example.com/bundle.swu?token=secret', response=response)
    target = MagicMock()
    target.run.return_value = '/tmp/sima-cli-update.ABC12345'
    bundle = swu_artifacts.ARTIFACTORY_BASE_URL + '/bundle.swu'
    with patch.object(swu, 'Target', return_value=target), \
            patch.object(swu, 'prepare_key', return_value=(swu.DEFAULT_KEY, None)), \
            patch.object(swu, 'preflight', return_value={'running slot': 'A'}), \
            patch.object(swu, '_select_staging_root', return_value='/tmp'), \
            patch.object(swu_artifacts, 'get_auth_token', return_value='expired-token'), \
            patch.object(swu_artifacts.requests, 'Session') as session, \
            patch.object(swu, 'install_script') as install:
        if stage == 'query':
            session.return_value.post.return_value.raise_for_status.side_effect = error
            result = CliRunner().invoke(update_command)
        elif stage == 'size':
            session.return_value.head.return_value.raise_for_status.side_effect = error
            with patch.object(swu, 'resolve_bundle', return_value=bundle):
                result = CliRunner().invoke(update_command)
        else:
            # Use the actual downloader, which wraps the HTTP exception in RuntimeError.
            session.return_value.head.return_value.raise_for_status.side_effect = error
            with patch.object(swu, 'resolve_bundle', return_value=bundle), \
                    patch.object(swu, 'bundle_size', return_value=1):
                result = CliRunner().invoke(update_command)
    assert result.exit_code == 1
    assert expected in result.output
    assert 'No firmware was installed.' in result.output
    assert 'State is unknown' not in result.output
    assert 'secret' not in result.output
    if status in (401, 403):
        assert 'sima-cli -i login' in result.output
    if status == 403:
        assert 'permission' in result.output
    target.transfer.assert_not_called()
    install.assert_not_called()
    target.close.assert_called_once()


@pytest.mark.parametrize('token', [None, '', '  '])
def test_missing_login_reaches_cli_as_actionable_error(token):
    target = MagicMock()
    with patch.object(swu, 'Target', return_value=target), \
            patch.object(swu, 'prepare_key', return_value=(swu.DEFAULT_KEY, None)), \
            patch.object(swu, 'preflight'), \
            patch.object(swu_artifacts, 'get_auth_token', return_value=token), \
            patch.object(swu_artifacts.requests, 'Session') as session:
        result = CliRunner().invoke(update_command)
    assert result.exit_code == 1
    assert 'Artifactory login is required on this machine' in result.output
    assert 'sima-cli -i login' in result.output
    assert 'No firmware was installed.' in result.output
    assert 'State is unknown' not in result.output
    session.assert_not_called()
    target.transfer.assert_not_called()


@pytest.mark.parametrize('error,expected', [
    (requests.Timeout('secret'), 'request to Artifactory timed out'),
    (requests.ConnectionError('secret'), 'Unable to access Artifactory'),
    (OSError('disk full'), 'before firmware installation started'),
    (EOFError(), 'before firmware installation started'),
    (KeyboardInterrupt(), 'before firmware installation started'),
])
def test_preinstall_failures_do_not_claim_unknown_firmware_state(error, expected):
    with patch.object(swu, 'Target') as target, \
            patch.object(swu, 'prepare_key', return_value=(swu.DEFAULT_KEY, None)), \
            patch.object(swu, 'preflight'), \
            patch.object(swu, 'resolve_bundle', side_effect=error):
        result = CliRunner().invoke(update_command)
    assert result.exit_code == 1
    assert expected in result.output
    assert 'No firmware was installed.' in result.output
    assert 'State is unknown' not in result.output
    target.return_value.transfer.assert_not_called()


@pytest.mark.parametrize('error', [OSError('connection lost'), EOFError(), KeyboardInterrupt()])
def test_actual_install_interruption_still_requires_inspection(tmp_path, error):
    bundle = tmp_path / 'bundle.swu'
    bundle.write_bytes(b'bundle')
    target = MagicMock()
    def run(script, **kwargs):
        if kwargs.get('stream'):
            raise error
        return '/tmp/sima-cli-update.ABC12345'
    target.run.side_effect = run
    with patch.object(swu, 'Target', return_value=target), \
            patch.object(swu, 'prepare_key', return_value=(swu.DEFAULT_KEY, None)), \
            patch.object(swu, 'preflight', return_value={'running slot': 'A'}), \
            patch.object(swu, 'resolve_bundle', return_value=str(bundle)), \
            patch.object(swu, '_select_staging_root', return_value='/tmp'), \
            patch.object(swu, '_check_space'):
        result = CliRunner().invoke(update_command)
    assert result.exit_code == 1
    assert 'State is unknown; inspect the board before retrying.' in result.output
    assert 'No firmware was installed.' not in result.output
    assert 'Retained bundle' in result.output
