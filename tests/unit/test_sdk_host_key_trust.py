"""Regression coverage for SDK pairing trust and credential handling."""
import os
import subprocess
from unittest.mock import Mock, patch

import pytest

from sima_cli.sdk.utils import bootstrap_devkit_container


def bootstrap_script(port=22):
    result = Mock(returncode=0, stdout="", stderr="")
    with patch("sima_cli.sdk.utils.subprocess.run", return_value=result) as run:
        bootstrap_devkit_container("sdk", {
            "devkit_ip": "192.0.2.10", "devkit_port": port,
            "devkit_password": "test", "noninteractive": True,
        })
    return run.call_args.args[0][-1]


@pytest.mark.parametrize("port", [22, 2222])
def test_pairing_uses_first_use_trust_without_resetting_saved_keys(port):
    script = bootstrap_script(port)
    assert "StrictHostKeyChecking=accept-new" in script
    assert "ssh-keyscan" not in script
    assert "ssh-keygen -R" not in script
    assert "host_key_verification_required" not in script
    assert "ssh-keygen -F" not in script
    assert f'-p {port}' in script
    subprocess.run(["bash", "-n"], input=script, text=True, check=True)


@pytest.mark.parametrize("mode", ["noninteractive", "interactive", "interactive_retry"])
def test_custom_password_never_appears_in_docker_arguments(mode):
    secret = "custom password ' with $shell; syntax\nand newline"
    success = Mock(returncode=0, stdout="", stderr="")
    results = [success]
    if mode == "interactive_retry":
        results = [Mock(returncode=1, stdout="__SIMA_DEVKIT_BOOTSTRAP_STATUS=source_failed\n", stderr="permission denied"), success]
    with patch("sima_cli.sdk.utils.subprocess.run", side_effect=results) as run, \
         patch("sima_cli.sdk.utils.sys.stdin.isatty", return_value=True), \
         patch("sima_cli.sdk.utils.sys.stdout.isatty", return_value=True):
        bootstrap_devkit_container("sdk", {
            "devkit_ip": "192.0.2.10", "devkit_password": secret,
            "bootstrap_interactive": mode == "interactive",
            "noninteractive": mode == "noninteractive",
        })
    assert run.call_count == len(results)
    for call in run.call_args_list:
        argv = call.args[0]
        assert all(secret not in arg for arg in argv)
        assert argv[argv.index("--env") + 1] == "DEVKIT_SYNC_PASSWORD"
        assert call.kwargs["env"]["DEVKIT_SYNC_PASSWORD"] == secret
    assert os.environ.get("DEVKIT_SYNC_PASSWORD") != secret


def test_openssh_accepts_new_and_saved_keys_but_rejects_changed_key(tmp_path):
    """Use real SSH handshakes to check first-use and mismatch behavior."""
    import shutil
    import socket
    import threading

    import paramiko

    if not shutil.which("ssh"):
        pytest.skip("OpenSSH client is required")

    class Server(paramiko.ServerInterface):
        def __init__(self):
            self.auth_attempted = False

        def check_auth_none(self, username):
            self.auth_attempted = True
            return paramiko.AUTH_FAILED

    key = paramiko.RSAKey.generate(2048)
    replacement = paramiko.RSAKey.generate(2048)
    known_hosts = tmp_path / "known_hosts"
    # Extract the actual pairing policy, so this also catches policy regressions.
    import re
    policy = re.search(r"StrictHostKeyChecking=(\S+)", bootstrap_script()).group(1)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(10)
        port = listener.getsockname()[1]
        for server_key, expect_auth in [(key, True), (key, True), (replacement, False)]:
            server = Server()
            done = threading.Event()
            errors = []

            def serve():
                try:
                    conn, _ = listener.accept()
                    with paramiko.Transport(conn) as transport:
                        transport.add_server_key(server_key)
                        transport.start_server(server=server)
                        done.wait(10)
                except (EOFError, ConnectionResetError):
                    pass  # The client closes immediately on host-key rejection.
                except Exception as exc:
                    errors.append(exc)

            thread = threading.Thread(target=serve, daemon=True)
            thread.start()
            before = known_hosts.read_bytes() if known_hosts.exists() else None
            try:
                result = subprocess.run([
                    "ssh", "-F", os.devnull, "-p", str(port),
                    "-o", f"StrictHostKeyChecking={policy}",
                    "-o", f"UserKnownHostsFile={known_hosts}",
                    "-o", f"GlobalKnownHostsFile={os.devnull}",
                    "-o", "HashKnownHosts=yes", "-o", "BatchMode=yes",
                    "-o", "PreferredAuthentications=none", "-o", "ConnectTimeout=5",
                    "test@127.0.0.1", "true",
                ], capture_output=True, text=True, timeout=10)
            finally:
                done.set()
                thread.join(10)
            assert not thread.is_alive()
            assert not errors
            assert server.auth_attempted == expect_auth, result.stderr
            assert known_hosts.exists()
            if before is not None:
                assert known_hosts.read_bytes() == before
            if not expect_auth:
                assert result.returncode != 0
                assert "REMOTE HOST IDENTIFICATION HAS CHANGED" in result.stderr
