"""Finding the detector weights, and fetching them when they are not here yet.

Without these the node still starts, logs one warning, and falls back to a
2005 pedestrian detector that misses people standing in front of it. Nothing
else goes wrong -- which is exactly why this is worth being careful about: a
robot that cannot see is indistinguishable, from the outside, from a quiet
afternoon.

The awkward part is that the customer's laptop is cabled to the robot at the
moment it needs them, and may have no way out to the internet at all. So the
search comes first and the download last: a copy next to the deployer, a copy
in the user's cache, a copy in the repository, and only then the network.
"""
from __future__ import annotations

import hashlib
import os
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional

PROTOTXT = 'MobileNetSSD_deploy.prototxt'
CAFFEMODEL = 'MobileNetSSD_deploy.caffemodel'
NAMES = (PROTOTXT, CAFFEMODEL)

# Pinned: these are the bytes the deployment has been proven with, and a
# silently different model is a silently different robot.
SHA256 = {
    PROTOTXT: '2d180f723b3109e21f8287f6b3c691390d07b60eed998327cd3259ffa0e50608',
    CAFFEMODEL: '52eed8be80522c152a17fb56740de705b79881bde1a167e0e747310523685fc7',
}

URLS = {
    PROTOTXT: ('https://raw.githubusercontent.com/chuanqi305/MobileNet-SSD/'
               'daef68a6c2f5fbb8c88404266aa28180646d17e0/voc/MobileNetSSD_deploy.prototxt'),
    CAFFEMODEL: ('https://raw.githubusercontent.com/PINTO0309/MobileNet-SSD-RealSense/'
                 'master/caffemodel/MobileNetSSD/MobileNetSSD_deploy.caffemodel'),
}


@dataclass(frozen=True)
class Weights:
    directory: Optional[str]
    missing: List[str]

    @property
    def ready(self) -> bool:
        return self.directory is not None and not self.missing


def cache_dir() -> Path:
    """Where a downloaded copy is kept between deployments.

    Under the user's home rather than beside the program: on Windows the
    deployer may well sit in Program Files, which is not writable.
    """
    return Path.home() / '.x2-deployer' / 'models'


def search_paths(extra: Optional[str] = None) -> List[Path]:
    here = Path(__file__).resolve()
    repo = here.parents[3]                      # .../tools/deployer/core/ -> repo
    candidates = [
        Path(extra) if extra else None,
        cache_dir(),
        here.parents[2] / 'models',             # bundled next to the deployer
        repo / 'models',
        Path.home() / 'x2-models',              # where the CLI runbook puts them
    ]
    return [c for c in candidates if c is not None]


def _complete(directory: Path) -> bool:
    return all((directory / name).is_file() for name in NAMES)


def locate(extra: Optional[str] = None) -> Weights:
    """The first directory that has both files, or what is missing from the cache."""
    for candidate in search_paths(extra):
        if _complete(candidate):
            return Weights(str(candidate), [])
    target = cache_dir()
    return Weights(None, [n for n in NAMES if not (target / n).is_file()])


def verify(directory) -> List[str]:
    """Names whose contents are not what they should be. Empty means good."""
    bad = []
    for name in NAMES:
        path = Path(directory) / name
        if not path.is_file():
            bad.append(name)
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != SHA256[name]:
            bad.append(name)
    return bad


def download(on_progress: Optional[Callable[[str, int], None]] = None,
             target: Optional[Path] = None) -> str:
    """Fetch both files into the cache, reporting percent per file.

    Raises with a sentence rather than a URLError: the person reading it is
    being told to find a network, not to debug one.
    """
    target = Path(target or cache_dir())
    target.mkdir(parents=True, exist_ok=True)

    for name in NAMES:
        destination = target / name
        if destination.is_file() and not verify_one(destination, name):
            continue
        try:
            _fetch(URLS[name], destination, name, on_progress)
        except Exception as exc:                        # noqa: BLE001
            raise RuntimeError(
                f'下载识别模型失败({name})。请确认这台电脑能上网,'
                f'或者在有网的地方先下载一次。') from exc

    bad = verify(target)
    if bad:
        raise RuntimeError(f'下载的识别模型文件校验不通过:{", ".join(bad)}')
    return str(target)


def verify_one(path: Path, name: str) -> bool:
    """True when the file is wrong and should be fetched again."""
    return hashlib.sha256(path.read_bytes()).hexdigest() != SHA256[name]


def _fetch(url: str, destination: Path, name: str,
           on_progress: Optional[Callable[[str, int], None]]) -> None:
    partial = destination.with_suffix(destination.suffix + '.part')
    with urllib.request.urlopen(url, timeout=30) as response:
        total = int(response.headers.get('Content-Length') or 0)
        done = 0
        with open(partial, 'wb') as handle:
            while True:
                chunk = response.read(64 * 1024)
                if not chunk:
                    break
                handle.write(chunk)
                done += len(chunk)
                if on_progress is not None and total:
                    on_progress(name, int(done * 100 / total))
    os.replace(partial, destination)
    if on_progress is not None:
        on_progress(name, 100)
