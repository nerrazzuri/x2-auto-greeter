"""Talking to an X2 over SSH, without bash, rsync or sshpass.

tools/deploy/install.sh does this already and does it well, but it is a bash
script built out of ssh, rsync and ssh-copy-id. None of those exist on a
Windows laptop, and the customers this deployer is for are as likely to be on
Windows as not. So the same sequence is expressed here in paramiko, which runs
the same on both.

What is deliberately kept from install.sh:

  * everything lands under /home/run/x2_greeter, so uninstall.sh still removes
    the whole deployment, and nothing under /agibot is ever written;
  * the tree is mirrored rather than merged -- a file deleted locally goes on
    the robot too, which is how a renamed module stops being importable;
  * docs/ never leaves the laptop.

What is different: no key is installed. A customer deploying their own robot
authenticates with the vendor's password for the length of one deployment,
and leaving a key behind on a machine we do not own is not ours to decide.
"""
from __future__ import annotations

import os
import posixpath
import stat
from dataclasses import dataclass
from typing import Callable, Iterable, List, Optional, Tuple

import paramiko

ROOT = '/home/run/x2_greeter'
SHARE = f'{ROOT}/ws/install/x2_greeter/share/x2_greeter/config'

DEFAULT_USER = 'run'
DEFAULT_PASSWORD = '1'          # the vendor's, on every X2 shipped so far
DEFAULT_HOST = '10.0.1.41'

# Mirrored to the robot minus these. docs/ holds the design spec and the
# implementation plan, which have no business standing in a customer's mall.
EXCLUDED = {'.git', '__pycache__', 'sdk', 'docs', '.pytest_cache', 'build',
            'install', 'log', '.venv', 'node_modules'}
EXCLUDED_SUFFIXES = ('.pyc', '.pyo', '.swp')


class RobotError(RuntimeError):
    """Something the customer needs told, in words, not a traceback."""


@dataclass(frozen=True)
class Identity:
    """Which robot this is. Not which model -- which unit."""

    serial: str
    mac: str
    hostname: str

    def __str__(self) -> str:
        return f'{self.hostname} · 序列号 {self.serial} · {self.mac}'


@dataclass(frozen=True)
class Result:
    code: int
    out: str
    err: str

    @property
    def ok(self) -> bool:
        return self.code == 0


class Robot:
    """One SSH session to one robot, with the file transfer it needs.

    Used as a context manager so a failed deployment does not leave a session
    open against a robot a customer is about to unplug.
    """

    def __init__(self, host: str = DEFAULT_HOST, user: str = DEFAULT_USER,
                 password: str = DEFAULT_PASSWORD, timeout: float = 10.0) -> None:
        self.host, self.user, self._password = host, user, password
        self._timeout = timeout
        self._client: Optional[paramiko.SSHClient] = None
        self._sftp: Optional[paramiko.SFTPClient] = None

    # -- session --------------------------------------------------------------

    def connect(self) -> None:
        client = paramiko.SSHClient()
        # The X2s are flashed from one image and share their host keys, so a
        # known_hosts entry proves nothing about which unit answered -- see
        # identify(), which asks the board instead. Refusing to connect on a
        # host-key change would strand a customer swapping robots.
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            client.connect(self.host, username=self.user, password=self._password,
                           timeout=self._timeout, allow_agent=False,
                           look_for_keys=False)
        except paramiko.AuthenticationException as exc:
            raise RobotError(
                f'{self.user}@{self.host} 的密码不对。X2 出厂密码是 "1"。') from exc
        except OSError as exc:
            raise RobotError(
                f'连不上 {self.host}。请检查网线是否插好,以及本机的有线网口地址'
                f'是不是 10.0.1.x/24。') from exc
        self._client = client

    def close(self) -> None:
        if self._sftp is not None:
            self._sftp.close()
            self._sftp = None
        if self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self) -> 'Robot':
        self.connect()
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    # -- commands -------------------------------------------------------------

    def run(self, command: str, timeout: Optional[float] = 120.0) -> Result:
        if self._client is None:
            raise RobotError('还没有连接到机器人')
        _stdin, stdout, stderr = self._client.exec_command(command, timeout=timeout)
        out = stdout.read().decode('utf-8', 'replace')
        err = stderr.read().decode('utf-8', 'replace')
        return Result(stdout.channel.recv_exit_status(), out, err)

    def sudo(self, command: str, timeout: Optional[float] = 120.0) -> Result:
        """Run one command as root.

        The password goes in on stdin with `-S` and `-p ''`: there is no
        terminal on this channel, and sudo's own prompt would otherwise be
        mixed into the output the customer is shown.
        """
        if self._client is None:
            raise RobotError('还没有连接到机器人')
        wrapped = f"sudo -S -p '' {command}"
        stdin, stdout, stderr = self._client.exec_command(wrapped, timeout=timeout)
        stdin.write(self._password + '\n')
        stdin.flush()
        out = stdout.read().decode('utf-8', 'replace')
        err = stderr.read().decode('utf-8', 'replace')
        code = stdout.channel.recv_exit_status()
        if code != 0 and 'incorrect password' in err.lower():
            raise RobotError('sudo 密码不对,装不了开机自启。')
        return Result(code, out, err)

    # -- identity -------------------------------------------------------------

    def identify(self) -> Identity:
        """Which unit this is, from the board rather than from SSH.

        Every X2 presents the same SSH host keys, so a fingerprint cannot tell
        two of them apart. The board serial can, and the customer needs it in
        their own records: it is what says whether the robot in front of them
        is the one that was deployed last week.
        """
        serial = self.run(
            'tr -d "\\0" < /proc/device-tree/serial-number').out.strip()
        mac = self.run(
            "ip -brief link show develop0 | awk '{print $3}'").out.strip()
        hostname = self.run('hostname').out.strip()
        if not serial:
            raise RobotError('读不到机器人的板子序列号,这可能不是一台 X2。')
        return Identity(serial=serial, mac=mac, hostname=hostname)

    # -- files ----------------------------------------------------------------

    @property
    def sftp(self) -> paramiko.SFTPClient:
        if self._sftp is None:
            if self._client is None:
                raise RobotError('还没有连接到机器人')
            self._sftp = self._client.open_sftp()
        return self._sftp

    def makedirs(self, remote: str) -> None:
        parts, built = remote.strip('/').split('/'), ''
        for part in parts:
            built = f'{built}/{part}'
            try:
                self.sftp.stat(built)
            except IOError:
                self.sftp.mkdir(built)

    def put(self, local, remote: str) -> None:
        self.makedirs(posixpath.dirname(remote))
        self.sftp.put(os.fspath(local), remote)

    def put_text(self, text: str, remote: str) -> None:
        self.makedirs(posixpath.dirname(remote))
        with self.sftp.open(remote, 'w') as handle:
            handle.write(text)

    def read_text(self, remote: str) -> str:
        with self.sftp.open(remote, 'r') as handle:
            return handle.read().decode('utf-8', 'replace')

    def exists(self, remote: str) -> bool:
        try:
            self.sftp.stat(remote)
            return True
        except IOError:
            return False

    def mirror(self, local_root, remote_root: str,
               on_file: Optional[Callable[[str], None]] = None) -> int:
        """Copy a tree over, and delete what is no longer in it.

        The delete half is not tidiness. A module that was renamed locally
        stays importable on the robot until its old file is gone, and a stale
        .py next to a new one is the kind of thing that runs for weeks.
        """
        local_root = os.fspath(local_root)
        wanted = set()
        sent = 0

        for dirpath, dirnames, filenames in os.walk(local_root):
            dirnames[:] = sorted(d for d in dirnames if d not in EXCLUDED)
            relative = os.path.relpath(dirpath, local_root)
            remote_dir = (remote_root if relative == '.'
                          else posixpath.join(remote_root, relative.replace(os.sep, '/')))
            self.makedirs(remote_dir)
            wanted.add(remote_dir)

            for name in sorted(filenames):
                if name in EXCLUDED or name.endswith(EXCLUDED_SUFFIXES):
                    continue
                source = os.path.join(dirpath, name)
                target = posixpath.join(remote_dir, name)
                wanted.add(target)
                if on_file is not None:
                    on_file(posixpath.relpath(target, remote_root))
                self.sftp.put(source, target)
                sent += 1

        self._prune(remote_root, wanted)
        return sent

    def _prune(self, remote_root: str, wanted: set) -> None:
        for path, is_dir in reversed(list(self._walk_remote(remote_root))):
            if path in wanted:
                continue
            try:
                self.sftp.rmdir(path) if is_dir else self.sftp.remove(path)
            except IOError:
                pass                      # a non-empty directory we still want

    def _walk_remote(self, root: str) -> Iterable[Tuple[str, bool]]:
        try:
            entries = self.sftp.listdir_attr(root)
        except IOError:
            return
        for entry in entries:
            path = posixpath.join(root, entry.filename)
            is_dir = stat.S_ISDIR(entry.st_mode)
            yield path, is_dir
            if is_dir:
                yield from self._walk_remote(path)
