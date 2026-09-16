#!/usr/bin/env python3
"""Package the deployer as one file a customer can double-click.

    python3 tools/deployer/build.py

Produces `X2部署工具.exe` on Windows and `X2部署工具` on Linux, at the top of
the repository, with Python and every library inside it. The customer installs
nothing.

**PyInstaller does not cross-compile.** A Windows executable has to be built on
Windows and a Linux one on Linux -- there is no flag that changes that, and a
Linux machine cannot produce the .exe no matter what it is asked. Run this once
on each platform you ship to.

The detector weights are bundled when they are already on the build machine.
That is on purpose: a customer's laptop may be cabled to the robot with no way
out to the internet, and a 23 MB binary that works beats a 5 MB one that
cannot see people.
"""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
NAME = 'X2部署工具'

WINDOWS = platform.system() == 'Windows'
SEPARATOR = ';' if WINDOWS else ':'          # PyInstaller's --add-data syntax


def _weights() -> list:
    """The detector model, if this machine has a copy to bundle."""
    sys.path.insert(0, str(REPO / 'tools'))
    from deployer.core import assets

    found = assets.locate()
    if not found.ready:
        print('· 没有找到识别模型,不打包进去。')
        print('  客户第一次部署时需要能上网,否则机器人看不清人。')
        print(f'  现在下载:python3 {REPO}/tools/fetch_model.py --dest ~/x2-models')
        return []
    print(f'· 打包识别模型:{found.directory}')
    return [f'--add-data={found.directory}{SEPARATOR}models']


def _package() -> list:
    """What gets sent to the robot, under one directory of its own.

    Not scattered at the top of the bundle: a frozen program unpacks itself
    into a temporary directory alongside the whole Python runtime, and a
    deployment that mirrored that directory would upload the interpreter to
    the robot. Everything meant for the robot lives under payload/, and
    nothing else does.
    """
    package = REPO / 'x2_greeter_ws'
    tools = REPO / 'tools' / 'deploy'
    return [f'--add-data={package}{SEPARATOR}payload/x2_greeter_ws',
            f'--add-data={tools}{SEPARATOR}payload/tools/deploy']


def main() -> int:
    if shutil.which('pyinstaller') is None:
        print('需要先安装 PyInstaller:')
        print(f'  {sys.executable} -m pip install --user pyinstaller')
        return 1

    command = [
        'pyinstaller', '--onefile', '--noconsole', '--clean',
        f'--name={NAME}',
        f'--distpath={REPO}',
        f'--workpath={HERE / "build"}',
        f'--specpath={HERE / "build"}',
        # paramiko pulls its ciphers in at runtime, which a static scan misses.
        '--hidden-import=paramiko',
        '--collect-submodules=paramiko',
        *_package(),
        *_weights(),
        str(HERE / 'gui' / 'app.py'),
    ]

    print('· 正在打包,大约两三分钟…')
    result = subprocess.run(command, cwd=REPO)
    if result.returncode != 0:
        return result.returncode

    produced = REPO / (f'{NAME}.exe' if WINDOWS else NAME)
    if not produced.exists():
        print(f'打包结束了,但没有找到 {produced}', file=sys.stderr)
        return 1
    if not WINDOWS:
        produced.chmod(produced.stat().st_mode | 0o111)

    size = produced.stat().st_size / (1024 * 1024)
    print(f'\n✓ 已生成:{produced}  ({size:.0f} MB)')
    print('  客户双击它就能打开部署界面,不需要安装 Python。')
    if not WINDOWS:
        print('\n注意:这是 Linux 版。Windows 的 .exe 必须在 Windows 上打包,')
        print('     PyInstaller 不能跨平台交叉编译。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
