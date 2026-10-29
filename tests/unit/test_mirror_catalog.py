"""Complete catalog discovery and narrowly bounded legacy fallback."""
from unittest.mock import Mock, call
import json

import pytest
import requests

from sima_cli.update.mirror_catalog import read_mirror_index

PRIMARY = 'https://artifacts.neat.sima.ai/daily-platform-images/index-v2.json'
LEGACY = PRIMARY.replace('index-v2.json', 'index.json')


def response(status, data=None):
    result = requests.Response()
    result.status_code = status
    result.url = PRIMARY
    result._content = json.dumps(data).encode()
    return result


def test_complete_catalog_is_preferred():
    session = Mock()
    session.get.return_value = response(200, {'builds': ['release']})
    assert read_mirror_index(session, PRIMARY, 30) == {'builds': ['release']}
    session.get.assert_called_once_with(PRIMARY, timeout=30)


@pytest.mark.parametrize('status', [403, 404])
def test_missing_complete_catalog_uses_legacy(status):
    session = Mock()
    session.get.side_effect = [response(status), response(200, {'builds': ['daily']})]
    assert read_mirror_index(session, PRIMARY, 15) == {'builds': ['daily']}
    assert session.get.call_args_list == [call(PRIMARY, timeout=15), call(LEGACY, timeout=15)]


@pytest.mark.parametrize('status', [401, 429, 500, 503])
def test_other_http_errors_do_not_fall_back(status):
    session = Mock()
    session.get.return_value = response(status)
    with pytest.raises(requests.HTTPError):
        read_mirror_index(session, PRIMARY, 30)
    session.get.assert_called_once_with(PRIMARY, timeout=30)


def test_network_error_does_not_fall_back():
    session = Mock()
    session.get.side_effect = requests.Timeout('timeout')
    with pytest.raises(requests.Timeout):
        read_mirror_index(session, PRIMARY, 30)
    assert session.get.call_count == 1


def test_invalid_complete_catalog_does_not_fall_back():
    session = Mock()
    result = response(200)
    result._content = b'not JSON'
    session.get.return_value = result
    with pytest.raises(ValueError):
        read_mirror_index(session, PRIMARY, 30)
    assert session.get.call_count == 1
