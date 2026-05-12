import re
from typing import Iterable, Pattern, List

# Common noisy bits you mentioned; add your own here.
DEFAULT_NOISE_PATTERNS = [
    r"Information:\s+You may need to update /etc/fstab\.",   # fdisk/parted hint
    r"resize2fs\s+\d+\.\d+\.\d+\s*\([^)]+\)",                # e2fsprogs banner
    # add more as needed...
]

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

def strip_ansi(s: str) -> str:
    return _ANSI_RE.sub("", s)

class LineSquelcher:
    """Return False for lines we want to hide."""
    def __init__(self,
                 patterns: Iterable[str] = DEFAULT_NOISE_PATTERNS,
                 ignore_case: bool = True):
        flags = re.IGNORECASE if ignore_case else 0
        self._rx: List[Pattern[str]] = [re.compile(p, flags) for p in patterns]

    def allow(self, line: str) -> bool:
        clean = strip_ansi(line)
        return not any(rx.search(clean) for rx in self._rx)