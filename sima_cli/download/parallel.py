"""Four bounded range requests for immutable public mirror artifacts."""
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import re
import tempfile
from threading import Event

import requests
from tqdm import tqdm

from sima_cli.utils.streaming import DOWNLOAD_CHUNK_SIZE, publish_partial

PARALLEL_MIN_SIZE = 64 * 1024 * 1024
DOWNLOAD_WORKERS = 4


class RangeUnsupported(Exception):
    """The server ignored a conditional range; retry sequentially."""


def download_parallel(url, destination, size, etag):
    """Publish only after all ranges match the HEAD validator and exact size.

    Parallel scratch files never use the sequential resume filename: a sparse
    or incomplete range must never be mistaken for a contiguous valid prefix.
    Each worker owns its session and file handle. Failed attempts remove their
    scratch file after all workers have stopped, preserving any previous file.
    """
    destination = Path(destination)
    stopped = Event()
    fd, scratch = tempfile.mkstemp(prefix=destination.name + '.parallel-', dir=destination.parent)
    os.close(fd)
    try:
        with tqdm(desc=f'⬇️  Downloading {destination.name}', total=size,
                  unit='B', unit_scale=True, unit_divisor=1024) as bar:
            def fetch(index):
                start = size * index // DOWNLOAD_WORKERS
                end = size * (index + 1) // DOWNLOAD_WORKERS - 1
                try:
                    with requests.Session() as session:
                        session.trust_env = False
                        with session.get(url, stream=True, timeout=30, headers={
                            'Accept-Encoding': 'identity', 'Range': f'bytes={start}-{end}',
                            'If-Match': etag,
                        }) as response:
                            response.raise_for_status()
                            if response.status_code == 200:
                                raise RangeUnsupported()
                            match = re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)', response.headers.get('Content-Range', ''))
                            length = response.headers.get('Content-Length')
                            if (response.status_code != 206 or not match
                                    or tuple(map(int, match.groups())) != (start, end, size)
                                    or response.headers.get('ETag') != etag
                                    or response.headers.get('Content-Encoding', 'identity').lower() != 'identity'
                                    or (length is not None and int(length) != end - start + 1)):
                                raise ValueError('Inconsistent parallel download response')
                            written = 0
                            with open(scratch, 'r+b') as output:
                                output.seek(start)
                                for chunk in response.iter_content(chunk_size=DOWNLOAD_CHUNK_SIZE):
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
        publish_partial(scratch, destination)
        return True
    except RangeUnsupported:
        return False
    finally:
        Path(scratch).unlink(missing_ok=True)
