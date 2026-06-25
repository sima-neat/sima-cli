"""Tests for the docker-startup / docker-group fix in sima_cli.utils.docker.

Covers:
  - `_try_reexec_with_docker_group` (preflighted `sg docker` retry)
  - `_handle_docker_socket_permission_denied` (group-already-set vs. need-to-add,
    accept / decline, usermod success / failure)
  - The Linux branch of `check_and_start_docker`: systemctl return-code is
    honoured, the daemon-up-but-socket-denied detection routes to the group
    flow, and `time.sleep` is not hit in tests.
"""

import subprocess
import unittest
from unittest.mock import patch, MagicMock

from sima_cli.utils import docker


class TestTryReexecWithDockerGroup(unittest.TestCase):
    def test_sentinel_short_circuits(self):
        with patch.dict(docker.os.environ, {docker._REEXEC_ENV_FLAG: "1"}, clear=False), \
             patch("sima_cli.utils.docker.shutil.which") as which, \
             patch("sima_cli.utils.docker.subprocess.run") as run, \
             patch("sima_cli.utils.docker.os.execvpe") as execvpe:
            docker._try_reexec_with_docker_group()

        which.assert_not_called()
        run.assert_not_called()
        execvpe.assert_not_called()

    def test_probe_failure_falls_back_without_exec(self):
        probe_result = MagicMock(returncode=1)
        with patch.dict(docker.os.environ, {}, clear=False), \
             patch("sima_cli.utils.docker.shutil.which", return_value="/usr/bin/sg"), \
             patch("sima_cli.utils.docker.subprocess.run", return_value=probe_result) as run, \
             patch("sima_cli.utils.docker.os.execvpe") as execvpe:
            docker.os.environ.pop(docker._REEXEC_ENV_FLAG, None)
            docker._try_reexec_with_docker_group()

        run.assert_called_once()
        execvpe.assert_not_called()

    def test_probe_success_execs_original_command_with_sentinel(self):
        argv = ["sima-cli", "sdk", "setup", "--devkit", "192.168.135.108"]
        probe_result = MagicMock(returncode=0)
        captured = {}

        def fake_execvpe(file, args, env):
            captured["file"] = file
            captured["args"] = args
            captured["env"] = env

        with patch.dict(docker.os.environ, {"PATH": "/usr/bin"}, clear=False), \
             patch("sima_cli.utils.docker.shutil.which", return_value="/usr/bin/sg"), \
             patch("sima_cli.utils.docker.subprocess.run", return_value=probe_result), \
             patch("sima_cli.utils.docker.sys.argv", argv), \
             patch("sima_cli.utils.docker.os.execvpe", side_effect=fake_execvpe):
            docker.os.environ.pop(docker._REEXEC_ENV_FLAG, None)
            docker._try_reexec_with_docker_group()

        self.assertEqual(captured["file"], "sg")
        self.assertEqual(captured["args"][0:3], ["sg", "docker", "-c"])
        self.assertIn("sima-cli", captured["args"][3])
        self.assertIn("192.168.135.108", captured["args"][3])
        self.assertEqual(captured["env"].get(docker._REEXEC_ENV_FLAG), "1")


class TestDockerGroupRefreshInstructions(unittest.TestCase):
    def test_print_refresh_instructions_include_newgrp_and_original_command(self):
        argv = ["sima-cli", "sdk", "setup", "--devkit", "192.168.135.108"]
        with patch("builtins.print") as print_mock, \
             patch("sima_cli.utils.docker.sys.argv", argv), \
             patch("sima_cli.utils.docker.shlex.join", return_value="sima-cli sdk setup --devkit 192.168.135.108"):
            docker._print_docker_group_refresh_instructions()

        printed = "\n".join(call.args[0] for call in print_mock.call_args_list)
        self.assertIn("newgrp docker", printed)
        self.assertIn("sima-cli sdk setup --devkit 192.168.135.108", printed)


class TestHandleDockerSocketPermissionDenied(unittest.TestCase):
    def test_persisted_group_but_not_active_session_exits_with_newgrp_instructions(self):
        with patch("sima_cli.utils.docker._current_user", return_value="alice"), \
             patch("sima_cli.utils.docker._user_in_docker_group", return_value=True), \
             patch("sima_cli.utils.docker._active_user_in_docker_group", return_value=False), \
             patch("sima_cli.utils.docker._try_reexec_with_docker_group") as reexec, \
             patch("sima_cli.utils.docker._print_docker_group_refresh_instructions") as instructions:
            with self.assertRaises(SystemExit) as cm:
                docker._handle_docker_socket_permission_denied()

        reexec.assert_called_once_with()
        instructions.assert_called_once_with()
        self.assertEqual(cm.exception.code, 1)

    def test_active_group_but_socket_still_denied_exits_with_socket_diagnostics(self):
        with patch("sima_cli.utils.docker._current_user", return_value="alice"), \
             patch("sima_cli.utils.docker._user_in_docker_group", return_value=True), \
             patch("sima_cli.utils.docker._active_user_in_docker_group", return_value=True), \
             patch("sima_cli.utils.docker._print_docker_group_refresh_instructions") as instructions:
            with self.assertRaises(SystemExit) as cm:
                docker._handle_docker_socket_permission_denied()

        instructions.assert_not_called()
        self.assertEqual(cm.exception.code, 1)

    def test_not_in_group_user_declines_exits(self):
        with patch("sima_cli.utils.docker._current_user", return_value="alice"), \
             patch("sima_cli.utils.docker._user_in_docker_group", return_value=False), \
             patch("sima_cli.utils.docker.confirm", return_value=False), \
             patch("sima_cli.utils.docker.subprocess.run") as run:
            with self.assertRaises(SystemExit) as cm:
                docker._handle_docker_socket_permission_denied()

        # Declined: no usermod.
        run.assert_not_called()
        self.assertEqual(cm.exception.code, 1)

    def test_not_in_group_usermod_fails_exits(self):
        usermod_result = MagicMock(returncode=1)
        with patch("sima_cli.utils.docker._current_user", return_value="alice"), \
             patch("sima_cli.utils.docker._user_in_docker_group", return_value=False), \
             patch("sima_cli.utils.docker.confirm", return_value=True), \
             patch("sima_cli.utils.docker.subprocess.run", return_value=usermod_result) as run:
            with self.assertRaises(SystemExit) as cm:
                docker._handle_docker_socket_permission_denied()

        run.assert_called_once_with(["sudo", "usermod", "-aG", "docker", "alice"])
        self.assertEqual(cm.exception.code, 1)

    def test_not_in_group_usermod_succeeds_exits_with_rerun_required(self):
        # usermod succeeded, but the original install/setup operation has NOT
        # completed because the current shell cannot use the new group yet.
        # Exit non-zero so parent scripts / CI do not treat setup as success.
        usermod_result = MagicMock(returncode=0)
        with patch("sima_cli.utils.docker._current_user", return_value="alice"), \
             patch("sima_cli.utils.docker._user_in_docker_group", return_value=False), \
             patch("sima_cli.utils.docker.confirm", return_value=True), \
             patch("sima_cli.utils.docker.subprocess.run", return_value=usermod_result), \
             patch("sima_cli.utils.docker._try_reexec_with_docker_group") as reexec, \
             patch("sima_cli.utils.docker._print_docker_group_refresh_instructions") as instructions:
            with self.assertRaises(SystemExit) as cm:
                docker._handle_docker_socket_permission_denied()

        reexec.assert_called_once_with()
        instructions.assert_called_once_with()
        self.assertNotEqual(cm.exception.code, 0)


class TestCheckAndStartDockerLinux(unittest.TestCase):
    """Linux branch of check_and_start_docker — the part affected by the fix."""

    def _patches(self, **kwargs):
        # Common patch stack: pretend we are on Linux, never really sleep,
        # never really shell out. Tests override specific entries via kwargs.
        defaults = {
            "get_environment_type": ("host", "linux"),
            "time_sleep": None,
            "docker_info_probe": (1, "Cannot connect to the Docker daemon"),
            "is_socket_permission_denied": False,
            "confirm": [True, True],  # start now? yes; sudo? yes
            "subprocess_run_returncode": 0,
        }
        defaults.update(kwargs)
        return defaults

    def test_daemon_already_running_returns_true_without_systemctl(self):
        with patch("sima_cli.utils.docker.get_environment_type", return_value=("host", "linux")), \
             patch("sima_cli.utils.docker._docker_info_probe", return_value=(0, "")), \
             patch("sima_cli.utils.docker.subprocess.run") as run:
            self.assertTrue(docker.check_and_start_docker())

        run.assert_not_called()

    def test_socket_permission_denied_routes_to_group_handler(self):
        # `docker info` fails with the socket-permission message → must NOT
        # invoke systemctl; must call the group-fix handler.
        probe_output = "permission denied while trying to connect to the Docker daemon socket at unix:///var/run/docker.sock"
        with patch("sima_cli.utils.docker.get_environment_type", return_value=("host", "linux")), \
             patch("sima_cli.utils.docker._docker_info_probe", return_value=(1, probe_output)), \
             patch("sima_cli.utils.docker._handle_docker_socket_permission_denied") as handler, \
             patch("sima_cli.utils.docker.subprocess.run") as run:
            self.assertTrue(docker.check_and_start_docker())

        handler.assert_called_once()
        run.assert_not_called()

    def test_systemctl_nonzero_breaks_retry_loop_and_exits(self):
        # The original bug: subprocess.run output was suppressed AND the
        # return code was ignored, so the loop spun 3 times. New behaviour:
        # break immediately on non-zero return code and exit 1.
        systemctl_result = MagicMock(returncode=5)
        with patch("sima_cli.utils.docker.get_environment_type", return_value=("host", "linux")), \
             patch("sima_cli.utils.docker._docker_info_probe", return_value=(1, "daemon not running")), \
             patch("sima_cli.utils.docker._is_socket_permission_denied", return_value=False), \
             patch("sima_cli.utils.docker.confirm", side_effect=[True, True]), \
             patch("sima_cli.utils.docker.subprocess.run", return_value=systemctl_result) as run, \
             patch("sima_cli.utils.docker.time.sleep"), \
             patch("sima_cli.utils.docker.print_manual_start_instructions"):
            with self.assertRaises(SystemExit) as cm:
                docker.check_and_start_docker()

        self.assertEqual(cm.exception.code, 1)
        # Exactly one systemctl call — the loop must NOT retry after non-zero.
        self.assertEqual(run.call_count, 1)
        # And critically, the call no longer hides stdout/stderr.
        run_kwargs = run.call_args.kwargs
        self.assertNotIn("stdout", run_kwargs)
        self.assertNotIn("stderr", run_kwargs)

    def test_systemctl_success_then_daemon_comes_up_returns_true(self):
        # systemctl returns 0, docker info still fails once, then succeeds —
        # the inner poll loop must accept that and return True.
        systemctl_result = MagicMock(returncode=0)
        probe_sequence = [
            (1, "daemon not running"),  # initial probe before start
            (1, "still starting"),       # first poll inside loop
            (0, ""),                     # second poll: daemon ready
        ]
        with patch("sima_cli.utils.docker.get_environment_type", return_value=("host", "linux")), \
             patch("sima_cli.utils.docker._docker_info_probe", side_effect=probe_sequence), \
             patch("sima_cli.utils.docker._is_socket_permission_denied", return_value=False), \
             patch("sima_cli.utils.docker.confirm", side_effect=[True, True]), \
             patch("sima_cli.utils.docker.subprocess.run", return_value=systemctl_result), \
             patch("sima_cli.utils.docker.time.sleep"):
            self.assertTrue(docker.check_and_start_docker())

    def test_systemctl_success_but_socket_denied_routes_to_group_handler(self):
        # systemctl ok, but the daemon is accessible only to docker-group
        # members. The poll loop must detect the socket-permission failure
        # and call the group handler instead of timing out.
        systemctl_result = MagicMock(returncode=0)
        probe_sequence = [
            (1, "daemon not running"),
            (1, "permission denied while trying to connect to the Docker daemon socket at unix:///var/run/docker.sock"),
        ]
        with patch("sima_cli.utils.docker.get_environment_type", return_value=("host", "linux")), \
             patch("sima_cli.utils.docker._docker_info_probe", side_effect=probe_sequence), \
             patch("sima_cli.utils.docker._is_socket_permission_denied", side_effect=[False, True]), \
             patch("sima_cli.utils.docker.confirm", side_effect=[True, True]), \
             patch("sima_cli.utils.docker.subprocess.run", return_value=systemctl_result), \
             patch("sima_cli.utils.docker._handle_docker_socket_permission_denied") as handler, \
             patch("sima_cli.utils.docker.time.sleep"):
            self.assertTrue(docker.check_and_start_docker())

        handler.assert_called_once()


class TestDockerInfoProbe(unittest.TestCase):
    """Verify the probe always returns a 2-tuple and never raises."""

    def test_success_returns_zero_and_combined_output(self):
        completed = MagicMock(returncode=0, stdout="server info\n", stderr="")
        with patch("sima_cli.utils.docker.subprocess.run", return_value=completed) as run:
            rc, output = docker._docker_info_probe()

        self.assertEqual(rc, 0)
        self.assertIn("server info", output)
        # Probe must time out — otherwise a hung daemon would block the CLI.
        self.assertEqual(run.call_args.kwargs.get("timeout"), 5)
        self.assertEqual(run.call_args.args[0], ["docker", "info"])

    def test_failure_returncode_is_propagated_with_stderr(self):
        completed = MagicMock(
            returncode=1,
            stdout="",
            stderr="permission denied while trying to connect to the Docker daemon socket",
        )
        with patch("sima_cli.utils.docker.subprocess.run", return_value=completed):
            rc, output = docker._docker_info_probe()

        self.assertEqual(rc, 1)
        # Caller relies on this combined string to detect socket-permission
        # errors, so stderr must be included.
        self.assertIn("permission denied", output)

    def test_docker_binary_missing_returns_none(self):
        with patch("sima_cli.utils.docker.subprocess.run", side_effect=FileNotFoundError()):
            rc, output = docker._docker_info_probe()

        self.assertIsNone(rc)
        self.assertIn("docker: command not found", output)

    def test_subprocess_error_returns_none(self):
        # TimeoutExpired is a SubprocessError subclass — the probe must
        # swallow it (a frozen daemon must not crash the CLI) and report
        # the failure as returncode=None.
        err = subprocess.TimeoutExpired(cmd=["docker", "info"], timeout=5)
        with patch("sima_cli.utils.docker.subprocess.run", side_effect=err):
            rc, output = docker._docker_info_probe()

        self.assertIsNone(rc)
        self.assertTrue(output)


class TestIsSocketPermissionDenied(unittest.TestCase):
    def test_matches_typical_docker_socket_error(self):
        msg = "Got permission denied while trying to connect to the Docker daemon socket at unix:///var/run/docker.sock"
        self.assertTrue(docker._is_socket_permission_denied(msg))

    def test_does_not_match_daemon_down_error(self):
        msg = "Cannot connect to the Docker daemon at unix:///var/run/docker.sock. Is the docker daemon running?"
        self.assertFalse(docker._is_socket_permission_denied(msg))

    def test_handles_empty_input(self):
        self.assertFalse(docker._is_socket_permission_denied(""))
        self.assertFalse(docker._is_socket_permission_denied(None))


if __name__ == "__main__":
    unittest.main()
