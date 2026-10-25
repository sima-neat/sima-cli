from unittest.mock import MagicMock, call, patch

import pytest
import requests

from sima_cli.download.downloader import _request_download, download_file_from_url


def response(status=200):
    result = MagicMock()
    result.status_code = status
    result.headers = {'content-length': '4'}
    if status >= 400:
        result.raise_for_status.side_effect = requests.HTTPError(response=result)
    result.__enter__.return_value = result
    result.iter_content.return_value = [b'data']
    return result


@pytest.mark.parametrize('method', ['head', 'get'])
@pytest.mark.parametrize('failure', [requests.ReadTimeout, requests.ConnectTimeout,
                                    requests.ConnectionError])
def test_download_recovers_before_headers(tmp_path, method, failure):
    with patch('sima_cli.download.downloader.requests.Session') as factory, \
            patch('sima_cli.download.downloader.time.sleep') as sleep:
        session = factory.return_value
        session.head.return_value = response()
        session.get.return_value = response()
        request = getattr(session, method)
        request.side_effect = [failure('temporary'), request.return_value]
        path = download_file_from_url('https://example.com/file.deb', str(tmp_path))
        assert (tmp_path / 'file.deb').read_bytes() == b'data'
        assert path == str(tmp_path / 'file.deb')
        assert request.call_count == 2
        assert request.call_args.kwargs['timeout'] == (10, 60)
        sleep.assert_called_once_with(1)
        session.close.assert_called_once()


@pytest.mark.parametrize('status', [429, 500, 502, 503, 504])
def test_transient_http_error_closes_response_and_retries(status):
    failed, ok = response(status), response()
    request = MagicMock(side_effect=[failed, ok])
    with patch('sima_cli.download.downloader.time.sleep') as sleep:
        assert _request_download(request, 'https://example.com/file') is ok
    failed.close.assert_called_once()
    sleep.assert_called_once_with(1)


@pytest.mark.parametrize('status', [401, 403, 404])
def test_permanent_http_error_does_not_retry(status):
    failed = response(status)
    request = MagicMock(return_value=failed)
    with patch('sima_cli.download.downloader.time.sleep') as sleep:
        with pytest.raises(requests.HTTPError):
            _request_download(request, 'https://example.com/file')
    request.assert_called_once()
    failed.close.assert_called_once()
    sleep.assert_not_called()


@pytest.mark.parametrize('failure', [requests.ReadTimeout('timeout'),
                                    requests.ConnectionError('connection')])
def test_exhaustion_is_bounded_and_preserves_destination(tmp_path, failure):
    destination = tmp_path / 'file.deb'
    destination.write_bytes(b'existing')
    with patch('sima_cli.download.downloader.requests.Session') as factory, \
            patch('sima_cli.download.downloader.time.sleep') as sleep:
        factory.return_value.head.side_effect = failure
        with pytest.raises(RuntimeError, match='Download failed'):
            download_file_from_url('https://example.com/file.deb', str(tmp_path))
        assert factory.return_value.head.call_count == 3
        assert sleep.call_args_list == [call(1), call(2)]
        factory.return_value.get.assert_not_called()
    assert destination.read_bytes() == b'existing'


def test_http_retry_exhaustion_closes_every_response():
    failures = [response(503) for _ in range(3)]
    request = MagicMock(side_effect=failures)
    with patch('sima_cli.download.downloader.time.sleep') as sleep:
        with pytest.raises(requests.HTTPError):
            _request_download(request, 'https://example.com/file')
    assert request.call_count == 3
    assert sleep.call_args_list == [call(1), call(2)]
    for failed in failures:
        failed.close.assert_called_once()


def test_tls_errors_are_not_retried():
    request = MagicMock(side_effect=requests.exceptions.SSLError('certificate'))
    with patch('sima_cli.download.downloader.time.sleep') as sleep:
        with pytest.raises(requests.exceptions.SSLError):
            _request_download(request, 'https://example.com/file')
    request.assert_called_once()
    sleep.assert_not_called()


def test_interrupted_body_is_not_replayed_or_published(tmp_path):
    def chunks(**kwargs):
        yield b'da'
        raise requests.ConnectionError('body interrupted')

    with patch('sima_cli.download.downloader.requests.Session') as factory, \
            patch('sima_cli.download.downloader.time.sleep') as sleep:
        session = factory.return_value
        session.head.return_value = response()
        session.get.return_value = response()
        session.get.return_value.iter_content.side_effect = chunks
        with pytest.raises(RuntimeError, match='body interrupted'):
            download_file_from_url('https://example.com/file.deb', str(tmp_path))
        session.get.assert_called_once()
        sleep.assert_not_called()
    assert not (tmp_path / 'file.deb').exists()
    assert (tmp_path / 'file.deb.part').read_bytes() == b'da'
