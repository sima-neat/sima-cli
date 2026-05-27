"""Tests for the docker-startup / docker-group fix in sima_cli.utils.docker.

Covers:
  - `_reexec_with_docker_group` (sentinel guard, missing `sg`, success path)
  - `_handle_docker_socket_permission_denied` (group-already-set vs. need-to-add,
    accept / decline, usermod success / failure)
  - The Linux branch of `check_and_start_docker`: systemctl return-code is
    honoured, the daemon-up-but-socket-denied detection routes to the group
    flow, and `time.sleep` is not hit in tests.
"""

import unittest
from unittest.mock import patch, MagicMock

from sima_cli.utils import docker


class TestReexecWithDockerGroup(unittest.TestCase):
    def test_sentinel_short_circuits(self):
        # Already inside a re-exec — must NOT call execvpe a second time.
        env = {docker._REEXEC_ENV_FLAG: "1"}
        with patch.dict(docker.os.environ, env, clear=False), \
             patch("sima_cli.utils.docker.shutil.which") as which, \
             patch("sima_cli.utils.docker.os.execvpe") as execvpe:
            docker._reexec_with_docker_group()

        which.assert_not_called()
        execvpe.assert_not_called()

    def test_missing_sg_falls_back(self):
        with patch.dict(docker.os.environ, {}, clear=False), \
             patch("sima_cli.utils.docker.shutil.which", return_value=None) as which, \
             patch("sima_cli.utils.docker.os.execvpe") as execvpe:
            docker.os.environ.pop(docker._REEXEC_ENV_FLAG, None)
            docker._reexec_with_docker_group()

        which.assert_called_once_with("sg")
        execvpe.assert_not_called()

    def test_invokes_sg_with_quoted_argv_and_sentinel(self):
        argv = ["sima-cli", "sdk", "setup", "--devkit", "192.168.135.108"]
        captured = {}

        def fake_execvpe(file, args, env):
            captured["file"] = file
            captured["args"] = args
            captured["env"] = env

        with patch.dict(docker.os.environ, {"PATH": "/usr/bin"}, clear=False), \
             patch("sima_cli.utils.docker.shutil.which", return_value="/usr/bin/sg"), \
             patch("sima_cli.utils.docker.sys.argv", argv), \
             patch("sima_cli.utils.docker.os.execvpe", side_effect=fake_execvpe):
            docker.os.environ.pop(docker._REEXEC_ENV_FLAG, None)
            docker._reexec_with_docker_group()

        self.assertEqual(captured["file"], "sg")
        # sg <group> -c "<shell-quoted argv>"
        self.assertEqual(captured["args"][0:3], ["sg", "docker", "-c"])
        # shlex.join must produce a single string the child shell will parse
        # back into the original argv.
        self.assertIn("sima-cli", captured["args"][3])
        self.assertIn("192.168.135.108", captured["args"][3])
        # Sentinel must be set so the re-exec'd process won't loop.
        self.assertEqual(captured["env"].get(docker._REEXEC_ENV_FLAG), "1")

    def test_execvpe_oserror_returns_to_caller(self):
        with patch.dict(docker.os.environ, {}, clear=False), \
             patch("sima_cli.utils.docker.shutil.which", return_value="/usr/bin/sg"), \
             patch("sima_cli.utils.docker.os.execvpe", side_effect=OSError("denied")):
            docker.os.environ.pop(docker._REEXEC_ENV_FLAG, None)
            # Must NOT raise — caller relies on graceful fallback.
            docker._reexec_with_docker_group()


class TestHandleDockerSocketPermissionDenied(unittest.TestCase):
    def setUp(self):
        # Each test starts with a clean sentinel so we control which branch
        # runs.
        self._saved_sentinel = docker.os.environ.pop(docker._REEXEC_ENV_FLAG, None)

    def tearDown(self):
        if self._saved_sentinel is not None:
            docker.os.environ[docker._REEXEC_ENV_FLAG] = self._saved_sentinel
        else:
            docker.os.environ.pop(docker._REEXEC_ENV_FLAG, None)

    def test_already_in_group_first_pass_attempts_reexec_then_falls_back(self):
        # User is in the docker group but the current session hasn't picked
        # it up. First pass should attempt re-exec; if that returns (i.e.,
        # could not actually re-exec, e.g. no `sg`), we fall through to the
        # manual newgrp instructions and exit 1.
        with patch("sima_cli.utils.docker._current_user", return_value="alice"), \
             patch("sima_cli.utils.docker._user_in_docker_group", return_value=True), \
             patch("sima_cli.utils.docker._reexec_with_docker_group") as reexec:
            with self.assertRaises(SystemExit) as cm:
                docker._handle_docker_socket_permission_denied()

        reexec.assert_called_once()
        self.assertEqual(cm.exception.code, 1)

    def test_already_in_group_after_retry_exits_with_logout_hint(self):
        docker.os.environ[docker._REEXEC_ENV_FLAG] = "1"
        with patch("sima_cli.utils.docker._current_user", return_value="alice"), \
             patch("sima_cli.utils.docker._user_in_docker_group", return_value=True), \
             patch("sima_cli.utils.docker._reexec_with_docker_group") as reexec:
            with self.assertRaises(SystemExit) as cm:
                docker._handle_docker_socket_permission_denied()

        # Already-retried branch must NOT re-exec a second time.
        reexec.assert_not_called()
        self.assertEqual(cm.exception.code, 1)

    def test_not_in_group_user_declines_exits(self):
        with patch("sima_cli.utils.docker._current_user", return_value="alice"), \
             patch("sima_cli.utils.docker._user_in_docker_group", return_value=False), \
             patch("sima_cli.utils.docker.confirm", return_value=False), \
             patch("sima_cli.utils.docker.subprocess.run") as run, \
             patch("sima_cli.utils.docker._reexec_with_docker_group") as reexec:
            with self.assertRaises(SystemExit) as cm:
                docker._handle_docker_socket_permission_denied()

        # Declined: no usermod, no re-exec.
        run.assert_not_called()
        reexec.assert_not_called()
        self.assertEqual(cm.exception.code, 1)

    def test_not_in_group_usermod_fails_exits(self):
        usermod_result = MagicMock(returncode=1)
        with patch("sima_cli.utils.docker._current_user", return_value="alice"), \
             patch("sima_cli.utils.docker._user_in_docker_group", return_value=False), \
             patch("sima_cli.utils.docker.confirm", return_value=True), \
             patch("sima_cli.utils.docker.subprocess.run", return_value=usermod_result) as run, \
             patch("sima_cli.utils.docker._reexec_with_docker_group") as reexec:
            with self.assertRaises(SystemExit) as cm:
                docker._handle_docker_socket_permission_denied()

        run.assert_called_once_with(["sudo", "usermod", "-aG", "docker", "alice"])
        reexec.assert_not_called()
        self.assertEqual(cm.exception.code, 1)

    def test_not_in_group_usermod_succeeds_reexecs_then_exits(self):
        usermod_result = MagicMock(returncode=0)
        with patch("sima_cli.utils.docker._current_user", return_value="alice"), \
             patch("sima_cli.utils.docker._user_in_docker_group", return_value=False), \
             patch("sima_cli.utils.docker.confirm", return_value=True), \
             patch("sima_cli.utils.docker.subprocess.run", return_value=usermod_result), \
             patch("sima_cli.utils.docker._reexec_with_docker_group") as reexec:
            with self.assertRaises(SystemExit) as cm:
                docker._handle_docker_socket_permission_denied()

        reexec.assert_called_once()
        # After a successful add the original code path exits 0 if re-exec
        # didn't actually take over the process.
        self.assertEqual(cm.exception.code, 0)


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
