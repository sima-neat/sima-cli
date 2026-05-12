import shutil

def check_disk_space(required_bytes: int, folder: str = ".") -> bool:
    """
    Check if the given folder has enough free disk space.

    Args:
        required_bytes (int): Space required in bytes
        folder (str): Path to check (default: current dir)

    Returns:
        bool: True if enough space is available, False otherwise
    """
    total, used, free = shutil.disk_usage(folder)
    return free >= required_bytes
