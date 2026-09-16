"""What the packaged program is called, and whether the docs still say so.

This file exists because of one upload. The executable was called
`部署工具-Linux`, which is a perfectly good file name everywhere except a
GitHub release: asset names are stripped to ASCII on the way in, without a
warning, and it arrived as `default.-Linux`. A customer downloading that has
no idea what it is, and on Windows the same stripping is one step away from
taking the `.exe` with it.

The other half is drift. The name lives in build.py and is repeated in three
documents that tell a customer what to double-click. A document that names a
file nobody will find is worse than no document.
"""
import importlib
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO / 'tools' / 'deployer'))

import build                                                    # noqa: E402

CUSTOMER_FACING = ('tools/deployer/CUSTOMER_README.md',
                   'tools/deployer/README.md',
                   'tools/deployer/RELEASING.md')


def _name_on(system: str, monkeypatch) -> str:
    """What build.py would call the executable on that platform."""
    monkeypatch.setattr(build.platform, 'system', lambda: system)
    return importlib.reload(build).NAME


@pytest.fixture(autouse=True)
def _restore():
    yield
    importlib.reload(build)


@pytest.mark.parametrize('system', ['Windows', 'Linux'])
def test_the_executable_has_an_ascii_name(system, monkeypatch):
    """GitHub strips anything else from a release asset, silently."""
    name = _name_on(system, monkeypatch)
    assert name.isascii(), f'{name!r} 上传到 GitHub release 会被改名'
    assert re.fullmatch(r'[A-Za-z0-9._-]+', name), \
        f'{name!r} 里有可能被替换掉的字符'


def test_the_two_platforms_get_different_names(monkeypatch):
    """Both files end up in one folder. A customer who picks the wrong one
    gets something that will not open and no clue why."""
    windows = _name_on('Windows', monkeypatch)
    linux = _name_on('Linux', monkeypatch)
    assert windows != linux
    assert 'Windows' in windows and 'Linux' in linux


@pytest.mark.parametrize('document', CUSTOMER_FACING)
def test_the_documents_name_the_file_that_is_actually_built(document, monkeypatch):
    """Three documents tell a customer what to double-click. They have to say
    what build.py produces, or they send them looking for a file that is not
    there."""
    text = (REPO / document).read_text(encoding='utf-8')
    for system in ('Windows', 'Linux'):
        name = _name_on(system, monkeypatch)
        assert name in text, f'{document} 里没有提到 {name}'
