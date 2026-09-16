#!/usr/bin/env python3
"""Lift the X2 off its studio backdrop, once, by hand.

    python3 tools/deployer/gui/cutout.py photo.png tools/deployer/gui/x2.png

Nothing imports this. It exists so that the next person with a better
photograph can produce `x2.png` the same way instead of guessing, and so the
choices below are written down rather than lost with the shell history.

The robot is mostly white and so is the backdrop, so neither a brightness
threshold nor a flood fill that compares each pixel to its neighbour works:
both walk straight through the torso and hollow it out. What does work is that
the backdrop is a smooth gradient. Model it from the left and right margins of
each row, keep only pixels close to that model, and require them to be
reachable from the frame's edge -- then white *inside* the robot is safe,
because nothing outside can reach it.

Needs numpy, scipy and Pillow. The deployer itself uses none of the three.
"""
from __future__ import annotations

import sys

import numpy as np
from PIL import Image
from scipy import ndimage

FLAT = 12.0          # how far a pixel may sit from the backdrop and still be it
SPECK = 400          # an opaque island smaller than this is compression noise
MARGIN = 10          # columns each side that are backdrop by definition
BOX = (520, 720)     # big enough for the panel on a high-density screen


def cut(source: Image.Image) -> Image.Image:
    """The robot alone, on a transparent ground, cropped to its own edges."""
    rgb = np.asarray(source.convert('RGB'))
    a = rgb.astype(np.float32)
    _, width, _ = a.shape

    left = np.median(a[:, :MARGIN, :], axis=1)
    right = np.median(a[:, -MARGIN:, :], axis=1)
    ramp = np.linspace(0.0, 1.0, width, dtype=np.float32)[None, :, None]
    backdrop = left[:, None, :] * (1 - ramp) + right[:, None, :] * ramp
    close = np.abs(a - backdrop).max(axis=2) <= FLAT

    labels, _ = ndimage.label(close)
    touching = set(labels[0, :]) | set(labels[-1, :])
    touching |= set(labels[:, 0]) | set(labels[:, -1])
    touching.discard(0)
    outside = np.isin(labels, list(touching))

    islands, count = ndimage.label(~outside)
    areas = ndimage.sum_labels(~outside, islands, index=range(1, count + 1))
    specks = [i + 1 for i, area in enumerate(areas) if area < SPECK]
    if specks:
        outside |= np.isin(islands, specks)
    print(f'{count} opaque regions, {len(specks)} of them specks')

    alpha = np.where(outside, 0, 255).astype(np.uint8)
    cutout = Image.fromarray(np.dstack([rgb, alpha]), 'RGBA')
    return cutout.crop(cutout.getbbox())


def shrink(image: Image.Image, box: tuple) -> Image.Image:
    """Downscale, premultiplied, which is also what softens the edge.

    Resizing RGBA as it stands mixes the colour of fully transparent pixels --
    here the pale backdrop -- into the robot's outline. On a white panel that
    is invisible; on a dark one it is a light halo around every limb.
    """
    scale = min(box[0] / image.size[0], box[1] / image.size[1], 1.0)
    target = (round(image.size[0] * scale), round(image.size[1] * scale))

    a = np.asarray(image.convert('RGBA')).astype(np.float32) / 255.0
    lit = np.dstack([a[..., :3] * a[..., 3:4], a[..., 3:4]])
    small = np.asarray(
        Image.fromarray((lit * 255).astype(np.uint8), 'RGBA')
        .resize(target, Image.LANCZOS)).astype(np.float32) / 255.0

    alpha = np.clip(small[..., 3:4], 0.0, 1.0)
    colour = np.clip(small[..., :3] / np.maximum(alpha, 1e-4), 0.0, 1.0)
    return Image.fromarray(
        (np.dstack([colour, alpha]) * 255).astype(np.uint8), 'RGBA')


def main() -> int:
    if len(sys.argv) != 3:
        print('用法:cutout.py <照片> <输出.png>', file=sys.stderr)
        return 2
    out = shrink(cut(Image.open(sys.argv[1])), BOX)
    out.save(sys.argv[2], optimize=True)
    print(f'saved {sys.argv[2]} {out.size[0]}x{out.size[1]}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
