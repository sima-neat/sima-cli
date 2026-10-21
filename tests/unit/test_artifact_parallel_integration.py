"""Exercise public consumers against a real range-capable HTTP endpoint."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import time
from unittest.mock import Mock, patch

import pytest

from sima_cli.models.client import RegistryClient, ModelRegistryError
from sima_cli.vulcan.artifacts import ArtifactClient, VulcanArtifactError, download_artifacts


@pytest.fixture
def server():
    payload = bytes(range(256)) * 4097
    state = dict(active=0, maximum=0, ranges=0, expired=0)
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            with lock:
                state['active'] += 1
                state['maximum'] = max(state['maximum'], state['active'])
            try:
                time.sleep(.03)
                if self.path.startswith('/expired'):
                    state['expired'] += 1
                    self.send_response(403)
                    self.end_headers()
                    return
                requested = self.headers.get('Range')
                if requested and not self.path.startswith('/ignore'):
                    with lock:
                        state['ranges'] += 1
                    start, end = map(int, requested[6:].split('-'))
                    data = payload[start:end + 1]
                    self.send_response(206)
                    self.send_header('Content-Range', f'bytes {start}-{end}/{len(payload)}')
                else:
                    data = payload
                    self.send_response(200)
                if self.path.startswith('/corrupt'):
                    data = b'X' + data[1:]
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                with lock:
                    state['active'] -= 1

    httpd = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f'http://127.0.0.1:{httpd.server_port}', payload, state
    httpd.shutdown()
    httpd.server_close()
    thread.join()


def model_download(base, payload, path, endpoint='file', initial=None, progress=None):
    client = RegistryClient(base)
    client.artifact_download = Mock(return_value={'download_url': base + '/' + endpoint})
    artifact = {'name': 'file', 'size_bytes': len(payload), 'sha256': hashlib.sha256(payload).hexdigest()}
    client.download('run', artifact, path, initial_download=initial, progress=progress)
    return client


def neat_download(base, payload, directory, endpoint='file'):
    return download_artifacts(ArtifactClient(), base, {}, [{
        'path': 'file', 's3_key': endpoint, 'size': len(payload),
        'sha256': hashlib.sha256(payload).hexdigest(),
    }], directory)


def test_mixed_models_and_neat_share_four_connections(tmp_path, server):
    base, payload, state = server
    with patch('sima_cli.models.client.PARALLEL_MIN_SIZE', 1), patch('sima_cli.vulcan.artifacts.PARALLEL_MIN_SIZE', 1):
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(model_download, base, payload, tmp_path / f'model{i}') for i in range(2)]
            futures += [pool.submit(neat_download, base, payload, tmp_path / f'neat{i}') for i in range(2)]
            for future in futures:
                future.result()
    assert state['ranges'] == 16
    assert state['maximum'] == 4
    assert (tmp_path / 'model0').read_bytes() == payload
    assert (tmp_path / 'neat1/file').read_bytes() == payload
    assert not list(tmp_path.rglob('*.parallel-*'))


@pytest.mark.parametrize('consumer', ['models', 'neat'])
@pytest.mark.parametrize('endpoint', ['ignore', 'corrupt'])
def test_fallback_and_checksum_validation(tmp_path, server, consumer, endpoint):
    base, payload, _ = server
    folder = tmp_path / 'out'
    folder.mkdir()
    destination = folder / 'file'
    if consumer == 'neat':
        destination.write_bytes(b'old')
    with patch('sima_cli.models.client.PARALLEL_MIN_SIZE', 1), patch('sima_cli.vulcan.artifacts.PARALLEL_MIN_SIZE', 1):
        def run():
            if consumer == 'models':
                model_download(base, payload, destination, endpoint)
            else:
                neat_download(base, payload, folder, endpoint)
        if endpoint == 'corrupt':
            with pytest.raises((ModelRegistryError, VulcanArtifactError), match='SHA-256'):
                run()
            assert destination.read_bytes() == b'old' if consumer == 'neat' else not destination.exists()
        else:
            run()
            assert destination.read_bytes() == payload
    assert not list(tmp_path.rglob('*.parallel-*'))


def test_model_refreshes_expired_url_for_parallel_download(tmp_path, server):
    base, payload, state = server
    amounts = []
    with patch('sima_cli.models.client.PARALLEL_MIN_SIZE', 1):
        client = model_download(base, payload, tmp_path / 'model', initial={'download_url': base + '/expired'}, progress=amounts.append)
    client.artifact_download.assert_called_once()
    assert state['expired'] > 0
    assert state['ranges'] == 4
    assert sum(amounts) == len(payload)
    assert (tmp_path / 'model').read_bytes() == payload


def test_small_models_and_neat_share_sequential_limit(tmp_path, server):
    base, payload, state = server
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(model_download, base, payload, tmp_path / f'model{i}') for i in range(4)]
        futures += [pool.submit(neat_download, base, payload, tmp_path / f'neat{i}') for i in range(4)]
        for future in futures:
            future.result()
    assert state['ranges'] == 0
    assert state['maximum'] <= 4


@pytest.mark.parametrize('size,parallel_expected', [(64 * 1024**2 - 1, False), (64 * 1024**2, True)])
def test_model_parallel_threshold(tmp_path, size, parallel_expected):
    client = RegistryClient('http://example.invalid')
    with patch('sima_cli.models.client.download_parallel', return_value=True) as parallel, \
            patch.object(client, '_download_sequential') as sequential:
        client._download_once('http://example.invalid/file', tmp_path / 'part', size, 'a'*64)
    assert parallel.called == parallel_expected
    assert sequential.called != parallel_expected


def test_cancellation_while_waiting_for_connection(tmp_path):
    from concurrent.futures import CancelledError
    from contextlib import ExitStack
    from sima_cli.download.parallel import connection_slot, download_parallel
    from sima_cli.models.client import _DownloadCancellation
    cancellation = _DownloadCancellation()
    with ThreadPoolExecutor(max_workers=1) as pool:
        with ExitStack() as stack:
            for _ in range(4):
                stack.enter_context(connection_slot())
            future = pool.submit(download_parallel, 'http://example.invalid/file', tmp_path / 'part',
                                 64 * 1024**2, expected_sha='a'*64, cancellation=cancellation)
            cancellation.cancel()
            with pytest.raises(CancelledError):
                future.result(timeout=5)
    assert not list(tmp_path.iterdir())
    # Cancellation released every acquired permit.
    with ExitStack() as stack:
        for _ in range(4):
            stack.enter_context(connection_slot())
