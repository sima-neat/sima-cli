"""Bounded, sequential artifact writes with atomic publication."""
import hashlib
from contextlib import suppress
import os
import shutil
import tempfile
from pathlib import Path

DOWNLOAD_CHUNK_SIZE = 1024 * 1024


def publish_partial(partial, destination):
    """Publish durably, restoring the previous destination on sync failure."""
    destination = Path(destination)
    if os.name != "posix":
        os.replace(partial, destination)
        return

    # Open before renaming so even an unsupported directory open is harmless.
    fd = os.open(str(destination.parent), os.O_RDONLY)
    backup_dir = None
    backup = None
    retain_backup = False
    try:
        if os.path.lexists(destination):
            backup_dir = Path(tempfile.mkdtemp(prefix=".sima-download-", dir=destination.parent))
            backup = backup_dir / "previous"
            try:
                # A hard link retains the old inode without copying large artifacts.
                os.link(destination, backup, follow_symlinks=False)
            except OSError:
                shutil.copy2(destination, backup, follow_symlinks=False)
        os.replace(partial, destination)
        try:
            os.fsync(fd)
        except OSError as sync_error:
            try:
                if backup is not None:
                    os.replace(backup, destination)
                else:
                    destination.unlink()
            except OSError as rollback_error:
                retain_backup = backup is not None
                recovery = f" Previous file retained at {backup}." if retain_backup else ""
                raise OSError(
                    f"Directory sync failed and publication rollback failed: {rollback_error}.{recovery}"
                ) from sync_error
            # The filesystem may reject directory sync altogether. The old
            # pathname is restored, but crash durability cannot be guaranteed.
            raise
    finally:
        # Cleanup must not turn a committed publication into a failed download.
        with suppress(OSError):
            os.close(fd)
        if backup_dir is not None and not retain_backup:
            with suppress(OSError):
                shutil.rmtree(backup_dir)


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
