"""
MCP server for sima-cli — exposes DevKit remote execution as MCP tools.

The server speaks the Model Context Protocol over stdio. Every tool is a thin
wrapper over the existing SSH helpers in ``sima_cli.update.remote`` and the
discovery helpers in ``sima_cli.discover.discover`` — so behaviour stays
identical to the regular CLI, just driven by a coding agent.

stdio discipline
----------------
On stdio, ``stdout`` is reserved for the JSON-RPC stream; anything else printed
there corrupts the protocol. The reused helpers occasionally print progress via
``click.echo`` / ``rich`` (which target ``stdout``), so each tool body runs
inside :func:`_quiet`, redirecting ``stdout`` to a throwaway buffer for the
duration of the call. This is safe because the stdio server only writes protocol
bytes between requests, never while a tool is executing.
"""

import contextlib
import io
import os
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from typing import Any, Dict, List

SERVER_NAME = "sima-devkit"

# Cap tool output so a chatty command can't blow up the agent's context window.
_MAX_OUTPUT_CHARS = 20_000

# The MCP server connects to the DevKit as this unprivileged user. The identity
# is pinned here (overridable via env) rather than inherited from
# sima_cli.update.remote's module defaults, so the agent-facing connection
# always runs as a non-root user — the connection-layer half of the no-sudo
# command-gating policy.
_USER_ENV = "SIMA_DEVKIT_USER"
_PASSWORD_ENV = "SIMA_DEVKIT_PASSWORD"
_KEY_ENV = "SIMA_DEVKIT_KEY"  # path to a private key; if set, used instead of password
_STRICT_HOST_KEY_ENV = "SIMA_DEVKIT_STRICT_HOST_KEY"
_DEFAULT_USER = "sima"
_DEFAULT_PASSWORD = "edgeai"
_CONNECT_TIMEOUT = 10
# Discovery probes every ARP entry, so a down board must fail fast rather than
# stalling the whole scan for the full connect timeout.
_DISCOVERY_CONNECT_TIMEOUT = 3


def _user() -> str:
    """Resolve the unprivileged DevKit SSH user (env override, else the default)."""
    return os.environ.get(_USER_ENV) or _DEFAULT_USER


def _password() -> str:
    """Resolve the DevKit SSH password (env override, else the default)."""
    return os.environ.get(_PASSWORD_ENV) or _DEFAULT_PASSWORD


def _strict_host_key() -> bool:
    """Whether to reject hosts not already in known_hosts (no trust-on-first-use)."""
    return (os.environ.get(_STRICT_HOST_KEY_ENV) or "").lower() in ("1", "true", "yes", "on")


def _connect(ip: str, timeout: float = _CONNECT_TIMEOUT):
    """Open an SSH session to the DevKit as the unprivileged MCP user.

    Host-key handling: known_hosts is loaded so a *changed* key on a host you've
    connected to before fails (MITM / unexpected reflash). First-seen hosts are
    trust-on-first-use by default; set ``SIMA_DEVKIT_STRICT_HOST_KEY=1`` to
    reject hosts that aren't already known.

    Auth: a key file in ``SIMA_DEVKIT_KEY`` is preferred; otherwise the
    password (``SIMA_DEVKIT_PASSWORD`` or the built-in default). Agent/key
    discovery is enabled only when a key is configured, so the password path
    can't silently fall back to an unrelated agent identity.
    """
    import paramiko

    ssh = paramiko.SSHClient()
    # Verify hosts we've already trusted; the policy below governs only new ones.
    ssh.load_system_host_keys()
    policy = paramiko.RejectPolicy() if _strict_host_key() else paramiko.AutoAddPolicy()
    ssh.set_missing_host_key_policy(policy)

    kwargs = {
        "username": _user(),
        "timeout": timeout,
        "banner_timeout": timeout,
        "auth_timeout": timeout,
    }
    key_file = os.environ.get(_KEY_ENV)
    if key_file:
        kwargs.update(key_filename=key_file, allow_agent=True, look_for_keys=True)
    else:
        kwargs.update(password=_password(), allow_agent=False, look_for_keys=False)

    ssh.connect(ip, **kwargs)
    return ssh


def _truncate(text: str) -> str:
    if text is None:
        return ""
    if len(text) <= _MAX_OUTPUT_CHARS:
        return text
    omitted = len(text) - _MAX_OUTPUT_CHARS
    return text[:_MAX_OUTPUT_CHARS] + f"\n…[truncated {omitted} chars]"


def _close(ssh) -> None:
    try:
        ssh.close()
    except Exception:  # noqa: BLE001
        pass


def _probe_board_info(ssh):
    """Probe board type/version/model/fwtype over an already-open SSH session.

    Mirrors ``sima_cli.update.remote.get_remote_board_info`` but reuses the
    caller's ``_connect`` session, so the unprivileged MCP identity is the only
    SSH login path. Returns ``(board_type, build_version, model, full_image, fwtype)``.
    """
    def _run(cmd: str) -> str:
        _, stdout, _ = ssh.exec_command(cmd)
        return stdout.read().decode("utf-8", errors="replace")

    build_output = _run("cat /etc/build 2>/dev/null || cat /etc/buildinfo 2>/dev/null")
    model_output = _run("cat /proc/device-tree/model 2>/dev/null || echo ''").strip()

    devkit_model = ""
    if model_output:
        if model_output.startswith("SiMa.ai "):
            model_output = model_output[len("SiMa.ai "):]
        devkit_model = model_output.replace(" Board", "").lower().replace(" ", "-")

    nvme_rc = _run(
        r"""PATH="$PATH:/usr/sbin:/sbin";
        command -v nvme >/dev/null 2>&1 ||
        which nvme >/dev/null 2>&1; echo $?"""
    ).strip()
    full_image = nvme_rc == "0"

    board_type = build_version = fwtype = ""
    for line in build_output.splitlines():
        line = line.strip()
        if line.startswith("MACHINE"):
            board_type = line.split("=", 1)[-1].strip()
        elif line.startswith("SIMA_BUILD_VERSION"):
            build_version = line.split("=", 1)[-1].strip()
        elif line.startswith("DISTRO "):
            fwtype = line.split("=", 1)[-1].strip()

    return board_type, build_version, devkit_model, full_image, fwtype


def _board_info_dict(ip: str, info) -> Dict[str, Any]:
    board, version, model, full_image, fwtype = info
    return {
        "ip": ip,
        "board_type": board,
        "build_version": version,
        "model": model,
        "full_image": full_image,
        "fwtype": fwtype,
    }


@contextlib.contextmanager
def _quiet():
    """Redirect stdout to a buffer so reused helpers can't corrupt the
    JSON-RPC stream."""
    with contextlib.redirect_stdout(io.StringIO()):
        yield


def build_server():
    """Construct and return the FastMCP server with all DevKit tools
    registered."""
    from mcp.server.fastmcp import FastMCP

    from sima_cli.update.remote import run_remote_command_capture
    from sima_cli.discover.discover import get_sima_devices_from_arp

    from sima_cli.mcp.safety import evaluate_command

    server = FastMCP(SERVER_NAME)

    @server.tool()
    def discover_devices() -> List[Dict[str, Any]]:
        """Discover SiMa DevKit boards on the local network and probe each
        over SSH.

        Finds boards via the ARP cache, then connects to each (as the
        unprivileged MCP user) to add board type/version/model. Each entry has
        ``ip``, ``mac`` and ``reachable``; reachable boards also include
        ``board_type``, ``build_version``, ``model``, ``full_image`` and
        ``fwtype``. Returns an empty list if none are found.
        """
        with _quiet():
            found = get_sima_devices_from_arp() or []
            devices: List[Dict[str, Any]] = []
            for entry in found:
                ip = entry.get("ip")
                device: Dict[str, Any] = {"ip": ip, "mac": entry.get("mac")}
                try:
                    ssh = _connect(ip, timeout=_DISCOVERY_CONNECT_TIMEOUT)
                    try:
                        info = _probe_board_info(ssh)
                    finally:
                        _close(ssh)
                except Exception: # noqa: BLE001 - unreachable/booting board
                    device["reachable"] = False
                else:
                    device["reachable"] = True
                    device.update(_board_info_dict(ip, info))
                devices.append(device)
        return devices

    @server.tool()
    def get_board_info(ip: str) -> Dict[str, Any]:
        """Query a DevKit for its board type, build version, model and firmware type.

        Args:
            ip: IP address of the DevKit board.
        """
        with _quiet():
            try:
                ssh = _connect(ip)
            except Exception as e:  # noqa: BLE001 - board unreachable or still booting
                result = _board_info_dict(ip, ("", "", "", False, ""))
                result["reachable"] = False
                result["error"] = f"failed to connect to {ip}: {e}"
                return result
            try:
                info = _probe_board_info(ssh)
            finally:
                _close(ssh)
        result = _board_info_dict(ip, info)
        result["reachable"] = True
        return result

    @server.tool()
    def run_command(
        ip: str,
        command: str,
        timeout: int = 60,
    ) -> Dict[str, Any]:
        """Run a shell command on a DevKit over SSH and return its result.

        For safety the command is screened before execution and rejected if it
        attempts privilege escalation (sudo/su), recursive or wildcard deletion
        (rm -r / rm *), writes to /sys, /proc or /dev, or other clearly
        destructive operations. Run commands as the unprivileged DevKit user.

        Args:
            ip: IP address of the DevKit board.
            command: The shell command to execute on the board.
            timeout: Maximum seconds to wait before abandoning the command.

        Returns:
            ``{exit_code, stdout, stderr, timed_out, blocked}``. ``exit_code`` is
            -1 when the command was blocked by policy, timed out, or the
            connection failed; ``blocked`` is True only for policy rejections.
        """
        cmd = command.strip()

        allowed, reason = evaluate_command(cmd)
        if not allowed:
            return {
                "exit_code": -1,
                "stdout": "",
                "stderr": f"blocked by sima-cli MCP policy: {reason}",
                "timed_out": False,
                "blocked": True,
            }

        with _quiet():
            try:
                ssh = _connect(ip)
            except Exception as e:  # noqa: BLE001 - surface connection errors to the agent
                return {
                    "exit_code": -1,
                    "stdout": "",
                    "stderr": f"failed to connect to {ip}: {e}",
                    "timed_out": False,
                    "blocked": False,
                }

            pool = ThreadPoolExecutor(max_workers=1)
            try:
                future = pool.submit(run_remote_command_capture, ssh, cmd, _password())
                try:
                    code, out, err = future.result(timeout=timeout)
                except FuturesTimeout:
                    # Closing the transport unblocks the worker's blocking read so
                    # the thread can exit; don't wait on it (the command is hung).
                    _close(ssh)
                    pool.shutdown(wait=False)
                    return {
                        "exit_code": -1,
                        "stdout": "",
                        "stderr": f"command timed out after {timeout}s",
                        "timed_out": True,
                        "blocked": False,
                    }
                pool.shutdown(wait=False)
            finally:
                _close(ssh)

        return {
            "exit_code": code,
            "stdout": _truncate(out),
            "stderr": _truncate(err),
            "timed_out": False,
            "blocked": False,
        }

    return server
