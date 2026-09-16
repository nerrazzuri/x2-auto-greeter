#!/usr/bin/env python3
"""Download the MobileNet-SSD Caffe weights used by the local person detector.

Nothing here is required to run the greeter: without these files the node
falls back to OpenCV's built-in HOG pedestrian detector. Fetching them makes
detection faster and more reliable.

Usage:
    python tools/fetch_model.py --dest models
    python tools/fetch_model.py --dest models --prototxt-url URL --weights-url URL
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
import urllib.request

# Pinned to a commit rather than to master, and kept identical to the pair in
# tools/deployer/core/assets.py -- a test compares the two, because the copy
# that was allowed to drift is the one that stopped working.
_COMMIT = 'bb17b6c3eef36d80be441ae8e5339be66e8e3b7a'
_SOURCE = f'https://raw.githubusercontent.com/chuanqi305/MobileNet-SSD/{_COMMIT}/'

DEFAULT_PROTOTXT_URL = _SOURCE + 'deploy.prototxt'
DEFAULT_WEIGHTS_URL = _SOURCE + 'mobilenet_iter_73000.caffemodel'
FILENAMES = ('MobileNetSSD_deploy.prototxt', 'MobileNetSSD_deploy.caffemodel')


def download(url: str, path: str) -> str:
    print(f'fetching {url}\n     -> {path}')
    with urllib.request.urlopen(url) as response, open(path, 'wb') as handle:
        digest = hashlib.sha256()
        while True:
            chunk = response.read(1 << 20)
            if not chunk:
                break
            digest.update(chunk)
            handle.write(chunk)
    return digest.hexdigest()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dest', default='models', help='directory to write the model into')
    parser.add_argument('--prototxt-url', default=DEFAULT_PROTOTXT_URL)
    parser.add_argument('--weights-url', default=DEFAULT_WEIGHTS_URL)
    args = parser.parse_args(argv)

    os.makedirs(args.dest, exist_ok=True)
    for url, name in ((args.prototxt_url, FILENAMES[0]), (args.weights_url, FILENAMES[1])):
        path = os.path.join(args.dest, name)
        if os.path.isfile(path):
            print(f'{path} already present, skipping')
            continue
        try:
            digest = download(url, path)
        except Exception as exc:                       # noqa: BLE001 - report and stop
            if os.path.isfile(path):
                os.remove(path)
            print(f'failed to fetch {url}: {exc}', file=sys.stderr)
            print('The greeter still runs without this file, using the HOG detector.',
                  file=sys.stderr)
            return 1
        print(f'  sha256 {digest}')

    print(f'\nDone. Set detect.model_dir to {os.path.abspath(args.dest)} in '
          f'config/greeter.yaml.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
