"""Where the vision model is fetched from, and whether those addresses work.

This file exists because both download URLs were wrong for the entire life of
the feature and no test noticed. The reason no test noticed is structural: the
download only runs on a machine that has no copy of the weights yet, and every
machine anyone had built or tested on already had one. The code path was dead
in practice, so a unit test that mocked urlopen would have passed just as
happily with a 404 behind it.

So the checks here are the ones that do not mock:

  * the two places that name the URLs still name the same ones -- the copy
    that was allowed to drift is the copy that broke
  * the URLs are pinned to a commit, not to a branch, because the checksums
    are pinned and a branch is not
  * and, when asked, an actual download of both files whose bytes have to
    match the checksums. That is the test that would have caught this, and it
    is the only one that can.

The real download moves 23 MB, so it runs only on request:

    X2_NETWORK_TESTS=1 pytest test/test_deployer_assets.py
"""
import hashlib
import os
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO / 'tools'))

from deployer.core import assets                                # noqa: E402

NEEDS_NETWORK = pytest.mark.skipif(
    not os.environ.get('X2_NETWORK_TESTS'),
    reason='下载 23 MB;要跑就设 X2_NETWORK_TESTS=1')


def _fetch_model():
    """tools/fetch_model.py, which is not in a package."""
    import importlib.util

    path = REPO / 'tools' / 'fetch_model.py'
    spec = importlib.util.spec_from_file_location('fetch_model', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_two_places_that_name_the_model_agree():
    """The GUI downloads it and so does the command-line script. When they
    disagree one of them is wrong, and it will be the one nobody runs."""
    cli = _fetch_model()
    assert cli.DEFAULT_PROTOTXT_URL == assets.URLS[assets.PROTOTXT]
    assert cli.DEFAULT_WEIGHTS_URL == assets.URLS[assets.CAFFEMODEL]


@pytest.mark.parametrize('name', assets.NAMES)
def test_the_urls_are_pinned_to_a_commit(name):
    """A branch keeps moving and the checksum does not, so a `master` URL is
    a download that works until somebody else pushes and then never again."""
    url = assets.URLS[name]
    assert url.startswith('https://'), '模型必须走 https'
    assert re.search(r'/[0-9a-f]{40}/', url), f'{name} 的地址没有钉死在某个 commit'
    assert '/master/' not in url and '/main/' not in url


@pytest.mark.parametrize('name', assets.NAMES)
def test_every_file_we_fetch_has_a_checksum(name):
    """Without one, a captive portal's login page downloads as a model and
    the robot silently falls back to a detector that cannot see people."""
    assert len(assets.SHA256[name]) == 64
    assert assets.URLS[name].rsplit('/', 1)[-1]


@NEEDS_NETWORK
def test_the_model_really_downloads_and_is_the_model_we_expect(tmp_path):
    """The whole thing, for real, into a temporary directory.

    Never into the cache: a passing test must not leave the weights behind,
    or the next person cannot reproduce the state this is guarding.
    """
    seen = []
    directory = assets.download(lambda n, p: seen.append((n, p)),
                                target=tmp_path)

    assert Path(directory) == tmp_path
    assert assets.verify(tmp_path) == [], '下下来的字节跟钉住的校验和对不上'
    for name in assets.NAMES:
        digest = hashlib.sha256((tmp_path / name).read_bytes()).hexdigest()
        assert digest == assets.SHA256[name]
    assert {n for n, _ in seen} == set(assets.NAMES), '两个文件都要报进度'
    assert max(p for _, p in seen) == 100
