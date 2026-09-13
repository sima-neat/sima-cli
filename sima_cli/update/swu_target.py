"""Checked command execution and bundle transfer for local/SSH SWUpdate."""
import hashlib
import os
import shlex
import subprocess

import click
from rich.progress import Progress

from sima_cli.update.remote import init_ssh_session


class Target:
    def __init__(self, ip=None, passwd='edgeai'):
        self.ip = ip
        self.passwd = passwd
        self.ssh = init_ssh_session(ip, password=passwd) if ip else None
        if self.ssh:
            self.ssh.get_transport().set_keepalive(15)

    def close(self):
        if self.ssh:
            self.ssh.close()

    def run(self, script, stream=None, check=True):
        command = ['sudo', '-S', '-p', '', 'sh', '-c', script]
        process = None
        if self.ssh:
            stdin, stdout, stderr = self.ssh.exec_command(shlex.join(command))
            # Merge stderr at the channel to avoid deadlock from an unread pipe.
            stdout.channel.set_combine_stderr(True)
            stdin.write(self.passwd + '\n')
            stdin.flush()
            stdin.channel.shutdown_write()
        else:
            process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                       stderr=subprocess.STDOUT)
            process.stdin.write((self.passwd + '\n').encode())
            process.stdin.close()
            stdout = process.stdout
        output = []
        line = bytearray()
        try:
            while True:
                data = stdout.read(1)
                if not data:
                    break
                if data in (b'\n', b'\r'):
                    text = line.decode('utf-8', errors='replace')
                    line.clear()
                    if stream:
                        stream(text)
                    else:
                        output.append(text)
                else:
                    line.extend(data)
            if line:
                text = line.decode('utf-8', errors='replace')
                stream(text) if stream else output.append(text)
            code = stdout.channel.recv_exit_status() if self.ssh else process.wait()
        except BaseException:
            if process:
                # Do not automatically kill or replay an installer after interruption.
                stdout.close()
            raise
        result = '\n'.join(output)
        if check and code != 0:
            raise click.ClickException(f'Target command failed (exit {code}). {result[-2000:]}')
        return result

    def transfer(self, local, remote, move=False):
        digest = hashlib.sha256()
        with open(local, 'rb') as source:
            for block in iter(lambda: source.read(1024 * 1024), b''):
                digest.update(block)
        if self.ssh:
            # The private staging directory is owned by the authenticated user.
            with self.ssh.open_sftp() as sftp, Progress() as progress:
                task = progress.add_task('Transfer SWU', total=os.path.getsize(local))
                sftp.put(local, remote, callback=lambda done, total: progress.update(task, completed=done, total=total))
        else:
            self.run(('mv -- ' if move else 'cp -- ') + shlex.quote(local) + ' ' + shlex.quote(remote))
        actual = self.run('sha256sum -- ' + shlex.quote(remote)).split()[0]
        if actual != digest.hexdigest():
            raise click.ClickException('Transferred SWU checksum does not match the downloaded bundle.')
