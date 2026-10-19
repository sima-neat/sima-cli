"""Bounded parallel artifact downloads with validation before publication."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, nullcontext
import hashlib
from copy import copy
import os
from pathlib import Path
import re
import tempfile
from threading import Event, BoundedSemaphore, Lock

import requests
from tqdm import tqdm

from sima_cli.utils.streaming import DOWNLOAD_CHUNK_SIZE, publish_partial

PARALLEL_MIN_SIZE = 64 * 1024 * 1024
DOWNLOAD_WORKERS = 4
_CONNECTIONS = BoundedSemaphore(DOWNLOAD_WORKERS)


@contextmanager
def connection_slot(cancellation=None):
    """Bound active artifact responses across files and range workers."""
    while not _CONNECTIONS.acquire(timeout=0.1):
        if cancellation is not None:
            cancellation.check()
    try:
        if cancellation is not None:
            cancellation.check()
        yield
    finally:
        _CONNECTIONS.release()


@contextmanager
def worker_session(session):
    if session is None:
        with requests.Session() as client:
            client.trust_env = False
            yield client
    else:
        # Preserve auth, proxy/TLS settings and retry adapters, but isolate
        # mutable cookies and headers. Shared adapters belong to the caller.
        client = copy(session)
        client.cookies = copy(session.cookies)
        client.headers = session.headers.copy()
        yield client


class RangeUnsupported(Exception):
    """The server ignored a conditional range; retry sequentially."""


def download_parallel(url, destination, size, etag="", *, expected_sha="",
                      session=None, timeout=30, progress=None, cancellation=None):
    """Publish only after ranges match size and an ETag or expected SHA-256.

    Parallel scratch files never use the sequential resume filename: a sparse
    or incomplete range must never be mistaken for a contiguous valid prefix.
    Each worker owns its session and file handle. Failed attempts remove their
    scratch file after all workers have stopped, preserving any previous file.
    """
    if not etag and not expected_sha:
        return False
    destination = Path(destination)
    reported = 0
    progress_lock = Lock()

    def report(amount):
        nonlocal reported
        with progress_lock:
            reported += amount
            if progress is not None:
                progress(amount)

    stopped = Event()
    fd, scratch = tempfile.mkstemp(prefix=destination.name + '.parallel-', dir=destination.parent)
    os.close(fd)
    try:
        with tqdm(desc=f'⬇️  Downloading {destination.name}', total=size,
                  unit='B', unit_scale=True, unit_divisor=1024, disable=progress is not None) as bar:
            def fetch(index):
                start = size * index // DOWNLOAD_WORKERS
                end = size * (index + 1) // DOWNLOAD_WORKERS - 1
                try:
                    with connection_slot(cancellation), worker_session(session) as client:
                        if stopped.is_set():
                            return
                        headers = {'Accept-Encoding': 'identity', 'Range': f'bytes={start}-{end}'}
                        if etag:
                            headers['If-Match'] = etag
                        with client.get(url, stream=True, timeout=timeout, headers=headers) as response, (
                            cancellation.track(response) if cancellation is not None else nullcontext()
                        ):
                            if response.status_code in (401, 403):
                                raise PermissionError('The artifact download URL expired or was rejected.')
                            response.raise_for_status()
                            if response.status_code == 200:
                                raise RangeUnsupported()
                            match = re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)', response.headers.get('Content-Range', ''))
                            length = response.headers.get('Content-Length')
                            if (response.status_code != 206 or not match
                                    or tuple(map(int, match.groups())) != (start, end, size)
                                    or (etag and response.headers.get('ETag') != etag)
                                    or response.headers.get('Content-Encoding', 'identity').lower() != 'identity'
                                    or (length is not None and int(length) != end - start + 1)):
                                raise ValueError('Inconsistent parallel download response')
                            written = 0
                            with open(scratch, 'r+b') as output:
                                output.seek(start)
                                for chunk in response.iter_content(chunk_size=DOWNLOAD_CHUNK_SIZE):
                                    if cancellation is not None:
                                        cancellation.check()
                                    if stopped.is_set():
                                        return
                                    if not chunk:
                                        continue
                                    written += len(chunk)
                                    if written > end - start + 1:
                                        raise ValueError('Parallel download exceeds expected range size')
                                    output.write(chunk)
                                    with bar.get_lock():
                                        bar.update(len(chunk))
                                    report(len(chunk))
                                if written != end - start + 1:
                                    raise ValueError('Parallel download range size mismatch')
                except BaseException:
                    stopped.set()
                    raise

            with ThreadPoolExecutor(max_workers=DOWNLOAD_WORKERS) as pool:
                futures = [pool.submit(fetch, index) for index in range(DOWNLOAD_WORKERS)]
                try:
                    for future in futures:
                        future.result()
                except BaseException:
                    stopped.set()
                    raise
        with open(scratch, 'r+b') as output:
            if os.fstat(output.fileno()).st_size != size:
                raise ValueError('Parallel download size mismatch')
            os.fsync(output.fileno())
        if expected_sha:
            digest = hashlib.sha256()
            with open(scratch, 'rb') as source:
                for chunk in iter(lambda: source.read(DOWNLOAD_CHUNK_SIZE), b''):
                    if cancellation is not None:
                        cancellation.check()
                    digest.update(chunk)
            if digest.hexdigest().lower() != expected_sha.lower():
                raise ValueError('Downloaded artifact SHA-256 mismatch')
        if cancellation is not None:
            cancellation.check()
        publish_partial(scratch, destination)
        return True
    except RangeUnsupported:
        return False
    finally:
        if Path(scratch).exists() and reported:
            report(-reported)
        Path(scratch).unlink(missing_ok=True)
