"""Native OpenSSH upload with byte progress and an existing SSH host-key pin."""
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import uuid


def upload(ssh, sftp, ip, password, local_path, remote_path, callback):
    """Use native SSH on POSIX hosts; retain SFTP where unavailable."""
    executable = shutil.which('ssh') if os.name == 'posix' else None
    if executable is None:
        return sftp.put(local_path, remote_path, callback=callback)

    size = os.path.getsize(local_path)
    staging = remote_path + '.sima-upload-' + uuid.uuid4().hex
    process = None
    with tempfile.TemporaryDirectory(prefix='sima-cli-upload-') as directory:
        helper = Path(directory) / 'askpass'
        # Password is passed only in the short-lived child's environment, never
        # in command-line arguments or a file. The directory is owner-only.
        helper.write_text('#!/bin/sh\nprintf \'%s\\n\' "$SIMA_CLI_SSH_PASSWORD"\n')
        helper.chmod(0o700)
        known_hosts = Path(directory) / 'known_hosts'
        key = ssh.get_transport().get_remote_server_key()
        known_hosts.write_text(f'{ip} {key.get_name()} {key.get_base64()}\n')
        environment = dict(os.environ, SSH_ASKPASS=str(helper), SSH_ASKPASS_REQUIRE='force',
                           DISPLAY=os.environ.get('DISPLAY') or 'sima-cli', SIMA_CLI_SSH_PASSWORD=password)
        # Pin the same host key as the already established Paramiko connection.
        # Ignore local aliases/proxies so the data connection uses that same host.
        command = [executable, '-F', '/dev/null', '-T',
                   '-o', 'StrictHostKeyChecking=yes', '-o', f'UserKnownHostsFile={known_hosts}',
                   '-o', 'PreferredAuthentications=password', '-o', 'PubkeyAuthentication=no',
                   '-o', 'NumberOfPasswordPrompts=1', '-o', 'ConnectTimeout=10',
                   '-o', 'ServerAliveInterval=5', '-o', 'ServerAliveCountMax=6',
                   '-o', 'Compression=no', f'sima@{ip}',
                   'umask 077; cat > ' + shlex.quote(staging)]
        try:
            with tempfile.TemporaryFile() as errors:
                process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                           stderr=errors, env=environment, start_new_session=True)
                transferred = 0
                try:
                    with open(local_path, 'rb') as source:
                        while True:
                            chunk = source.read(1024 * 1024)
                            if not chunk:
                                break
                            process.stdin.write(chunk)
                            transferred += len(chunk)
                            callback(transferred, size)
                    process.stdin.close()
                except BrokenPipeError:
                    # Report the SSH/storage error below, rather than just EPIPE.
                    pass
                code = process.wait(timeout=60)
                errors.seek(0)
                detail = errors.read(8192).decode('utf-8', errors='replace').strip()
                if code != 0:
                    raise RuntimeError(f'OpenSSH upload failed (exit {code}): {detail}')
                if transferred != size or sftp.stat(staging).st_size != size:
                    raise RuntimeError('Uploaded file size does not match the local file.')
                sftp.posix_rename(staging, remote_path)
        finally:
            if process is not None:
                if process.poll() is None:
                    process.kill()
                    process.wait()
                if process.stdin is not None:
                    try:
                        process.stdin.close()
                    except OSError:
                        pass
            try:
                sftp.remove(staging)
            except OSError:
                pass
