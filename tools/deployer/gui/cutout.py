#!/usr/bin/env python3
"""Turn a cut-out photograph of the robot into the picture the window shows.

    python3 tools/deployer/gui/cutout.py cutout.png tools/deployer/gui/x2.png \
        --detail photo.png

Nothing imports this. It exists so the next person with a better photograph
can produce `x2.png` without guessing, and so the two things that went wrong
the first time are written down rather than repeated.

**Cut the background out with a matting tool, not with code here.** The first
version of this script modelled the pale studio backdrop from the margins of
each row and flood-filled everything close to it. On a robot that is mostly
white, parts of the shin shade to within a few levels of the backdrop, and
they were cut away with it -- the robot came out missing a piece of leg.
Matting white-on-white is a real problem and a background remover already
solves it. Feed it the result.

`--detail` is the one thing worth doing here: a background remover hands back
a downscaled image, so its alpha is lifted onto the full-resolution original
and only the matte is interpolated, never the robot.

Needs numpy and Pillow, neither of which the deployer itself uses.
"""
from __future__ import annotations

import argparse

import numpy as np
from PIL import Image

BOX = (520, 720)     # big enough for the panel on a high-density screen


def lift(shape: Image.Image, detail: Image.Image) -> Image.Image:
    """The transparency of one picture over the colour of a sharper one."""
    if shape.size != detail.size:
        scale = detail.size[0] / shape.size[0]
        leaning = abs(detail.size[1] / shape.size[1] - scale)
        if leaning > 0.01:
            raise SystemExit('两张图的长宽比不一样,对不上。')
        shape = shape.resize(detail.size, Image.LANCZOS)
    out = detail.convert('RGB')
    out.putalpha(shape.convert('RGBA').getchannel('A'))
    return out


def shrink(image: Image.Image, box: tuple) -> Image.Image:
    """Downscale with the alpha premultiplied, never upscaling.

    Resizing RGBA as it stands mixes the colour of transparent pixels -- here
    the pale backdrop the robot was lifted off -- into its outline. On a white
    panel that is invisible; on a dark one it is a halo around every limb.
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
    ap = argparse.ArgumentParser(description='生成界面里的机器人图片')
    ap.add_argument('source', help='已经去掉背景的 PNG')
    ap.add_argument('output', help='写到哪里,通常是 gui/x2.png')
    ap.add_argument('--detail', help='原始高分辨率照片,用它的清晰度')
    args = ap.parse_args()

    picture = Image.open(args.source).convert('RGBA')
    if picture.getchannel('A').getextrema()[0] == 255:
        raise SystemExit('这张图没有透明背景,先用抠图工具去背景。')
    if args.detail:
        picture = lift(picture, Image.open(args.detail))

    picture = picture.crop(picture.getbbox())
    out = shrink(picture, BOX)
    out.save(args.output, optimize=True)
    print(f'saved {args.output} {out.size[0]}x{out.size[1]}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
