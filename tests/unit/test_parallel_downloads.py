import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import pytest

from sima_cli.download.parallel import download_parallel
from sima_cli.download.downloader import download_file_from_url


@pytest.mark.parametrize('mode', ['ok', 'ignored', 'changed', 'wrong-range', 'truncated', 'overlong', 'encoded'])
def test_parallel_real_http(tmp_path, mode):
    payload = bytes(range(256)) * 8193
    ranges = []
    lock = threading.Lock()
    barrier = threading.Barrier(4)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            assert self.headers['If-Match'] == '"v1"'
            start, end = map(int, self.headers['Range'][6:].split('-'))
            with lock:
                ranges.append((start, end))
            barrier.wait(timeout=10)
            data = payload[start:end + 1]
            if mode == 'truncated':
                data = data[:-1]
            if mode == 'overlong':
                data += b'x'
            self.send_response(200 if mode == 'ignored' else 206)
            self.send_header('ETag', '"v2"' if mode == 'changed' else '"v1"')
            self.send_header('Content-Range', f'bytes {start + (mode == "wrong-range")}-{end}/{len(payload)}')
            if mode == 'encoded':
                self.send_header('Content-Encoding', 'gzip')
            self.end_headers()
            try:
                self.wfile.write(data)
            except (ConnectionError, BrokenPipeError):
                pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    dest = tmp_path / 'bundle.swu'
    dest.write_bytes(b'previous')
    try:
        url = f'http://127.0.0.1:{server.server_port}/bundle.swu'
        if mode in ('ok', 'ignored'):
            assert download_parallel(url, dest, len(payload), '"v1"') == (mode == 'ok')
        else:
            with pytest.raises(ValueError):
                download_parallel(url, dest, len(payload), '"v1"')
        assert dest.read_bytes() == (payload if mode == 'ok' else b'previous')
        assert len(ranges) == 4
        assert sorted(ranges) == [(len(payload)*i//4, len(payload)*(i+1)//4-1) for i in range(4)]
        assert list(tmp_path.iterdir()) == [dest]
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_public_mirror_selects_parallel_and_falls_back(tmp_path):
    from tests.unit.test_streaming_downloads import response, session_for
    url = 'https://artifacts.neat.sima.ai/bundle.swu'
    session = session_for(response(), {'Content-Length': '6', 'ETag': '"v1"', 'Accept-Ranges': 'bytes'})
    with patch('sima_cli.download.downloader.requests.Session', return_value=session), \
            patch('sima_cli.download.downloader.PARALLEL_MIN_SIZE', 1), \
            patch('sima_cli.download.downloader.download_parallel', return_value=False) as parallel:
        assert Path(download_file_from_url(url, str(tmp_path))).read_bytes() == b'abcdef'
    parallel.assert_called_once_with(url, str(tmp_path / 'bundle.swu'), 6, '"v1"')


@pytest.mark.parametrize('headers,internal', [
    ({'Content-Length': '6', 'ETag': 'W/"v1"', 'Accept-Ranges': 'bytes'}, False),
    ({'Content-Length': '6', 'Accept-Ranges': 'bytes'}, False),
    ({'Content-Length': '6', 'ETag': '"v1"'}, False),
    ({'Content-Length': '6', 'ETag': '"v1"', 'Accept-Ranges': 'bytes'}, True),
])
def test_parallel_requires_public_validated_range_support(tmp_path, headers, internal):
    from tests.unit.test_streaming_downloads import response, session_for
    session = session_for(response(), headers)
    with patch('sima_cli.download.downloader.requests.Session', return_value=session), \
            patch('sima_cli.download.downloader.get_auth_token', return_value=None), \
            patch('sima_cli.download.downloader.PARALLEL_MIN_SIZE', 1), \
            patch('sima_cli.download.downloader.download_parallel') as parallel:
        download_file_from_url('https://artifacts.neat.sima.ai/bundle.swu', str(tmp_path), internal=internal)
    parallel.assert_not_called()


def test_parallel_preserves_contiguous_resume(tmp_path):
    import hashlib
    import json
    from tests.unit.test_streaming_downloads import response, session_for
    url = 'https://artifacts.neat.sima.ai/file.bin'
    (tmp_path / 'file.bin.part').write_bytes(b'abc')
    (tmp_path / 'file.bin.part.json').write_text(json.dumps({
        'url_sha256': hashlib.sha256(url.encode()).hexdigest(), 'etag': '"v1"',
    }))
    session = session_for(response(b'def', 206, {
        'Content-Length': '3', 'Content-Range': 'bytes 3-5/6', 'ETag': '"v1"',
    }), {'Content-Length': '6', 'ETag': '"v1"', 'Accept-Ranges': 'bytes'})
    with patch('sima_cli.download.downloader.requests.Session', return_value=session), \
            patch('sima_cli.download.downloader.PARALLEL_MIN_SIZE', 1), \
            patch('sima_cli.download.downloader.download_parallel') as parallel:
        result = download_file_from_url(url, str(tmp_path))
    assert Path(result).read_bytes() == b'abcdef'
    parallel.assert_not_called()
