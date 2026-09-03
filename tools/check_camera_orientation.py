#!/usr/bin/env python3
"""Is the head camera mounted the right way up?

MobileNet-SSD does not recognise an inverted person, but it still recognises
inverted furniture. On a robot whose RGB-D module is mounted upside down that
produces the worst kind of failure: the node runs at full frame rate, the
detector returns nothing, and *not one log line is printed*. This script is
the check that tells them apart.

It samples live frames, runs the detector on the frame and on flipped copies,
and reports which orientation finds a person. Have somebody stand 2 m in front
of the robot while it runs.

    python3 tools/check_camera_orientation.py
    python3 tools/check_camera_orientation.py --seconds 30 --models ~/models

If `rot-180` finds a person and `as-is` does not, set `camera.rotate_180: true`
in config/greeter.yaml -- or, better, pass color_rotation:=180 and
depth_rotation:=180 to the camera driver, which fixes it for every consumer
rather than just this node.

No image is written to disk, here or anywhere else in this repository.
"""
from __future__ import annotations

import argparse
import os
import sys
import time

VOC_CLASSES = (
    'background', 'aeroplane', 'bicycle', 'bird', 'boat', 'bottle', 'bus', 'car',
    'cat', 'chair', 'cow', 'diningtable', 'dog', 'horse', 'motorbike', 'person',
    'pottedplant', 'sheep', 'sofa', 'train', 'tvmonitor',
)
PERSON = VOC_CLASSES.index('person')

DEFAULT_RGB = '/aima/hal/sensor/rgbd_head_front/rgb_image'
DEFAULT_DEPTH = '/aima/hal/sensor/rgbd_head_front/depth_image'


def variants(cv2):
    """Orientation candidates, as (name, transform) pairs."""
    return (
        ('as-is', lambda im: im),
        ('flip-vert', lambda im: cv2.flip(im, 0)),
        ('flip-horiz', lambda im: cv2.flip(im, 1)),
        ('rot-180', lambda im: cv2.flip(im, -1)),
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--models', default=os.path.expanduser('~/models'),
                        help='directory holding the MobileNet-SSD files')
    parser.add_argument('--rgb-topic', default=DEFAULT_RGB)
    parser.add_argument('--depth-topic', default=DEFAULT_DEPTH)
    parser.add_argument('--seconds', type=float, default=25.0)
    parser.add_argument('--every', type=int, default=25,
                        help='sample one frame in N (the detector is not free)')
    args = parser.parse_args(argv)

    import cv2
    import rclpy
    from rclpy.node import Node

    from x2_greeter.ros.frame_source import FrameSource

    prototxt = os.path.join(args.models, 'MobileNetSSD_deploy.prototxt')
    weights = os.path.join(args.models, 'MobileNetSSD_deploy.caffemodel')
    for path in (prototxt, weights):
        if not os.path.exists(path):
            print(f'missing {path}; run tools/fetch_model.py --dest {args.models}',
                  file=sys.stderr)
            return 2
    net = cv2.dnn.readNetFromCaffe(prototxt, weights)

    best = {name: {} for name, _ in variants(cv2)}
    counted = [0]

    def infer(image):
        blob = cv2.dnn.blobFromImage(cv2.resize(image, (300, 300)), 0.007843,
                                     (300, 300), 127.5)
        net.setInput(blob)
        return net.forward().reshape(-1, 7)

    def on_frame(bgr, depth, stamp):
        counted[0] += 1
        if counted[0] % args.every:
            return
        for name, transform in variants(cv2):
            for row in infer(transform(bgr)):
                class_id, confidence = int(row[1]), float(row[2])
                if confidence > 0.20 and 0 <= class_id < len(VOC_CLASSES):
                    label = VOC_CLASSES[class_id]
                    best[name][label] = max(best[name].get(label, 0.0), confidence)

    rclpy.init()
    node = Node('x2_camera_orientation_check')
    source = FrameSource(node, args.rgb_topic, args.depth_topic, on_frame)
    print(f'sampling for {args.seconds:.0f}s -- stand about 2 m in front of the robot')
    deadline = time.time() + args.seconds
    while time.time() < deadline:
        rclpy.spin_once(node, timeout_sec=0.2)
    source.destroy()
    node.destroy_node()
    rclpy.shutdown()

    print(f'\nsynchronised frames received: {counted[0]}')
    if counted[0] == 0:
        print('\nNo frames at all. That is a camera or topic problem, not an '
              'orientation one:\n'
              '  - check the topic names above against `ros2 topic list`\n'
              '  - on an AgiBot X2, source the robot\'s own environment first '
              '(entry/cfg/basic_env.sh):\n'
              '    without its FASTRTPS_DEFAULT_PROFILES_FILE the topics still '
              'list but no data ever arrives')
        return 1

    print()
    for name, _ in variants(cv2):
        found = best[name]
        person = found.get('person')
        verdict = f'PERSON {person:.2f}' if person else 'no person'
        others = ', '.join(f'{k}={v:.2f}'
                           for k, v in sorted(found.items(), key=lambda kv: -kv[1])[:4])
        print(f'  {name:<11} {verdict:<14} | {others or "(nothing detected)"}')

    upright = best['as-is'].get('person', 0.0)
    turned = best['rot-180'].get('person', 0.0)
    print()
    if turned > 0.5 and upright < 0.3:
        print('VERDICT: the camera is mounted upside down.\n'
              '  Set camera.rotate_180: true in config/greeter.yaml, or pass\n'
              '  color_rotation:=180 depth_rotation:=180 to the camera driver.')
    elif upright > 0.5:
        print('VERDICT: orientation is correct -- a person was found in the '
              'frame as published.')
    else:
        print('VERDICT: inconclusive -- no orientation found a person.\n'
              '  Was somebody standing 1-3 m in front, roughly centred? The '
              'other classes\n  listed above tell you what the camera is '
              'actually pointed at.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
