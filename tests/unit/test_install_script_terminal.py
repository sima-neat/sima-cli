"""Package scripts retain login setup and the caller's foreground terminal job."""

import json
import os
import select
import shlex
import shutil
import signal
import subprocess
import sys
import textwrap
import time
from unittest.mock import Mock, patch

import pytest

from sima_cli.install import metadata_installer as installer


@pytest.mark.skipif(os.name == "nt", reason="POSIX shell invocation")
def test_install_script_uses_noninteractive_login_shell(tmp_path, monkeypatch):
    monkeypatch.delenv("SIMA_INSTALL_CONTEXT", raising=False)
    monkeypatch.setenv("SHELL", "/bin/bash")
    metadata = {"name": "test", "version": "1", "installation": {"script": "./install.sh"}}
    with patch.object(installer, "registry"), patch.object(
        installer.subprocess, "run", return_value=Mock(returncode=0)
    ) as run:
        installer._run_installation_script(metadata, str(tmp_path), force=True)

    args, kwargs = run.call_args
    assert args == (["/bin/bash", "-l", "-c", "./install.sh"],)
    assert kwargs["cwd"] == str(tmp_path)
    assert kwargs["env"]["SIMA_INSTALL_CONTEXT"] == "1"
    assert kwargs["env"]["FORCE_INSTALL"] == "1"
    assert "start_new_session" not in kwargs
    assert "stdin" not in kwargs


@pytest.mark.skipif(os.name == "nt", reason="POSIX controlling terminal required")
@pytest.mark.parametrize("shell_name", ["bash", "zsh"])
def test_install_script_preserves_foreground_job_and_ctrl_c(tmp_path, shell_name):
    import pty

    shell = shutil.which(shell_name)
    if not shell:
        pytest.skip("{} is unavailable".format(shell_name))

    home = tmp_path / "home"
    home.mkdir()
    tooling = home / "tooling" / "bin"
    tooling.mkdir(parents=True)
    tool = tooling / "regression-tool"
    tool.write_text("#!/bin/sh\nprintf 'tooling-ok'\n")
    tool.chmod(0o755)
    profile = home / (".bash_profile" if shell_name == "bash" else ".zprofile")
    profile.write_text(
        "export INSTALL_PROFILE_MARKER=loaded\n"
        "export PATH={}:$PATH\n".format(shlex.quote(str(tooling)))
    )

    report = tmp_path / "report.json"
    probe = tmp_path / "probe.py"
    probe.write_text(textwrap.dedent("""\
        import json, os, subprocess, sys
        with open(sys.argv[1], 'w') as output:
            json.dump({
                'script_pgrp': os.getpgrp(),
                'script_foreground': os.tcgetpgrp(0),
                'stdin_is_tty': os.isatty(0),
                'profile': os.environ.get('INSTALL_PROFILE_MARKER'),
                'tool': subprocess.check_output(['regression-tool'], text=True),
            }, output)
    """))
    # Keep a command after the probe so the shell cannot optimize it into exec.
    script = "{} {} {}; :".format(
        shlex.quote(sys.executable), shlex.quote(str(probe)), shlex.quote(str(report))
    )
    driver = textwrap.dedent("""\
        import fcntl, json, os, signal, sys, termios
        fcntl.ioctl(0, termios.TIOCSCTTY, 0)
        from unittest.mock import patch
        from sima_cli.install import metadata_installer as installer

        report, script, directory = sys.argv[1:]
        before = os.tcgetpgrp(0)
        metadata = {'name': 'terminal-test', 'version': '1',
                    'installation': {'script': script}}
        with patch.object(installer, 'registry'):
            installer._run_installation_script(metadata, directory)
        with open(report) as source:
            state = json.load(source)
        state.update(caller_pgrp=os.getpgrp(), before=before,
                     after=os.tcgetpgrp(0))
        # Exercise a subsequent terminal operation without sudo or package writes.
        termios.tcsetattr(0, termios.TCSANOW, termios.tcgetattr(0))
        state['terminal_operation'] = True
        def interrupted(signum, frame):
            state['ctrl_c_received'] = True
            with open(report, 'w') as output:
                json.dump(state, output)
            sys.exit(0)
        signal.signal(signal.SIGINT, interrupted)
        print('READY_FOR_CTRL_C', flush=True)
        signal.pause()
    """)
    env = dict(os.environ, HOME=str(home), ZDOTDIR=str(home), SHELL=shell)
    for key in ("SIMA_INSTALL_CONTEXT", "INSTALL_PROFILE_MARKER", "BASH_ENV", "ENV"):
        env.pop(key, None)

    terminal, slave = pty.openpty()
    # Model an SSH terminal: the driver owns a session and its controlling PTY.
    process = subprocess.Popen(
        [sys.executable, "-c", driver, str(report), script, str(tmp_path)],
        env=env, stdin=slave, stdout=slave, stderr=slave, start_new_session=True,
    )
    os.close(slave)

    output = bytearray()
    sent_interrupt = False
    terminal_closed = False
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            readable, _, _ = select.select([] if terminal_closed else [terminal], [], [], 0.1)
            if readable:
                try:
                    chunk = os.read(terminal, 65536)
                except OSError:
                    chunk = b""  # Linux PTYs report EIO when their slave closes.
                if not chunk:
                    terminal_closed = True
                output.extend(chunk)
                if not sent_interrupt and b"READY_FOR_CTRL_C" in output:
                    os.write(terminal, b"\x03")
                    sent_interrupt = True
            if process.poll() is not None:
                break
        assert process.poll() is not None, "PTY child timed out:\n{}".format(output.decode(errors="replace"))
        assert process.returncode == 0, output.decode(errors="replace")
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
        process.wait()
        os.close(terminal)

    state = json.loads(report.read_text())
    assert state["script_pgrp"] == state["caller_pgrp"]
    assert state["script_foreground"] == state["before"] == state["after"] == state["caller_pgrp"]
    assert state["stdin_is_tty"] is True
    assert state["profile"] == "loaded"
    assert state["tool"] == "tooling-ok"
    assert state["terminal_operation"] is True
    assert state["ctrl_c_received"] is True
