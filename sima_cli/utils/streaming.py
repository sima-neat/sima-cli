"""Bounded, sequential artifact writes with atomic publication."""
import hashlib
import os
from pathlib import Path

DOWNLOAD_CHUNK_SIZE = 1024 * 1024


def publish_partial(partial, destination):
    """Publish a flushed file; persist the rename on POSIX filesystems."""
    os.replace(partial, destination)
    if os.name == "posix":
        fd = os.open(str(Path(destination).parent), os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def write_stream(chunks, destination, expected_size=None, expected_sha=""):
    """Hash while writing, retaining an existing destination on failure."""
    destination = Path(destination)
    partial = destination.with_name(destination.name + ".part")
    digest = hashlib.sha256()
    size = 0
    try:
        with partial.open("wb") as output:
            for chunk in chunks:
                if not chunk:
                    continue
                output.write(chunk)
                digest.update(chunk)
                size += len(chunk)
                if expected_size is not None and size > expected_size:
                    raise ValueError("Downloaded artifact exceeds expected size")
            if expected_size is not None and size != expected_size:
                raise ValueError("Downloaded artifact size mismatch")
            if expected_sha and digest.hexdigest().lower() != expected_sha.lower():
                raise ValueError("Downloaded artifact SHA256 mismatch")
            output.flush()
            os.fsync(output.fileno())
        publish_partial(partial, destination)
    finally:
        partial.unlink(missing_ok=True)
