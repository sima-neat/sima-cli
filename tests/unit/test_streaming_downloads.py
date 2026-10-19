import hashlib
import json
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
import requests

from sima_cli.download.downloader import download_file_from_url
from sima_cli.utils.streaming import DOWNLOAD_CHUNK_SIZE, write_stream
from sima_cli.vulcan.artifacts import ArtifactClient, VulcanArtifactError, download_artifacts

URL = 'https://example.invalid/file.bin'


def response(data=b'abcdef', status=200, headers=None):
    r = Mock()
    r.status_code = status
    r.headers = requests.structures.CaseInsensitiveDict(headers or {'Content-Length': str(len(data)), 'ETag': '"v1"'})
    r.iter_content.return_value = iter([data])
    r.__enter__ = Mock(return_value=r)
    r.__exit__ = Mock(return_value=False)
    return r


def session_for(r, head_headers=None):
    s = Mock()
    s.head.return_value = response(headers=head_headers or {'Content-Length': '6', 'ETag': '"v1"'})
    s.get.return_value = r
    return s


def seed_partial(tmp_path, data=b'abc', etag='"v1"'):
    (tmp_path / 'file.bin.part').write_bytes(data)
    (tmp_path / 'file.bin.part.json').write_text(json.dumps({
        'url_sha256': hashlib.sha256(URL.encode()).hexdigest(), 'etag': etag,
    }))


def test_shared_uses_bounded_chunks_and_publishes(tmp_path):
    r = response()
    s = session_for(r)
    with patch('sima_cli.download.downloader.requests.Session', return_value=s):
        result = download_file_from_url(URL, str(tmp_path))
    assert Path(result).read_bytes() == b'abcdef'
    assert not list(tmp_path.glob('*.part*'))
    r.iter_content.assert_called_once_with(chunk_size=DOWNLOAD_CHUNK_SIZE)
    s.close.assert_called_once()


def test_resume_validated_range(tmp_path):
    seed_partial(tmp_path)
    r = response(b'def', 206, {'Content-Length': '3', 'Content-Range': 'bytes 3-5/6', 'ETag': '"v1"'})
    s = session_for(r)
    with patch('sima_cli.download.downloader.requests.Session', return_value=s):
        download_file_from_url(URL, str(tmp_path))
    assert (tmp_path / 'file.bin').read_bytes() == b'abcdef'
    assert s.get.call_args.kwargs['headers']['If-Range'] == '"v1"'


@pytest.mark.parametrize('headers', [
    {'Content-Range': 'bytes 0-2/6', 'Content-Length': '3'},
    {'Content-Range': 'bytes 3-5/6', 'Content-Length': '2'},
    {'Content-Range': 'bytes 3-5/6', 'ETag': '"v2"'},
    {'Content-Range': 'bytes 3-4/6'},
    {},
])
def test_reject_invalid_ranges_without_appending(tmp_path, headers):
    seed_partial(tmp_path)
    r = response(b'def', 206, headers or {'X-Test': 'missing range'})
    with patch('sima_cli.download.downloader.requests.Session', return_value=session_for(r)):
        with pytest.raises(RuntimeError, match='partial download'):
            download_file_from_url(URL, str(tmp_path))
    assert not (tmp_path / 'file.bin').exists()
    assert (tmp_path / 'file.bin.part').read_bytes() == b'abc'


def test_ignored_range_restarts(tmp_path):
    seed_partial(tmp_path)
    with patch('sima_cli.download.downloader.requests.Session', return_value=session_for(response())):
        download_file_from_url(URL, str(tmp_path))
    assert (tmp_path / 'file.bin').read_bytes() == b'abcdef'


@pytest.mark.parametrize('etag', ['"old"', ''])
def test_changed_or_missing_validator_restarts(tmp_path, etag):
    seed_partial(tmp_path, etag=etag)
    s = session_for(response())
    with patch('sima_cli.download.downloader.requests.Session', return_value=s):
        download_file_from_url(URL, str(tmp_path))
    assert 'Range' not in s.get.call_args.kwargs['headers']
    assert (tmp_path / 'file.bin').read_bytes() == b'abcdef'


def test_truncated_download_preserves_existing_destination(tmp_path):
    (tmp_path / 'file.bin').write_bytes(b'old')
    r = response(b'ab', headers={'Content-Length': '6', 'ETag': '"v1"'})
    with patch('sima_cli.download.downloader.requests.Session', return_value=session_for(r)):
        with pytest.raises(RuntimeError, match='size mismatch'):
            download_file_from_url(URL, str(tmp_path))
    assert (tmp_path / 'file.bin').read_bytes() == b'old'
    assert (tmp_path / 'file.bin.part').read_bytes() == b'ab'


def test_interrupted_download_can_resume(tmp_path):
    def broken():
        yield b'abc'
        raise requests.ConnectionError('interrupted')
    r = response()
    r.iter_content.return_value = broken()
    with patch('sima_cli.download.downloader.requests.Session', return_value=session_for(r)):
        with pytest.raises(RuntimeError, match='interrupted'):
            download_file_from_url(URL, str(tmp_path))
    assert not (tmp_path / 'file.bin').exists()
    r = response(b'def', 206, {'Content-Length': '3', 'Content-Range': 'bytes 3-5/6', 'ETag': '"v1"'})
    with patch('sima_cli.download.downloader.requests.Session', return_value=session_for(r)):
        download_file_from_url(URL, str(tmp_path))
    assert (tmp_path / 'file.bin').read_bytes() == b'abcdef'


def test_unknown_length_does_not_skip_empty_file(tmp_path):
    (tmp_path / 'file.bin').touch()
    r = response(headers={'ETag': '"v1"'})
    with patch('sima_cli.download.downloader.requests.Session', return_value=session_for(r, {'ETag': '"v1"'})):
        download_file_from_url(URL, str(tmp_path))
    assert (tmp_path / 'file.bin').read_bytes() == b'abcdef'


@pytest.mark.parametrize('size,sha', [(7, ''), (6, '0'*64)])
def test_stream_validation_preserves_existing_file(tmp_path, size, sha):
    dest = tmp_path / 'file.bin'
    dest.write_bytes(b'old')
    with pytest.raises(ValueError):
        write_stream(iter([b'abc', b'def']), dest, size, sha)
    assert dest.read_bytes() == b'old'
    assert not (tmp_path / 'file.bin.part').exists()


def test_vulcan_streams_and_hashes_without_response_content(tmp_path):
    r = response()
    client = ArtifactClient(session_for(r))
    artifact = {'path': 'file.bin', 's3_key': 'file.bin', 'size': 6,
                'sha256': hashlib.sha256(b'abcdef').hexdigest()}
    download_artifacts(client, 'https://example.invalid', {}, [artifact], tmp_path)
    assert (tmp_path / 'file.bin').read_bytes() == b'abcdef'
    r.iter_content.assert_called_once_with(chunk_size=DOWNLOAD_CHUNK_SIZE)
    assert client.session.get.call_args.kwargs['stream'] is True
    r.__exit__.assert_called_once()


def test_vulcan_closes_response_on_hash_failure(tmp_path):
    r = response()
    with pytest.raises(VulcanArtifactError, match='SHA256'):
        download_artifacts(ArtifactClient(session_for(r)), 'https://example.invalid', {},
                           [{'path': 'file.bin', 's3_key': 'file.bin', 'sha256': '0'*64}], tmp_path)
    assert not (tmp_path / 'file.bin').exists()
    r.__exit__.assert_called_once()


def test_fsync_precedes_publication(tmp_path):
    events = []
    with patch('sima_cli.utils.streaming.os.fsync', side_effect=lambda fd: events.append('fsync')), patch(
        'sima_cli.utils.streaming.os.replace', side_effect=lambda *args: events.append('rename')
    ):
        write_stream(iter([b'abc']), tmp_path / 'file.bin', 3)
    assert events[:2] == ['fsync', 'rename']


def test_sync_failure_does_not_publish(tmp_path):
    dest = tmp_path / 'file.bin'
    dest.write_bytes(b'old')
    with patch('sima_cli.utils.streaming.os.fsync', side_effect=OSError('I/O error')):
        with pytest.raises(OSError):
            write_stream(iter([b'abc']), dest, 3)
    assert dest.read_bytes() == b'old'
    assert not (tmp_path / 'file.bin.part').exists()


def test_vulcan_interruption_preserves_existing_destination(tmp_path):
    def broken():
        yield b'abc'
        raise requests.ConnectionError('interrupted')
    r = response()
    r.iter_content.return_value = broken()
    (tmp_path / 'file.bin').write_bytes(b'old')
    with pytest.raises(VulcanArtifactError, match='interrupted'):
        download_artifacts(ArtifactClient(session_for(r)), 'https://example.invalid', {},
                           [{'path': 'file.bin', 's3_key': 'file.bin', 'size': 6}], tmp_path)
    assert (tmp_path / 'file.bin').read_bytes() == b'old'
    assert not (tmp_path / 'file.bin.part').exists()
    r.__exit__.assert_called_once()


def test_real_http_download_stream(tmp_path):
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    payload = b'x' * (DOWNLOAD_CHUNK_SIZE * 2 + 17)

    class Handler(BaseHTTPRequestHandler):
        def do_HEAD(self):
            self.send_response(200)
            self.send_header('Content-Length', str(len(payload)))
            self.send_header('ETag', '"fixture"')
            self.end_headers()

        def do_GET(self):
            self.do_HEAD()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        url = f'http://127.0.0.1:{server.server_port}/file.bin'
        result = download_file_from_url(url, str(tmp_path))
        assert Path(result).read_bytes() == payload
        assert not list(tmp_path.glob('*.part*'))
    finally:
        server.shutdown()
        server.server_close()
        worker.join()
