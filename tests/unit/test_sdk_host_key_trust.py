"""Exercise pairing's trust gate with real OpenSSH known_hosts lookups."""
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
@pytest.mark.parametrize("trusted", [False, True])
def test_trust_gate_preserves_hashed_keys_and_blocks_unknown_hosts(tmp_path, port, trusted):
    ssh_dir = tmp_path / ".ssh"
    ssh_dir.mkdir()
    key = ssh_dir / "fixture"
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True)
    identity = "192.0.2.10" if port == 22 else "[192.0.2.10]:2222"
    # A key for another port must not authorize this endpoint.
    host = identity if trusted else "[192.0.2.10]:2200"
    known_hosts = ssh_dir / "known_hosts"
    known_hosts.write_text(f"{host} {key.with_suffix('.pub').read_text()}")
    subprocess.run(["ssh-keygen", "-H", "-f", str(known_hosts)], capture_output=True, check=True)
    before = known_hosts.read_bytes()
    script = bootstrap_script(port)
    gate = script[script.index("    # Never replace saved trust"):script.index("    if ! SSHPASS=")]
    result = subprocess.run(["bash", "-c", gate], env={**os.environ, "HOME": str(tmp_path)}, capture_output=True, text=True)
    assert result.returncode == (0 if trusted else 42)
    assert known_hosts.read_bytes() == before
    assert "ssh-keyscan" not in script
    assert "ssh-keygen -R" not in script
    assert "StrictHostKeyChecking=yes" in script
    assert script.index("exit 42") < script.index("sshpass -e ssh-copy-id")


@pytest.mark.parametrize("interactive", [False, True])
def test_unknown_key_requires_verification_without_password_retry(interactive):
    result = Mock(returncode=42, stdout="__SIMA_DEVKIT_BOOTSTRAP_STATUS=host_key_verification_required\n", stderr="")
    with patch("sima_cli.sdk.utils.subprocess.run", return_value=result), \
         patch("sima_cli.sdk.utils.sys.stdin.isatty", return_value=True), \
         patch("sima_cli.sdk.utils.sys.stdout.isatty", return_value=True), \
         patch("sima_cli.sdk.utils.getpass.getpass") as password:
        with pytest.raises(RuntimeError, match="trusted channel") as error:
            bootstrap_devkit_container("sdk", {
                "devkit_ip": "192.0.2.10", "devkit_port": 2222,
                "devkit_password": "test", "bootstrap_interactive": interactive,
                "noninteractive": not interactive,
            })
    password.assert_not_called()
    assert "StrictHostKeyChecking=ask" in str(error.value)
    assert "PreferredAuthentications=none" in str(error.value)
    assert "-p 2222" in str(error.value)


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
