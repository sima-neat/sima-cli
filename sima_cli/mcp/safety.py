"""
Command gating for the sima-cli MCP server.

A coding agent driving ``run_command`` gets a real shell on the DevKit, so we
screen every command string *before* connecting and reject the dangerous ones.
The policy enforced here:

  * No privilege escalation — ``sudo`` / ``su`` / ``doas`` / ``pkexec``.
  * No recursive deletion (``rm -r``) and no wildcard/critical-path deletion
    (e.g. ``rm -rf *``, ``rm /etc``).
  * No writes to system paths (``/sys``, ``/proc``, ``/dev``, ``/etc``,
    ``/boot``, ``/bin``, ``/usr``, ``/lib``, ``/root``) via redirection,
    ``tee``, ``dd of=`` or ``sysctl``.
  * A set of unambiguously destructive commands: ``mkfs*``, ``dd``, ``shred``,
    ``wipefs``, ``fdisk``/``sfdisk``/``parted``, ``blkdiscard``, ``truncate``,
    ``find … -delete``/``-exec``, and fork bombs.
  * Shells invoked without an inline, screenable ``-c`` command (e.g.
    ``echo … | sh``, ``bash script.sh``) — their payload can't be inspected.

This is best-effort static screening, **not a sandbox**: a determined caller
can still obfuscate intent (variable indirection such as ``X=rm; $X -rf /``,
base64/eval payloads, or interpreters like ``python -c``). It stops the common
and accidental destructive cases an agent is likely to emit; real enforcement
(unprivileged SSH user, read-only mounts, restricted shell) belongs on the
device.

``evaluate_command(cmd)`` returns ``(allowed: bool, reason: Optional[str])``.
"""

import os
import re
import shlex
from typing import List, Optional, Tuple

# Paths that must never be the target of a delete.
CRITICAL_DELETE_PATHS = {
    "/", "/sys", "/proc", "/dev", "/etc", "/boot", "/bin", "/sbin",
    "/usr", "/lib", "/lib64", "/var", "/home", "/root", "/opt",
}

# Privilege escalation as a standalone word (not part of e.g. "subdir").
_ROOT_ESCALATION = re.compile(r"(?<![\w./-])(?:sudo|su|doas|pkexec)(?![\w-])")

# System paths that must never be written to (redirection / tee / dd of=).
_NO_WRITE = (
    r"(?:/sys|/proc|/dev|/etc|/boot|/bin|/sbin|/usr|/lib|/lib64|/root)(?:/|\b)"
)
_REDIRECT_TO_PROTECTED = re.compile(r">>?\s*['\"]?" + _NO_WRITE)
_TEE_TO_PROTECTED = re.compile(r"\btee\b[^|;&\n]*?['\"]?" + _NO_WRITE)
_DD_TO_PROTECTED = re.compile(r"\bdd\b[^|;&\n]*?\bof=\s*['\"]?" + _NO_WRITE)
# `sysctl -w key=val` or `sysctl key=val` both write kernel params.
_SYSCTL_WRITE = re.compile(r"\bsysctl\b[^|;&\n]*?(?:\s-w\b|\S=)")

# Fork bomb:  :(){ :|:& };:
_FORK_BOMB = re.compile(r"\(\s*\)\s*\{[^}]*\|")

# Shell operators that separate independent commands.
_SEGMENT_SPLIT = re.compile(r"\|\||&&|;|\||\n|&")

# Shells whose ``-c '<cmd>'`` argument we screen recursively; invoked any other
# way (piped-in script, script file) the payload can't be inspected → blocked.
_SHELLS = {"sh", "bash", "dash", "zsh", "ash", "ksh"}

# Commands that are destructive regardless of arguments.
_DESTRUCTIVE_COMMANDS = {
    "dd": "raw disk/device writes (dd)",
    "shred": "secure erase (shred)",
    "wipefs": "filesystem-signature wipe (wipefs)",
    "mke2fs": "filesystem creation (mke2fs)",
    "mkswap": "swap creation (mkswap)",
    "fdisk": "disk partitioning (fdisk)",
    "sfdisk": "disk partitioning (sfdisk)",
    "parted": "disk partitioning (parted)",
    "blkdiscard": "block discard (blkdiscard)",
    "truncate": "file truncation (truncate)",
}


def _is_critical_delete_target(target: str) -> bool:
    norm = target.rstrip("/") or "/"
    if norm in CRITICAL_DELETE_PATHS:
        return True
    # Deleting the contents of a critical dir, e.g. "/etc/*", "/sys/."
    for crit in CRITICAL_DELETE_PATHS:
        if crit == "/":
            continue
        if target == crit + "/*" or target.startswith(crit + "/"):
            return True
    return False


def _check_rm(args: List[str]) -> Optional[str]:
    recursive = False
    targets: List[str] = []
    for arg in args:
        if arg == "--":
            continue
        if arg.startswith("--"):
            if arg in ("--recursive", "--dir"):
                recursive = True
            continue
        if arg.startswith("-") and len(arg) > 1:
            letters = arg[1:]
            if "r" in letters or "R" in letters or "d" in letters:
                recursive = True
            continue
        targets.append(arg)

    if recursive:
        return "recursive deletion (rm -r) is not permitted"
    for target in targets:
        if "*" in target or "?" in target:
            return f"wildcard deletion ('{target}') is not permitted"
        if _is_critical_delete_target(target):
            return f"deleting a system path ('{target}') is not permitted"
    return None


def _strip_command_prefixes(tokens: List[str]) -> List[str]:
    """Drop leading ``VAR=val`` assignments and wrappers like ``env``/``command``."""
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tok):
            i += 1
            continue
        if tok in ("env", "command", "nice", "ionice", "nohup", "stdbuf"):
            i += 1
            continue
        break
    return tokens[i:]


def _check_segment(segment: str) -> Optional[str]:
    segment = segment.strip()
    if not segment:
        return None
    try:
        tokens = shlex.split(segment)
    except ValueError:
        tokens = segment.split()
    tokens = _strip_command_prefixes(tokens)
    if not tokens:
        return None

    name = os.path.basename(tokens[0])
    if name in _SHELLS:
        # Screen the literal argument of `sh -c '<cmd>'`; any other invocation
        # (piped-in script, script file) carries a payload we can't inspect.
        for idx in range(1, len(tokens) - 1):
            if tokens[idx] == "-c":
                allowed, reason = evaluate_command(tokens[idx + 1])
                return None if allowed else reason
        return f"invoking '{name}' without an inline -c command can't be screened"
    if name == "rm":
        return _check_rm(tokens[1:])
    if name == "find":
        return _check_find(tokens[1:])
    if name == "mkfs" or name.startswith("mkfs."):
        return f"filesystem creation ('{name}') is not permitted"
    if name in _DESTRUCTIVE_COMMANDS:
        return f"{_DESTRUCTIVE_COMMANDS[name]} is not permitted"
    return None


def _check_find(args: List[str]) -> Optional[str]:
    # `find … -delete` and `-exec/-execdir/-ok` can delete or run arbitrary cmds.
    for arg in args:
        if arg in ("-delete", "-exec", "-execdir", "-ok", "-okdir"):
            return f"find with '{arg}' is not permitted"
    return None


def _expand_substitutions(command: str) -> List[str]:
    """Return inner text of ``$(...)`` and backtick substitutions for screening."""
    inner = re.findall(r"\$\(([^()]*)\)", command)
    inner += re.findall(r"`([^`]*)`", command)
    return inner


def evaluate_command(command: str) -> Tuple[bool, Optional[str]]:
    """Return ``(allowed, reason)`` for a command about to run on the DevKit."""
    cmd = (command or "").strip()
    if not cmd:
        return True, None

    # Screen the command itself plus anything inside command substitutions.
    to_scan = [cmd] + _expand_substitutions(cmd)
    for chunk in to_scan:
        if _ROOT_ESCALATION.search(chunk):
            return False, "privilege escalation (sudo/su/doas/pkexec) is not permitted"
        if (
            _REDIRECT_TO_PROTECTED.search(chunk)
            or _TEE_TO_PROTECTED.search(chunk)
            or _DD_TO_PROTECTED.search(chunk)
            or _SYSCTL_WRITE.search(chunk)
        ):
            return False, "writing to a system path (/sys, /proc, /dev, /etc, …) is not permitted"
        if _FORK_BOMB.search(chunk):
            return False, "fork-bomb pattern is not permitted"

        for segment in _SEGMENT_SPLIT.split(chunk):
            reason = _check_segment(segment)
            if reason:
                return False, reason

    return True, None
