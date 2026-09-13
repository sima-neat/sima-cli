"""Host staging accessible to Docker clients, including Snap Docker."""
import os
import tempfile


def docker_cp_staging_dir():
    """
    Docker installed through Snap may not see host /tmp paths. Stage files under
    a non-hidden user home directory so docker cp can access them across Docker
    variants, including Snap confinement.
    """
    home = os.path.expanduser("~")
    if home and os.path.isdir(home) and os.access(home, os.W_OK):
        staging = tempfile.TemporaryDirectory(prefix="sima-cli-sdk-", dir=home)
        try:
            os.chmod(staging.name, 0o755)
        except OSError:
            staging.cleanup()
            raise
        return staging
    return tempfile.TemporaryDirectory(prefix="sima-cli-sdk-")

