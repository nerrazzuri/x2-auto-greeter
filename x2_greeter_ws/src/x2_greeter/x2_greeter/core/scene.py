"""What the robot can say about the people in front of it, and who it addresses.

This is deliberately not an extension of core/detection.py. gate_detections()
answers "is there one person worth greeting", which is the right question for
Phase 1 and the wrong one here: the addressing rule has to compare everybody
in frame before it can decide whether the robot is talking to a person or to
a group. So this module composes the same pure median_depth_m() helper and
does its own gating, and detection.py is left exactly as it is.

Nothing here identifies anyone. A "person" is a box, a distance, and an
estimate of how tall the thing in the box is. Stature is used for one purpose
only -- deciding whether to talk to a child the way you talk to a child --
and is never stated aloud, never stored, and never used to guess an age.
"""
from __future__ import annotations

import enum
import math
from dataclasses import dataclass
from typing import Optional, Sequence, Tuple

from x2_greeter.core.detection import median_depth_m
from x2_greeter.core.types import BBox, RawDetection

_CHILD_BANDS = frozenset({'child', 'toddler', 'infant'})


class AddressingMode(str, enum.Enum):
    """Who the robot believes it is speaking to. Fixed for a whole session."""

    INDIVIDUAL = 'individual'
    GROUP = 'group'


@dataclass(frozen=True)
class Person:
    bbox: Tuple[int, int, int, int]
    confidence: float
    distance_m: float
    center_offset: float          # 0.0 dead centre, 1.0 at the frame edge
    stature_m: Optional[float]
    likely_child: bool


@dataclass(frozen=True)
class SceneConfig:
    confidence_min: float = 0.5
    individual_max_m: float = 2.0
    # The horizon for *reading the room*, deliberately wider than Phase 1's
    # 3.0 m greeting trigger: somebody standing four metres back is part of
    # the group the robot is addressing even though they would not on their
    # own have started a conversation.
    scene_max_m: float = 5.0
    child_stature_max_m: float = 1.35
    vertical_fov_rad: float = 1.1868   # 68 degrees, head RGB-D colour


@dataclass(frozen=True)
class SceneSnapshot:
    people: Tuple[Person, ...]
    subject: Optional[Person]
    mode: AddressingMode
    at_s: float

    @property
    def person_count(self) -> int:
        return len(self.people)

    @property
    def closest_m(self) -> Optional[float]:
        return min((p.distance_m for p in self.people), default=None)

    @property
    def has_child(self) -> bool:
        return any(p.likely_child for p in self.people)


def stature_m(bbox, image_height: int, distance_m: float,
              vertical_fov_rad: float = 1.1868) -> Optional[float]:
    """How tall the thing in the box is, in metres, or None if unknowable.

    Assumes the person is upright and fully in frame. Both assumptions break
    often -- a cropped box reads short, a crouching adult reads short -- which
    is exactly why the result only ever *softens* the robot's language and
    never triggers an action.
    """
    height = int(image_height)
    distance = float(distance_m)
    if height <= 0 or not math.isfinite(distance) or distance <= 0.0:
        return None
    _, y1, _, y2 = bbox
    box_height = abs(int(y2) - int(y1))
    if box_height <= 0:
        return None
    angular = (box_height / height) * float(vertical_fov_rad)
    return 2.0 * distance * math.tan(angular / 2.0)


def choose_subject(people: Sequence[Person],
                   individual_max_m: float) -> Tuple[Optional[Person], AddressingMode]:
    """Decide who is being addressed. Called once, at session start.

    Anyone closer than individual_max_m makes this a conversation with a
    person; the subject is the most central of *those*, not the most central
    overall. Otherwise it is a group, and the subject is only the person the
    robot happens to be looking at.
    """
    if not people:
        return None, AddressingMode.GROUP
    near = [p for p in people if p.distance_m < float(individual_max_m)]
    if near:
        return min(near, key=lambda p: abs(p.center_offset)), AddressingMode.INDIVIDUAL
    return (min(people, key=lambda p: abs(p.center_offset)), AddressingMode.GROUP)


def child_mode(local_child: bool, model_age_band: Optional[str]) -> bool:
    """Should the robot speak to this person the way it speaks to a child?

    Either signal is enough. Both are weak on their own -- the stature
    heuristic mistakes a crouching adult for a child, the model mistakes a
    short adult for one -- and the failure modes are not symmetric: child mode
    only ever removes things the robot is allowed to say, so engaging it when
    unsure costs a slightly simpler sentence, while missing it costs the whole
    reason the mode exists.
    """
    if local_child:
        return True
    band = (model_age_band or '').strip().lower()
    return band in _CHILD_BANDS


def observe(raws, rgb_shape, depth, depth_scale: float, config: SceneConfig,
            at_s: float) -> SceneSnapshot:
    """Turn raw detections plus a depth frame into a scene snapshot.

    A detection with no usable depth is dropped, not admitted with a guessed
    distance: that distance is what the 1.0 m gesture interlock is made of.

    raw.bbox is a core.types.BBox, not a plain tuple: it is passed to
    median_depth_m() as-is (which reads it by attribute), and converted to a
    plain (x1, y1, x2, y2) tuple once per detection for stature_m() and for
    Person.bbox, which is documented as a Tuple[int, int, int, int].
    """
    people = []
    image_height = int(rgb_shape[0])
    for raw in raws:
        if float(raw.confidence) < config.confidence_min:
            continue
        distance = median_depth_m(depth, raw.bbox, rgb_shape, depth_scale)
        if distance is None or not math.isfinite(distance):
            continue
        if distance <= 0.0 or distance > config.scene_max_m:
            continue
        b = raw.bbox
        box = (int(b.x1), int(b.y1), int(b.x2), int(b.y2))
        width = int(rgb_shape[1])
        centre = (box[0] + box[2]) / 2.0
        offset = ((centre - width / 2.0) / (width / 2.0)) if width > 0 else 0.0
        height_m = stature_m(box, image_height, distance,
                             config.vertical_fov_rad)
        people.append(Person(
            bbox=box,
            confidence=float(raw.confidence),
            distance_m=float(distance),
            center_offset=float(offset),
            stature_m=height_m,
            likely_child=(height_m is not None
                          and height_m < config.child_stature_max_m),
        ))

    subject, mode = choose_subject(people, config.individual_max_m)
    return SceneSnapshot(people=tuple(people), subject=subject, mode=mode,
                         at_s=float(at_s))
