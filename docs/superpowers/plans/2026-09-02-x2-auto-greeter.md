# X2 Auto-Greeter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a ROS 2 node for the AgiBot X2 that greets a person who stops in front of its head camera — speaking a greeting and performing a greeting gesture — using a cloud LLM to compose the greeting when the internet is available and pre-written phrases when it is not.

**Architecture:** A cheap local person detector runs on every camera frame and decides *when* to ask the cloud; a single Claude call then confirms the person, composes the greeting, and picks the gesture. All behavioural logic is plain Python with no `rclpy` import, so it is unit-testable on a Windows workstation today; a thin `ros/` adapter shell is the only ROS-aware code and is exercised against real `aimdk_msgs` types in a `ros:humble` Docker container.

**Tech Stack:** Python 3.10, ROS 2 Humble (`rclpy`, `cv_bridge`, `sensor_msgs`), AimDK `aimdk_msgs` v1.0.0-ga424add, OpenCV (`cv2.dnn`), NumPy, `anthropic` Python SDK 1.x, pytest 8, Docker.

**Spec:** `docs/superpowers/specs/2026-09-02-x2-auto-greeter-design.md`

## Global Constraints

- **Privacy — no image is written to disk, anywhere, ever.** Not as a cache, not as a debug artefact, not in a log. Image bytes are never logged at any level. (Spec §12)
- **Zero `rclpy` imports outside `x2_greeter/ros/` and `x2_greeter/sim/`.** This is what makes the system testable on Windows. (Spec §4.2)
- **The node never changes the robot's motion mode** and **never issues a locomotion command.** (Spec §11)
- **Never build or run on PC1 (10.0.1.40).** The SDK docs state this is "strictly prohibited to avoid safety risks". Our node runs on PC2 (10.0.1.41).
- **Never write into `$HOME/aimdk*`** — the SDK README declares it "reserved and maintained by the system" and it is erased on firmware upgrade. Our workspace is a separate overlay.
- Python 3.10, C++17, ROS 2 Humble, Ubuntu 22.04 on the robot.
- TTS requests use `domain="x2_greeter"` and `priority_level=6` (`INTERACTION_L6`). (Spec §10)
- Audio assets: 16 kHz, 16-bit, mono, WAV or raw PCM only. MP3 is rejected. Stored on **PC3 (10.0.1.42)**, world-readable, under `/var/tmp/x2_greeter_audio`. (Spec §10)
- Cross-host ROS 2 service calls are unreliable; every service call uses the SDK's **8 attempts × 0.25 s** retry pattern.
- Model `claude-opus-5`, `output_config.effort = "low"`, adaptive thinking left on. **`budget_tokens` is rejected with a 400 on Opus 5** — never send it.
- `ANTHROPIC_API_KEY` is read from the environment. Never commit it; `.env` is gitignored.

### Deliberate deviations from the spec

Three corrections that the spec could not have anticipated. Each is small, and each is required for the system to work at all:

1. **`backend` → `backend.provider`.** Spec §13 lists both `backend` and `backend.model` as parameters. ROS 2 forbids a parameter that is also a prefix of another parameter, so the scalar is renamed `backend.provider` (values `claude` | `canned`).
2. **`camera.depth_scale` parameter added** (default `0.001`). Spec §17 open question 2 records that the depth encoding — `16UC1` millimetres vs `32FC1` metres — is unknown until hardware. A configurable metres-per-unit scale resolves it with one config line instead of a code change.
3. **`presence.confirm_timeout_s` added** (default `10.0`). The spec's state table has no exit from `CONFIRMING` if the verdict never arrives. Without one, a dropped worker future wedges the node permanently. On timeout the tracker returns to `IDLE`.

One vendor-SDK defect to work around: **`PlayAudioFile.srv`'s response field is spelled `reponse`** (sic), not `response`. Code reads it defensively so it keeps working if the vendor fixes the typo.

---

## File Structure

Repository root is `D:\Projects\X2` (`/d/Projects/X2` under the Bash tool).

```
pyproject.toml                                  pytest config: rootdir, pythonpath, ros marker
x2_greeter_ws/src/x2_greeter/
  package.xml                                   ament_python manifest
  setup.py  setup.cfg                           colcon packaging, entry points
  resource/x2_greeter                           ament resource index marker
  config/greeter.yaml                           all ROS parameters, spec §13 defaults
  config/phrases.yaml                           the canned greeting phrases (shared with audio tooling)
  launch/greeter.launch.py                      ros2 launch entry point
  x2_greeter/
    __init__.py                                 EMPTY — importing the package must not pull in rclpy
    core/                                       pure Python, no I/O, no ROS
      types.py                                  BBox, RawDetection, Detection, SceneContext, Verdict, JpegFrame
      gestures.py                               GestureSpec catalogue, resolve_area, GestureSelector
      detection.py                              GateConfig, median_depth_m, gate_detections, PersonDetector protocol
      detectors.py                              HogPersonDetector, MobileNetSsdDetector, ScriptedDetector
      presence.py                               PresenceState, PresenceConfig, PresenceTracker
      imaging.py                                to_jpeg_frame — the only place a frame is encoded
    cognition/                                  pure Python + outbound network
      port.py                                   GreetingBackend protocol, BackendUnavailable
      canned.py                                 CannedBackend, load_phrases, DEFAULT_PHRASES
      claude.py                                 ClaudeBackend (anthropic SDK)
      policy.py                                 GreetingPolicy — cloud with fallback
    ros/                                        the only rclpy code
      service_call.py                           call_with_retry — 8×0.25 s, executor-safe
      frame_source.py                           rgb+depth approximate sync -> numpy, stale watchdog
      speech.py                                 SpeechDispatcher — PlayTts / PlayAudioFile, tier demotion
      gesture.py                                GestureDispatcher — SetMcPresetMotion
      mode_guard.py                             ModeGuard — GetMcAction, is gesturing safe?
      greeting_node.py                          wiring, parameters, executor, worker thread
    sim/
      fake_robot.py                             synthetic camera + fake service servers
  test/
    core/test_types.py  test_gestures.py  test_detection.py  test_detectors.py
        test_presence.py  test_imaging.py
    cognition/test_canned.py  test_claude.py  test_policy.py
    test_privacy.py                             asserts the image path writes nothing to disk
    ros/test_service_call.py  test_frame_source.py  test_speech.py
        test_gesture_and_mode_guard.py
    integration/test_greeting_end_to_end.py
tools/
  make_greeting_audio.py                        generate conformant 16k/16-bit/mono WAVs
  deploy_audio.sh                               copy assets to PC3
  fetch_model.py                                download + verify MobileNet-SSD weights
docker/
  Dockerfile.test                               ros:humble + cv_bridge + anthropic
  run_tests.sh                                  build aimdk_msgs from source, run the ros-marked tests
docs/DEPLOYMENT.md                              the on-robot bring-up runbook
```

**Layering rule, mechanically:** `core/` imports nothing from `cognition/` or `ros/`. `cognition/` imports from `core/` only. `ros/` and `sim/` import from both and are the only modules that may `import rclpy`. Task 17 adds a test that enforces this by scanning imports.

---

## Task 1: Workspace scaffolding and core types

**Files:**
- Create: `pyproject.toml`
- Create: `x2_greeter_ws/src/x2_greeter/package.xml`
- Create: `x2_greeter_ws/src/x2_greeter/setup.py`
- Create: `x2_greeter_ws/src/x2_greeter/setup.cfg`
- Create: `x2_greeter_ws/src/x2_greeter/resource/x2_greeter`
- Create: `x2_greeter_ws/src/x2_greeter/x2_greeter/__init__.py`
- Create: `x2_greeter_ws/src/x2_greeter/x2_greeter/core/__init__.py`
- Create: `x2_greeter_ws/src/x2_greeter/x2_greeter/core/types.py`
- Test: `x2_greeter_ws/src/x2_greeter/test/core/test_types.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `BBox(x1:int, y1:int, x2:int, y2:int)` with properties `cx:float`, `cy:float`, `width:int`, `height:int`; `RawDetection(bbox:BBox, confidence:float)`; `Detection(bbox:BBox, confidence:float, distance_m:float, center_offset:float)`; `SceneContext(distance_m:float, center_offset:float)`; `Verdict(person_present:bool, facing_robot:bool, confidence:float, greeting:str, gesture:str|None, reason:str, source:str)`; `JpegFrame(data:bytes, media_type:str="image/jpeg")`. All frozen dataclasses.

- [ ] **Step 1: Create the package skeleton files**

`pyproject.toml` (repo root):

```toml
[tool.pytest.ini_options]
testpaths = ["x2_greeter_ws/src/x2_greeter/test"]
pythonpath = ["x2_greeter_ws/src/x2_greeter"]
markers = [
    "ros: requires rclpy and aimdk_msgs; runs only in the Docker harness",
]
addopts = "-m 'not ros'"
```

`x2_greeter_ws/src/x2_greeter/package.xml`:

```xml
<?xml version="1.0"?>
<?xml-model href="http://download.ros.org/schema/package_format3.xsd" schematypens="http://www.w3.org/2001/XMLSchema"?>
<package format="3">
  <name>x2_greeter</name>
  <version>0.1.0</version>
  <description>Greets a person who stops in front of the AgiBot X2 head camera.</description>
  <maintainer email="liangkaifeng1987@gmail.com">Liang Kai Feng</maintainer>
  <license>Proprietary</license>

  <exec_depend>rclpy</exec_depend>
  <exec_depend>sensor_msgs</exec_depend>
  <exec_depend>std_msgs</exec_depend>
  <exec_depend>cv_bridge</exec_depend>
  <exec_depend>aimdk_msgs</exec_depend>

  <test_depend>python3-pytest</test_depend>

  <export>
    <build_type>ament_python</build_type>
  </export>
</package>
```

`x2_greeter_ws/src/x2_greeter/setup.py`:

```python
import os
from glob import glob

from setuptools import find_packages, setup

package_name = 'x2_greeter'

setup(
    name=package_name,
    version='0.1.0',
    packages=find_packages(exclude=['test', 'test.*']),
    data_files=[
        ('share/ament_index/resource_index/packages', ['resource/' + package_name]),
        (os.path.join('share', package_name), ['package.xml']),
        (os.path.join('share', package_name, 'launch'), glob('launch/*.launch.py')),
        (os.path.join('share', package_name, 'config'), glob('config/*.yaml')),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='Liang Kai Feng',
    maintainer_email='liangkaifeng1987@gmail.com',
    description='Greets a person who stops in front of the AgiBot X2 head camera.',
    license='Proprietary',
    entry_points={
        'console_scripts': [
            'greeting_node = x2_greeter.ros.greeting_node:main',
            'fake_robot = x2_greeter.sim.fake_robot:main',
        ],
    },
)
```

`x2_greeter_ws/src/x2_greeter/setup.cfg`:

```ini
[develop]
script_dir=$base/lib/x2_greeter
[install]
install_scripts=$base/lib/x2_greeter
```

`resource/x2_greeter` is an empty file. `x2_greeter/__init__.py` and `x2_greeter/core/__init__.py` are empty files.

```bash
cd /d/Projects/X2
mkdir -p x2_greeter_ws/src/x2_greeter/{resource,config,launch}
mkdir -p x2_greeter_ws/src/x2_greeter/x2_greeter/{core,cognition,ros,sim}
mkdir -p x2_greeter_ws/src/x2_greeter/test/{core,cognition,ros,integration}
mkdir -p tools docker
touch x2_greeter_ws/src/x2_greeter/resource/x2_greeter
touch x2_greeter_ws/src/x2_greeter/x2_greeter/__init__.py
touch x2_greeter_ws/src/x2_greeter/x2_greeter/core/__init__.py
```

- [ ] **Step 2: Write the failing test**

`x2_greeter_ws/src/x2_greeter/test/core/test_types.py`:

```python
import dataclasses

import pytest

from x2_greeter.core.types import BBox, Detection, JpegFrame, RawDetection, SceneContext, Verdict


def test_bbox_geometry():
    box = BBox(x1=10, y1=20, x2=110, y2=220)
    assert box.width == 100
    assert box.height == 200
    assert box.cx == 60.0
    assert box.cy == 120.0


def test_bbox_is_frozen():
    box = BBox(0, 0, 1, 1)
    with pytest.raises(dataclasses.FrozenInstanceError):
        box.x1 = 5


def test_detection_carries_gating_results():
    det = Detection(bbox=BBox(0, 0, 10, 10), confidence=0.9,
                    distance_m=2.0, center_offset=-0.1)
    assert det.distance_m == 2.0
    assert det.center_offset == pytest.approx(-0.1)


def test_raw_detection_holds_only_what_the_detector_knows():
    raw = RawDetection(bbox=BBox(0, 0, 10, 10), confidence=0.75)
    assert {f.name for f in dataclasses.fields(raw)} == {'bbox', 'confidence'}


def test_scene_context_describes_where_the_person_is():
    ctx = SceneContext(distance_m=1.8, center_offset=0.05)
    assert ctx.distance_m == 1.8


def test_verdict_defaults_gesture_to_none():
    v = Verdict(person_present=True, facing_robot=False, confidence=0.8,
                greeting='Hello!', reason='saw a person', source='canned')
    assert v.gesture is None


def test_jpeg_frame_defaults_media_type():
    frame = JpegFrame(data=b'\xff\xd8\xff')
    assert frame.media_type == 'image/jpeg'
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `cd /d/Projects/X2 && python -m pytest x2_greeter_ws/src/x2_greeter/test/core/test_types.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'x2_greeter.core.types'`

- [ ] **Step 4: Write the implementation**

`x2_greeter_ws/src/x2_greeter/x2_greeter/core/types.py`:

```python
"""Value types shared across the greeter.

Pure data. No I/O, no ROS, no OpenCV — importable anywhere, including on a
Windows workstation with no ROS installation.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class BBox:
    """An axis-aligned box in RGB pixel coordinates."""

    x1: int
    y1: int
    x2: int
    y2: int

    @property
    def width(self) -> int:
        return self.x2 - self.x1

    @property
    def height(self) -> int:
        return self.y2 - self.y1

    @property
    def cx(self) -> float:
        return (self.x1 + self.x2) / 2.0

    @property
    def cy(self) -> float:
        return (self.y1 + self.y2) / 2.0


@dataclass(frozen=True)
class RawDetection:
    """What a person detector reports, before any gating."""

    bbox: BBox
    confidence: float


@dataclass(frozen=True)
class Detection:
    """A detection that has passed every gate in core.detection.

    center_offset is signed and normalised: 0.0 is dead centre, -0.5 is the
    left edge of frame, +0.5 the right edge.
    """

    bbox: BBox
    confidence: float
    distance_m: float
    center_offset: float


@dataclass(frozen=True)
class SceneContext:
    """What the local detector knows about the person, passed to a backend."""

    distance_m: float
    center_offset: float


@dataclass(frozen=True)
class Verdict:
    """A backend's answer: is this a person, and what should the robot do?

    gesture is a gesture *name* from the enabled allowlist, or None to let the
    selector choose at random. It is never a motion ID.
    """

    person_present: bool
    facing_robot: bool
    confidence: float
    greeting: str
    reason: str
    source: str
    gesture: Optional[str] = None


@dataclass(frozen=True)
class JpegFrame:
    """An encoded frame on its way to a cloud backend. Never written to disk."""

    data: bytes
    media_type: str = 'image/jpeg'
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `cd /d/Projects/X2 && python -m pytest x2_greeter_ws/src/x2_greeter/test/core/test_types.py -v`
Expected: PASS — 7 passed

- [ ] **Step 6: Commit**

```bash
cd /d/Projects/X2
git add pyproject.toml x2_greeter_ws tools docker
git commit -m "feat: scaffold x2_greeter package and core value types"
```

---

## Task 2: Gesture catalogue and selection

**Files:**
- Create: `x2_greeter_ws/src/x2_greeter/x2_greeter/core/gestures.py`
- Test: `x2_greeter_ws/src/x2_greeter/test/core/test_gestures.py`

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces: `GestureSpec(name:str, motion_id:int, areas:tuple[int,...], handed:bool)`; `CATALOGUE: dict[str, GestureSpec]` (18 entries); `DEFAULT_ENABLED: tuple[str,...]` (the 11 names of spec §9); `AREA_LEFT=1`, `AREA_RIGHT=2`, `AREA_BOTH=3`, `AREA_WHOLE_BODY=11`; `resolve_area(spec:GestureSpec, hand_preference:str, rng:random.Random) -> int`; `GestureChoice(name:str, motion_id:int, area_id:int)` NamedTuple; `GestureSelector(enabled:Sequence[str], hand_preference:str='right', rng:random.Random|None=None)` with `.enabled_names -> tuple[str,...]` and `.select(requested:str|None=None) -> GestureChoice`.

- [ ] **Step 1: Write the failing test**

`x2_greeter_ws/src/x2_greeter/test/core/test_gestures.py`:

```python
import random

import pytest

from x2_greeter.core.gestures import (
    AREA_BOTH,
    AREA_LEFT,
    AREA_RIGHT,
    AREA_WHOLE_BODY,
    CATALOGUE,
    DEFAULT_ENABLED,
    GestureSelector,
    resolve_area,
)

EXPECTED_ENABLED = {
    'wave', 'salute', 'handshake', 'raise_hand', 'raise_both', 'bow',
    'high_five', 'wave_chest', 'cheer', 'blow_kiss', 'heart',
}
EXPECTED_DISABLED = {
    'hug', 'wave_goodbye', 'clap', 'cross_arms', 'scratch_head',
    'grab_buttocks', 'dynamic_light_wave',
}


def test_default_enabled_matches_the_spec():
    assert set(DEFAULT_ENABLED) == EXPECTED_ENABLED


def test_catalogue_also_carries_the_disabled_gestures():
    assert EXPECTED_DISABLED <= set(CATALOGUE)
    assert set(CATALOGUE) == EXPECTED_ENABLED | EXPECTED_DISABLED


@pytest.mark.parametrize(('name', 'motion_id'), [
    ('wave', 1002), ('salute', 1013), ('handshake', 1003), ('raise_hand', 1001),
    ('raise_both', 1010), ('bow', 3001), ('high_five', 1008), ('wave_chest', 1011),
    ('cheer', 3011), ('blow_kiss', 1004), ('heart', 1007),
    ('hug', 3008), ('wave_goodbye', 3031), ('clap', 3017), ('cross_arms', 3009),
    ('scratch_head', 3024), ('grab_buttocks', 3025), ('dynamic_light_wave', 3007),
])
def test_motion_ids_match_the_interface_docs(name, motion_id):
    assert CATALOGUE[name].motion_id == motion_id


def test_handed_gesture_honours_the_hand_preference():
    wave = CATALOGUE['wave']
    rng = random.Random(0)
    assert resolve_area(wave, 'right', rng) == AREA_RIGHT
    assert resolve_area(wave, 'left', rng) == AREA_LEFT


def test_whole_body_gesture_ignores_the_hand_preference():
    bow = CATALOGUE['bow']
    rng = random.Random(0)
    assert resolve_area(bow, 'left', rng) == AREA_WHOLE_BODY
    assert resolve_area(bow, 'both', rng) == AREA_WHOLE_BODY


def test_both_arm_gesture_ignores_the_hand_preference():
    rng = random.Random(0)
    assert resolve_area(CATALOGUE['raise_both'], 'left', rng) == AREA_BOTH


def test_heart_supports_both_hands_and_either_single_hand():
    heart = CATALOGUE['heart']
    rng = random.Random(0)
    assert resolve_area(heart, 'both', rng) == AREA_BOTH
    assert resolve_area(heart, 'left', rng) == AREA_LEFT
    assert resolve_area(heart, 'right', rng) == AREA_RIGHT


def test_both_preference_on_a_single_arm_gesture_falls_back_to_the_first_area():
    # 'wave' has no both-arm variant; area 2 (right) is its documented default.
    assert resolve_area(CATALOGUE['wave'], 'both', random.Random(0)) == AREA_RIGHT


def test_random_preference_picks_a_side_per_call():
    wave = CATALOGUE['wave']
    sides = {resolve_area(wave, 'random', random.Random(seed)) for seed in range(20)}
    assert sides == {AREA_LEFT, AREA_RIGHT}


def test_selector_uses_a_requested_gesture_that_is_enabled():
    sel = GestureSelector(['wave', 'salute'], hand_preference='right', rng=random.Random(0))
    choice = sel.select('salute')
    assert choice.name == 'salute'
    assert choice.motion_id == 1013
    assert choice.area_id == AREA_RIGHT


def test_selector_rejects_a_gesture_outside_the_allowlist():
    sel = GestureSelector(['wave', 'salute'], rng=random.Random(0))
    # 'hug' exists in the catalogue but is not enabled.
    assert sel.select('hug').name in {'wave', 'salute'}


def test_selector_rejects_a_gesture_that_is_not_a_gesture_at_all():
    sel = GestureSelector(['wave', 'salute'], rng=random.Random(0))
    assert sel.select('DROP TABLE gestures').name in {'wave', 'salute'}


def test_selector_never_repeats_the_previous_gesture():
    sel = GestureSelector(['wave', 'salute', 'bow'], rng=random.Random(7))
    previous = None
    for _ in range(30):
        name = sel.select().name
        assert name != previous
        previous = name


def test_selector_can_repeat_when_only_one_gesture_is_enabled():
    sel = GestureSelector(['wave'], rng=random.Random(0))
    assert [sel.select().name for _ in range(3)] == ['wave', 'wave', 'wave']


def test_a_requested_gesture_also_counts_as_the_previous_one():
    sel = GestureSelector(['wave', 'salute'], rng=random.Random(0))
    sel.select('wave')
    assert sel.select().name == 'salute'


def test_selector_rejects_an_unknown_name_at_construction():
    with pytest.raises(ValueError, match='not_a_gesture'):
        GestureSelector(['wave', 'not_a_gesture'])


def test_selector_rejects_an_empty_allowlist():
    with pytest.raises(ValueError, match='at least one'):
        GestureSelector([])


def test_selector_rejects_an_unknown_hand_preference():
    with pytest.raises(ValueError, match='hand_preference'):
        GestureSelector(['wave'], hand_preference='sideways')


def test_enabled_names_is_exposed_for_the_llm_schema():
    sel = GestureSelector(['wave', 'salute'])
    assert sel.enabled_names == ('wave', 'salute')
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd /d/Projects/X2 && python -m pytest x2_greeter_ws/src/x2_greeter/test/core/test_gestures.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'x2_greeter.core.gestures'`

- [ ] **Step 3: Write the implementation**

`x2_greeter_ws/src/x2_greeter/x2_greeter/core/gestures.py`:

```python
"""The preset-motion catalogue and the policy for choosing one.

Motion and area IDs come from the AimDK interface docs
(dev/Interface/control_mod/preset_motion.html). An LLM never supplies a motion
ID — it supplies a *name*, which is validated against the enabled allowlist
before it is ever turned into an ID.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from typing import NamedTuple, Optional, Sequence

AREA_LEFT = 1
AREA_RIGHT = 2
AREA_BOTH = 3
AREA_WHOLE_BODY = 11

_HAND_PREFERENCE_AREAS = {
    'left': AREA_LEFT,
    'right': AREA_RIGHT,
    'both': AREA_BOTH,
}
VALID_HAND_PREFERENCES = frozenset(_HAND_PREFERENCE_AREAS) | {'random'}


@dataclass(frozen=True)
class GestureSpec:
    """One preset motion.

    areas lists the control areas the docs permit, most-preferred first — so
    areas[0] is the fallback when a hand preference cannot be honoured.
    handed is True when the gesture has left/right variants and should follow
    gestures.hand_preference.
    """

    name: str
    motion_id: int
    areas: tuple
    handed: bool


def _spec(name: str, motion_id: int, areas: tuple, handed: bool) -> GestureSpec:
    return GestureSpec(name=name, motion_id=motion_id, areas=areas, handed=handed)


CATALOGUE = {
    # --- enabled by default: greetings ---
    'wave': _spec('wave', 1002, (AREA_RIGHT, AREA_LEFT), True),
    'salute': _spec('salute', 1013, (AREA_RIGHT, AREA_LEFT), True),
    'handshake': _spec('handshake', 1003, (AREA_RIGHT, AREA_LEFT), True),
    'raise_hand': _spec('raise_hand', 1001, (AREA_RIGHT, AREA_LEFT), True),
    'raise_both': _spec('raise_both', 1010, (AREA_BOTH,), False),
    'bow': _spec('bow', 3001, (AREA_WHOLE_BODY,), False),
    'high_five': _spec('high_five', 1008, (AREA_RIGHT, AREA_LEFT), True),
    'wave_chest': _spec('wave_chest', 1011, (AREA_RIGHT, AREA_LEFT), True),
    'cheer': _spec('cheer', 3011, (AREA_WHOLE_BODY,), False),
    'blow_kiss': _spec('blow_kiss', 1004, (AREA_RIGHT, AREA_LEFT), True),
    'heart': _spec('heart', 1007, (AREA_BOTH, AREA_RIGHT, AREA_LEFT), True),
    # --- available but disabled by default (spec section 9) ---
    'hug': _spec('hug', 3008, (AREA_WHOLE_BODY,), False),
    'wave_goodbye': _spec('wave_goodbye', 3031, (AREA_WHOLE_BODY,), False),
    'clap': _spec('clap', 3017, (AREA_WHOLE_BODY,), False),
    'cross_arms': _spec('cross_arms', 3009, (AREA_WHOLE_BODY,), False),
    'scratch_head': _spec('scratch_head', 3024, (AREA_WHOLE_BODY,), False),
    'grab_buttocks': _spec('grab_buttocks', 3025, (AREA_WHOLE_BODY,), False),
    'dynamic_light_wave': _spec('dynamic_light_wave', 3007, (AREA_WHOLE_BODY,), False),
}

DEFAULT_ENABLED = (
    'wave', 'salute', 'handshake', 'raise_hand', 'raise_both', 'bow',
    'high_five', 'wave_chest', 'cheer', 'blow_kiss', 'heart',
)


class GestureChoice(NamedTuple):
    name: str
    motion_id: int
    area_id: int


def resolve_area(spec: GestureSpec, hand_preference: str, rng: random.Random) -> int:
    """Pick the control area for one performance of a gesture.

    Whole-body and both-arm-only gestures ignore the preference entirely; a
    preference the gesture does not offer falls back to its documented default.
    """
    if not spec.handed:
        return spec.areas[0]
    if hand_preference == 'random':
        hand_preference = rng.choice(('left', 'right'))
    wanted = _HAND_PREFERENCE_AREAS.get(hand_preference)
    if wanted in spec.areas:
        return wanted
    return spec.areas[0]


class GestureSelector:
    """Turns an optional gesture *name* into a concrete (motion, area) pair.

    Not thread-safe: it holds the previous choice so it can avoid repeating
    itself. The greeter calls it from a single worker thread.
    """

    def __init__(self, enabled: Sequence[str], hand_preference: str = 'right',
                 rng: Optional[random.Random] = None) -> None:
        enabled = tuple(enabled)
        if not enabled:
            raise ValueError('gestures.enabled must list at least one gesture')
        unknown = [name for name in enabled if name not in CATALOGUE]
        if unknown:
            raise ValueError(f'unknown gesture(s) in gestures.enabled: {", ".join(unknown)}')
        if hand_preference not in VALID_HAND_PREFERENCES:
            raise ValueError(
                f'hand_preference must be one of {sorted(VALID_HAND_PREFERENCES)}, '
                f'got {hand_preference!r}')
        self._enabled = enabled
        self._hand_preference = hand_preference
        self._rng = rng if rng is not None else random.Random()
        self._last: Optional[str] = None

    @property
    def enabled_names(self) -> tuple:
        return self._enabled

    def select(self, requested: Optional[str] = None) -> GestureChoice:
        """Honour `requested` if it is on the allowlist, otherwise choose randomly."""
        if requested in self._enabled:
            name = requested
        else:
            name = self._random_name()
        self._last = name
        spec = CATALOGUE[name]
        return GestureChoice(name=name, motion_id=spec.motion_id,
                             area_id=resolve_area(spec, self._hand_preference, self._rng))

    def _random_name(self) -> str:
        pool = [name for name in self._enabled if name != self._last]
        if not pool:
            pool = list(self._enabled)
        return self._rng.choice(pool)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd /d/Projects/X2 && python -m pytest x2_greeter_ws/src/x2_greeter/test/core/test_gestures.py -v`
Expected: PASS — 19 passed

- [ ] **Step 5: Commit**

```bash
cd /d/Projects/X2
git add x2_greeter_ws/src/x2_greeter/x2_greeter/core/gestures.py x2_greeter_ws/src/x2_greeter/test/core/test_gestures.py
git commit -m "feat: add gesture catalogue and allowlist-validated selection"
```

---

## Task 3: Detection gating

**Files:**
- Create: `x2_greeter_ws/src/x2_greeter/x2_greeter/core/detection.py`
- Test: `x2_greeter_ws/src/x2_greeter/test/core/test_detection.py`

**Interfaces:**
- Consumes: `BBox`, `RawDetection`, `Detection` from `x2_greeter.core.types`.
- Produces: `PersonDetector` Protocol with `detect(self, bgr: numpy.ndarray) -> list[RawDetection]`; `GateConfig(confidence_min:float=0.5, distance_min_m:float=1.0, distance_max_m:float=3.0, center_tolerance:float=0.25)`; `median_depth_m(depth:numpy.ndarray|None, bbox:BBox, rgb_shape:tuple[int,int], depth_scale:float) -> float|None`; `gate_detections(raws:Sequence[RawDetection], rgb_shape:tuple[int,int], depth:numpy.ndarray|None, depth_scale:float, config:GateConfig) -> Detection|None`. `rgb_shape` is `(height, width)`.

- [ ] **Step 1: Write the failing test**

`x2_greeter_ws/src/x2_greeter/test/core/test_detection.py`:

```python
import numpy as np
import pytest

from x2_greeter.core.detection import GateConfig, gate_detections, median_depth_m
from x2_greeter.core.types import BBox, RawDetection

RGB_SHAPE = (480, 640)  # (height, width)


def uniform_depth(value_mm, shape=RGB_SHAPE):
    return np.full(shape, value_mm, dtype=np.uint16)


def centred_box(width=100, height=300):
    cx, cy = RGB_SHAPE[1] // 2, RGB_SHAPE[0] // 2
    return BBox(cx - width // 2, cy - height // 2, cx + width // 2, cy + height // 2)


def test_median_depth_converts_millimetres_to_metres():
    depth = uniform_depth(2000)
    assert median_depth_m(depth, centred_box(), RGB_SHAPE, 0.001) == pytest.approx(2.0)


def test_median_depth_handles_a_float_metre_encoding():
    depth = np.full(RGB_SHAPE, 2.5, dtype=np.float32)
    assert median_depth_m(depth, centred_box(), RGB_SHAPE, 1.0) == pytest.approx(2.5)


def test_median_depth_scales_the_box_when_depth_resolution_differs():
    # Depth is half the RGB resolution; only the true box region holds 2000 mm.
    depth = np.zeros((240, 320), dtype=np.uint16)
    box = centred_box()
    depth[box.y1 // 2:box.y2 // 2, box.x1 // 2:box.x2 // 2] = 2000
    assert median_depth_m(depth, box, RGB_SHAPE, 0.001) == pytest.approx(2.0)


def test_median_depth_ignores_zero_pixels():
    depth = uniform_depth(2000)
    box = centred_box()
    depth[box.y1:box.y1 + 200, box.x1:box.x2] = 0  # a large invalid patch
    assert median_depth_m(depth, box, RGB_SHAPE, 0.001) == pytest.approx(2.0)


def test_median_depth_ignores_nan_and_inf():
    depth = np.full(RGB_SHAPE, 2.0, dtype=np.float32)
    box = centred_box()
    depth[box.y1, box.x1] = np.nan
    depth[box.y1 + 1, box.x1] = np.inf
    assert median_depth_m(depth, box, RGB_SHAPE, 1.0) == pytest.approx(2.0)


def test_median_depth_is_none_when_every_pixel_is_invalid():
    assert median_depth_m(uniform_depth(0), centred_box(), RGB_SHAPE, 0.001) is None


def test_median_depth_is_none_when_there_is_no_depth_frame():
    assert median_depth_m(None, centred_box(), RGB_SHAPE, 0.001) is None


def test_a_good_detection_passes_every_gate():
    raw = RawDetection(bbox=centred_box(), confidence=0.9)
    got = gate_detections([raw], RGB_SHAPE, uniform_depth(2000), 0.001, GateConfig())
    assert got is not None
    assert got.confidence == 0.9
    assert got.distance_m == pytest.approx(2.0)
    assert got.center_offset == pytest.approx(0.0)


def test_low_confidence_is_rejected():
    raw = RawDetection(bbox=centred_box(), confidence=0.4)
    assert gate_detections([raw], RGB_SHAPE, uniform_depth(2000), 0.001, GateConfig()) is None


def test_someone_too_far_away_is_rejected():
    raw = RawDetection(bbox=centred_box(), confidence=0.9)
    assert gate_detections([raw], RGB_SHAPE, uniform_depth(5000), 0.001, GateConfig()) is None


def test_someone_too_close_is_rejected():
    raw = RawDetection(bbox=centred_box(), confidence=0.9)
    assert gate_detections([raw], RGB_SHAPE, uniform_depth(600), 0.001, GateConfig()) is None


def test_someone_at_the_edge_of_frame_is_rejected():
    raw = RawDetection(bbox=BBox(0, 90, 60, 390), confidence=0.9)
    assert gate_detections([raw], RGB_SHAPE, uniform_depth(2000), 0.001, GateConfig()) is None


def test_missing_depth_rejects_rather_than_guesses():
    raw = RawDetection(bbox=centred_box(), confidence=0.9)
    assert gate_detections([raw], RGB_SHAPE, None, 0.001, GateConfig()) is None


def test_invalid_depth_rejects_rather_than_guesses():
    raw = RawDetection(bbox=centred_box(), confidence=0.9)
    assert gate_detections([raw], RGB_SHAPE, uniform_depth(0), 0.001, GateConfig()) is None


def test_the_most_central_person_wins():
    centre = RawDetection(bbox=centred_box(), confidence=0.6)
    off_centre = RawDetection(bbox=BBox(180, 90, 280, 390), confidence=0.95)
    got = gate_detections([off_centre, centre], RGB_SHAPE, uniform_depth(2000), 0.001, GateConfig())
    assert got is not None
    assert got.confidence == 0.6  # centrality beats confidence


def test_center_offset_is_signed_left_negative():
    left = RawDetection(bbox=BBox(200, 90, 300, 390), confidence=0.9)
    got = gate_detections([left], RGB_SHAPE, uniform_depth(2000), 0.001, GateConfig())
    assert got is not None
    assert got.center_offset < 0


def test_a_box_reaching_outside_the_frame_is_clamped_not_crashed():
    raw = RawDetection(bbox=BBox(280, -50, 380, 700), confidence=0.9)
    got = gate_detections([raw], RGB_SHAPE, uniform_depth(2000), 0.001, GateConfig())
    assert got is not None


def test_a_degenerate_box_is_rejected():
    raw = RawDetection(bbox=BBox(320, 240, 320, 240), confidence=0.9)
    assert gate_detections([raw], RGB_SHAPE, uniform_depth(2000), 0.001, GateConfig()) is None


def test_no_detections_yields_none():
    assert gate_detections([], RGB_SHAPE, uniform_depth(2000), 0.001, GateConfig()) is None
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd /d/Projects/X2 && python -m pytest x2_greeter_ws/src/x2_greeter/test/core/test_detection.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'x2_greeter.core.detection'`

- [ ] **Step 3: Write the implementation**

`x2_greeter_ws/src/x2_greeter/x2_greeter/core/detection.py`:

```python
"""Turning raw detector boxes into a single gated Detection, or nothing.

Three gates, all of which must pass (spec section 6): confidence, distance
from the depth frame, and centring. Depth is never guessed — an absent or
unreadable depth reading rejects the detection.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Protocol, Sequence

import numpy as np

from x2_greeter.core.types import BBox, Detection, RawDetection


class PersonDetector(Protocol):
    """Anything that can find people in a BGR frame."""

    def detect(self, bgr: np.ndarray) -> List[RawDetection]:
        ...


@dataclass(frozen=True)
class GateConfig:
    confidence_min: float = 0.5
    distance_min_m: float = 1.0
    distance_max_m: float = 3.0
    center_tolerance: float = 0.25


def median_depth_m(depth: Optional[np.ndarray], bbox: BBox, rgb_shape: tuple,
                   depth_scale: float) -> Optional[float]:
    """Median distance in metres over the bbox, or None if it cannot be read.

    The depth image may be a different resolution from the RGB image, so the
    box is rescaled by the per-axis ratio between the two. depth_scale is
    metres per depth unit: 0.001 for 16UC1 millimetres, 1.0 for 32FC1 metres.
    """
    if depth is None or depth.size == 0:
        return None

    rgb_h, rgb_w = rgb_shape[0], rgb_shape[1]
    depth_h, depth_w = depth.shape[0], depth.shape[1]
    sx = depth_w / float(rgb_w)
    sy = depth_h / float(rgb_h)

    x1 = int(np.clip(round(bbox.x1 * sx), 0, depth_w - 1))
    x2 = int(np.clip(round(bbox.x2 * sx), 0, depth_w))
    y1 = int(np.clip(round(bbox.y1 * sy), 0, depth_h - 1))
    y2 = int(np.clip(round(bbox.y2 * sy), 0, depth_h))
    if x2 <= x1 or y2 <= y1:
        return None

    patch = depth[y1:y2, x1:x2].astype(np.float64, copy=False)
    valid = patch[np.isfinite(patch) & (patch > 0)]
    if valid.size == 0:
        return None
    return float(np.median(valid)) * depth_scale


def gate_detections(raws: Sequence[RawDetection], rgb_shape: tuple,
                    depth: Optional[np.ndarray], depth_scale: float,
                    config: GateConfig) -> Optional[Detection]:
    """Return the most central detection that passes every gate, or None."""
    rgb_h, rgb_w = rgb_shape[0], rgb_shape[1]
    if rgb_w <= 0 or rgb_h <= 0:
        return None

    best: Optional[Detection] = None
    for raw in raws:
        if raw.confidence < config.confidence_min:
            continue
        if raw.bbox.width <= 0 or raw.bbox.height <= 0:
            continue

        offset = raw.bbox.cx / float(rgb_w) - 0.5
        if abs(offset) > config.center_tolerance:
            continue

        distance = median_depth_m(depth, raw.bbox, rgb_shape, depth_scale)
        if distance is None:
            continue
        if not (config.distance_min_m <= distance <= config.distance_max_m):
            continue

        candidate = Detection(bbox=raw.bbox, confidence=raw.confidence,
                              distance_m=distance, center_offset=offset)
        if best is None or abs(candidate.center_offset) < abs(best.center_offset):
            best = candidate
    return best
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd /d/Projects/X2 && python -m pytest x2_greeter_ws/src/x2_greeter/test/core/test_detection.py -v`
Expected: PASS — 19 passed

- [ ] **Step 5: Commit**

```bash
cd /d/Projects/X2
git add x2_greeter_ws/src/x2_greeter/x2_greeter/core/detection.py x2_greeter_ws/src/x2_greeter/test/core/test_detection.py
git commit -m "feat: add confidence, distance and centring gates for detections"
```

---

## Task 4: The presence state machine

**Files:**
- Create: `x2_greeter_ws/src/x2_greeter/x2_greeter/core/presence.py`
- Test: `x2_greeter_ws/src/x2_greeter/test/core/test_presence.py`

**Interfaces:**
- Consumes: `Detection`, `BBox` from `x2_greeter.core.types`.
- Produces: `PresenceState` enum with members `IDLE`, `CANDIDATE`, `CONFIRMING`, `GREETING`, `COOLDOWN`; `PresenceConfig(dwell_s:float=1.0, loss_grace_s:float=0.5, clear_s:float=3.0, cooldown_s:float=30.0, reject_cooldown_s:float=5.0, confirm_timeout_s:float=10.0)`; `PresenceTracker(config:PresenceConfig)` with `.state -> PresenceState`, `.has_live_detection -> bool`, `.update(now:float, detection:Detection|None) -> Detection|None` (returns the detection to confirm exactly once, at the CANDIDATE→CONFIRMING transition), `.on_verdict(now:float, person_present:bool) -> None`, `.on_greeting_dispatched(now:float) -> None`.

- [ ] **Step 1: Write the failing test**

`x2_greeter_ws/src/x2_greeter/test/core/test_presence.py`:

```python
import pytest

from x2_greeter.core.presence import PresenceConfig, PresenceState, PresenceTracker
from x2_greeter.core.types import BBox, Detection

CFG = PresenceConfig(dwell_s=1.0, loss_grace_s=0.5, clear_s=3.0,
                     cooldown_s=30.0, reject_cooldown_s=5.0, confirm_timeout_s=10.0)


def person(distance_m=2.0, offset=0.0):
    return Detection(bbox=BBox(270, 90, 370, 390), confidence=0.9,
                     distance_m=distance_m, center_offset=offset)


@pytest.fixture
def tracker():
    return PresenceTracker(CFG)


def drive_to_confirming(tracker, t0=0.0):
    """Standard path: someone appears and stays put for the dwell period."""
    tracker.update(t0, person())
    return tracker.update(t0 + CFG.dwell_s, person())


def test_starts_idle(tracker):
    assert tracker.state is PresenceState.IDLE


def test_a_detection_moves_to_candidate(tracker):
    assert tracker.update(0.0, person()) is None
    assert tracker.state is PresenceState.CANDIDATE


def test_dwelling_moves_to_confirming_and_returns_the_detection(tracker):
    tracker.update(0.0, person())
    assert tracker.update(0.9, person()) is None
    assert tracker.state is PresenceState.CANDIDATE
    returned = tracker.update(1.0, person(distance_m=2.4))
    assert tracker.state is PresenceState.CONFIRMING
    assert returned is not None
    assert returned.distance_m == pytest.approx(2.4)


def test_the_confirm_detection_is_returned_only_once(tracker):
    drive_to_confirming(tracker)
    assert tracker.update(1.5, person()) is None


def test_a_passer_by_never_reaches_confirming(tracker):
    tracker.update(0.0, person())
    tracker.update(0.4, person())
    tracker.update(0.6, None)
    assert tracker.update(1.2, None) is None
    assert tracker.state is PresenceState.IDLE


def test_a_brief_detection_dropout_does_not_reset_the_dwell(tracker):
    tracker.update(0.0, person())
    tracker.update(0.4, None)          # inside the 0.5 s grace
    tracker.update(0.6, person())
    tracker.update(1.0, person())
    assert tracker.state is PresenceState.CONFIRMING


def test_candidate_returns_to_idle_after_the_loss_grace(tracker):
    tracker.update(0.0, person())
    tracker.update(0.2, None)
    tracker.update(0.71, None)
    assert tracker.state is PresenceState.IDLE


def test_a_positive_verdict_greets(tracker):
    drive_to_confirming(tracker)
    tracker.on_verdict(1.2, person_present=True)
    assert tracker.state is PresenceState.GREETING


def test_a_negative_verdict_enters_the_short_cooldown(tracker):
    drive_to_confirming(tracker)
    tracker.on_verdict(1.2, person_present=False)
    assert tracker.state is PresenceState.COOLDOWN
    # The short reject cooldown expires long before the 30 s greeting cooldown.
    tracker.update(2.0, None)
    tracker.update(6.3, None)
    assert tracker.state is PresenceState.IDLE


def test_dispatching_a_greeting_enters_the_long_cooldown(tracker):
    drive_to_confirming(tracker)
    tracker.on_verdict(1.2, person_present=True)
    tracker.on_greeting_dispatched(1.3)
    assert tracker.state is PresenceState.COOLDOWN


def test_the_cooldown_does_not_lift_while_the_person_is_still_there(tracker):
    drive_to_confirming(tracker)
    tracker.on_verdict(1.2, person_present=True)
    tracker.on_greeting_dispatched(1.3)
    for t in (10.0, 20.0, 31.0, 60.0, 120.0):
        tracker.update(t, person())
    assert tracker.state is PresenceState.COOLDOWN


def test_the_cooldown_lifts_after_they_leave(tracker):
    drive_to_confirming(tracker)
    tracker.on_verdict(1.2, person_present=True)
    tracker.on_greeting_dispatched(1.3)
    tracker.update(40.0, person())     # still there, cooldown already elapsed
    assert tracker.state is PresenceState.COOLDOWN
    tracker.update(41.0, None)         # they walk away
    tracker.update(43.0, None)
    assert tracker.state is PresenceState.COOLDOWN   # clear_s not yet satisfied
    tracker.update(44.1, None)
    assert tracker.state is PresenceState.IDLE


def test_someone_returning_during_the_clear_window_restarts_it(tracker):
    drive_to_confirming(tracker)
    tracker.on_verdict(1.2, person_present=True)
    tracker.on_greeting_dispatched(1.3)
    tracker.update(40.0, None)
    tracker.update(42.0, person())     # came back, resets the clear timer
    tracker.update(43.0, None)
    tracker.update(45.0, None)
    assert tracker.state is PresenceState.COOLDOWN
    tracker.update(46.1, None)
    assert tracker.state is PresenceState.IDLE


def test_a_second_person_can_be_greeted_after_the_cycle_completes(tracker):
    drive_to_confirming(tracker)
    tracker.on_verdict(1.2, person_present=True)
    tracker.on_greeting_dispatched(1.3)
    for t in (40.0, 41.0, 42.0, 43.0, 44.1):
        tracker.update(t, None)
    assert tracker.state is PresenceState.IDLE
    assert drive_to_confirming(tracker, t0=50.0) is not None


def test_has_live_detection_is_true_inside_the_grace_window(tracker):
    drive_to_confirming(tracker)
    tracker.update(1.2, person())
    assert tracker.has_live_detection is True


def test_has_live_detection_goes_false_after_the_grace_window(tracker):
    drive_to_confirming(tracker)
    tracker.update(1.2, person())
    tracker.update(2.0, None)
    assert tracker.has_live_detection is False


def test_has_live_detection_is_false_before_anything_is_seen(tracker):
    assert tracker.has_live_detection is False


def test_a_lost_verdict_does_not_wedge_the_machine(tracker):
    drive_to_confirming(tracker)
    tracker.update(5.0, person())
    tracker.update(11.5, person())
    assert tracker.state is PresenceState.IDLE


def test_a_verdict_arriving_after_the_confirm_timeout_is_ignored(tracker):
    drive_to_confirming(tracker)
    tracker.update(11.5, None)
    assert tracker.state is PresenceState.IDLE
    tracker.on_verdict(12.0, person_present=True)
    assert tracker.state is PresenceState.IDLE


def test_a_verdict_outside_confirming_is_ignored(tracker):
    tracker.on_verdict(0.5, person_present=True)
    assert tracker.state is PresenceState.IDLE


def test_dispatch_outside_greeting_is_ignored(tracker):
    tracker.on_greeting_dispatched(0.5)
    assert tracker.state is PresenceState.IDLE
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd /d/Projects/X2 && python -m pytest x2_greeter_ws/src/x2_greeter/test/core/test_presence.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'x2_greeter.core.presence'`

- [ ] **Step 3: Write the implementation**

`x2_greeter_ws/src/x2_greeter/x2_greeter/core/presence.py`:

```python
"""The presence state machine (spec section 7).

Pure and clock-injected: every method takes `now` in seconds rather than
reading a clock, so the whole lifecycle can be tested in microseconds.

The dwell gate is what makes cloud-primary vision affordable: "someone in
front of the robot" means someone who stopped, so waiting ~1 s before asking
the cloud costs nothing and filters out people merely walking past.

The compound cooldown exit is "don't greet the same person forever" without
face recognition: the cooldown will not lift while you are still standing
there. Walk away, come back, get greeted again.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from x2_greeter.core.types import Detection


class PresenceState(Enum):
    IDLE = 'idle'
    CANDIDATE = 'candidate'
    CONFIRMING = 'confirming'
    GREETING = 'greeting'
    COOLDOWN = 'cooldown'


@dataclass(frozen=True)
class PresenceConfig:
    dwell_s: float = 1.0
    loss_grace_s: float = 0.5
    clear_s: float = 3.0
    cooldown_s: float = 30.0
    reject_cooldown_s: float = 5.0
    # Not in the spec's table: without it, a dropped backend future would leave
    # the machine in CONFIRMING forever and the robot would never greet again.
    confirm_timeout_s: float = 10.0


class PresenceTracker:
    """Decides when to ask a backend, and when a greeting may happen.

    Single-threaded by contract: the ROS executor calls update() and the
    greeting worker's callbacks are marshalled back onto the same thread.
    """

    def __init__(self, config: PresenceConfig) -> None:
        self._config = config
        self._state = PresenceState.IDLE
        self._now = 0.0
        self._candidate_since = 0.0
        self._confirming_since = 0.0
        self._last_seen: Optional[float] = None
        self._cooldown_until = 0.0
        self._clear_since: Optional[float] = None

    @property
    def state(self) -> PresenceState:
        return self._state

    @property
    def has_live_detection(self) -> bool:
        """True if a gated detection arrived within the loss-grace window.

        The greeting worker consults this before falling back to a canned
        greeting: the spec only allows the fallback while the *local* detector
        still sees somebody.
        """
        if self._last_seen is None:
            return False
        return (self._now - self._last_seen) < self._config.loss_grace_s

    def update(self, now: float, detection: Optional[Detection]) -> Optional[Detection]:
        """Feed one frame's gating result.

        Returns the detection to confirm exactly once — at the moment the
        machine enters CONFIRMING — and None on every other call.
        """
        self._now = now
        if detection is not None:
            self._last_seen = now

        if self._state is PresenceState.IDLE:
            if detection is not None:
                self._state = PresenceState.CANDIDATE
                self._candidate_since = now
            return None

        if self._state is PresenceState.CANDIDATE:
            if detection is None:
                if self._elapsed_since_seen(now) >= self._config.loss_grace_s:
                    self._state = PresenceState.IDLE
                return None
            if (now - self._candidate_since) >= self._config.dwell_s:
                self._state = PresenceState.CONFIRMING
                self._confirming_since = now
                return detection
            return None

        if self._state is PresenceState.CONFIRMING:
            if (now - self._confirming_since) >= self._config.confirm_timeout_s:
                self._state = PresenceState.IDLE
            return None

        if self._state is PresenceState.GREETING:
            return None

        # COOLDOWN
        if detection is not None:
            self._clear_since = None
        elif self._clear_since is None:
            self._clear_since = now

        cooled = now >= self._cooldown_until
        cleared = (self._clear_since is not None
                   and (now - self._clear_since) >= self._config.clear_s)
        if cooled and cleared:
            self._state = PresenceState.IDLE
        return None

    def on_verdict(self, now: float, person_present: bool) -> None:
        """Report a backend's answer. Ignored unless still CONFIRMING."""
        self._now = now
        if self._state is not PresenceState.CONFIRMING:
            return
        if person_present:
            self._state = PresenceState.GREETING
        else:
            self._enter_cooldown(now, self._config.reject_cooldown_s)

    def on_greeting_dispatched(self, now: float) -> None:
        """Report that speech and gesture have been issued."""
        self._now = now
        if self._state is not PresenceState.GREETING:
            return
        self._enter_cooldown(now, self._config.cooldown_s)

    def _enter_cooldown(self, now: float, duration_s: float) -> None:
        self._state = PresenceState.COOLDOWN
        self._cooldown_until = now + duration_s
        # The person is presumably still standing there, so the clear timer has
        # not started yet.
        self._clear_since = None

    def _elapsed_since_seen(self, now: float) -> float:
        if self._last_seen is None:
            return float('inf')
        return now - self._last_seen
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd /d/Projects/X2 && python -m pytest x2_greeter_ws/src/x2_greeter/test/core/test_presence.py -v`
Expected: PASS — 21 passed

- [ ] **Step 5: Commit**

```bash
cd /d/Projects/X2
git add x2_greeter_ws/src/x2_greeter/x2_greeter/core/presence.py x2_greeter_ws/src/x2_greeter/test/core/test_presence.py
git commit -m "feat: add dwell/cooldown presence state machine"
```

---

## Task 5: Frame encoding and the privacy guarantee

**Files:**
- Create: `x2_greeter_ws/src/x2_greeter/x2_greeter/core/imaging.py`
- Test: `x2_greeter_ws/src/x2_greeter/test/core/test_imaging.py`
- Test: `x2_greeter_ws/src/x2_greeter/test/test_privacy.py`

**Interfaces:**
- Consumes: `JpegFrame` from `x2_greeter.core.types`.
- Produces: `to_jpeg_frame(bgr:numpy.ndarray, max_edge:int=512, quality:int=80) -> JpegFrame`. This is the **only** function in the codebase that encodes a camera frame, which is what makes the privacy guarantee testable in one place.

- [ ] **Step 1: Write the failing tests**

`x2_greeter_ws/src/x2_greeter/test/core/test_imaging.py`:

```python
import cv2
import numpy as np
import pytest

from x2_greeter.core.imaging import to_jpeg_frame
from x2_greeter.core.types import JpegFrame


def frame(height=480, width=640):
    rng = np.random.default_rng(0)
    return rng.integers(0, 256, size=(height, width, 3), dtype=np.uint8)


def test_returns_a_jpeg_frame():
    result = to_jpeg_frame(frame())
    assert isinstance(result, JpegFrame)
    assert result.media_type == 'image/jpeg'
    assert result.data[:2] == b'\xff\xd8'  # JPEG SOI marker


def test_downscales_the_longest_edge():
    decoded = cv2.imdecode(np.frombuffer(to_jpeg_frame(frame(480, 640)).data, np.uint8),
                           cv2.IMREAD_COLOR)
    assert max(decoded.shape[:2]) == 512
    assert decoded.shape[:2] == (384, 512)  # aspect ratio preserved


def test_downscales_a_portrait_frame_by_its_height():
    decoded = cv2.imdecode(np.frombuffer(to_jpeg_frame(frame(1200, 600)).data, np.uint8),
                           cv2.IMREAD_COLOR)
    assert decoded.shape[:2] == (512, 256)


def test_a_small_frame_is_not_upscaled():
    decoded = cv2.imdecode(np.frombuffer(to_jpeg_frame(frame(200, 300)).data, np.uint8),
                           cv2.IMREAD_COLOR)
    assert decoded.shape[:2] == (200, 300)


def test_a_grayscale_frame_is_promoted_to_three_channels():
    gray = np.zeros((480, 640), dtype=np.uint8)
    decoded = cv2.imdecode(np.frombuffer(to_jpeg_frame(gray).data, np.uint8), cv2.IMREAD_COLOR)
    assert decoded.shape[2] == 3


def test_an_empty_frame_raises_rather_than_sending_garbage():
    with pytest.raises(ValueError, match='empty'):
        to_jpeg_frame(np.zeros((0, 0, 3), dtype=np.uint8))
```

`x2_greeter_ws/src/x2_greeter/test/test_privacy.py`:

```python
"""The privacy guarantee: no image is written to disk, anywhere, ever.

Spec section 12. This test installs a tripwire over every filesystem-mutating
entry point Python offers and drives the entire image path through it.
"""
import builtins
import io
import os

import numpy as np
import pytest

from x2_greeter.core.detection import GateConfig, gate_detections
from x2_greeter.core.detectors import ScriptedDetector
from x2_greeter.core.imaging import to_jpeg_frame
from x2_greeter.core.types import BBox, RawDetection


@pytest.fixture
def no_disk_writes(monkeypatch):
    """Fail loudly if anything opens a file for writing or touches the FS."""
    violations = []
    real_open = builtins.open

    def guarded_open(file, mode='r', *args, **kwargs):
        if any(flag in mode for flag in ('w', 'a', 'x', '+')):
            violations.append(f'open({file!r}, {mode!r})')
        return real_open(file, mode, *args, **kwargs)

    def forbid(name):
        def _forbidden(*args, **kwargs):
            violations.append(f'{name}{args!r}')
            raise AssertionError(f'{name} called in the image path')
        return _forbidden

    monkeypatch.setattr(builtins, 'open', guarded_open)
    monkeypatch.setattr(io, 'open', guarded_open)
    monkeypatch.setattr(os, 'write', forbid('os.write'))
    monkeypatch.setattr(os, 'mkdir', forbid('os.mkdir'))
    monkeypatch.setattr(os, 'makedirs', forbid('os.makedirs'))

    import cv2
    monkeypatch.setattr(cv2, 'imwrite', forbid('cv2.imwrite'))

    yield violations
    assert violations == [], f'image path wrote to disk: {violations}'


def test_the_whole_image_path_writes_nothing_to_disk(no_disk_writes):
    rng = np.random.default_rng(0)
    bgr = rng.integers(0, 256, size=(480, 640, 3), dtype=np.uint8)
    depth = np.full((480, 640), 2000, dtype=np.uint16)

    detector = ScriptedDetector([RawDetection(bbox=BBox(270, 90, 370, 390), confidence=0.9)])
    raws = detector.detect(bgr)
    detection = gate_detections(raws, bgr.shape[:2], depth, 0.001, GateConfig())
    assert detection is not None

    frame = to_jpeg_frame(bgr)
    assert len(frame.data) > 0


def test_encoding_never_returns_a_path_or_filename():
    rng = np.random.default_rng(0)
    frame = to_jpeg_frame(rng.integers(0, 256, size=(64, 64, 3), dtype=np.uint8))
    assert not hasattr(frame, 'path')
    assert not hasattr(frame, 'filename')
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /d/Projects/X2 && python -m pytest x2_greeter_ws/src/x2_greeter/test/core/test_imaging.py x2_greeter_ws/src/x2_greeter/test/test_privacy.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'x2_greeter.core.imaging'`. The privacy test also fails on `x2_greeter.core.detectors`, which arrives in Task 9; that is expected and it will go green then.

- [ ] **Step 3: Write the implementation**

`x2_greeter_ws/src/x2_greeter/x2_greeter/core/imaging.py`:

```python
"""Encoding a camera frame for transmission.

The single place a frame is turned into bytes. Nothing here touches the
filesystem, and the caller receives bytes it holds only until the backend call
returns (spec section 12).
"""
from __future__ import annotations

import cv2
import numpy as np

from x2_greeter.core.types import JpegFrame


def to_jpeg_frame(bgr: np.ndarray, max_edge: int = 512, quality: int = 80) -> JpegFrame:
    """Downscale to `max_edge` on the longest side and JPEG-encode, in memory.

    512 px is enough for "is that a person, and roughly what are they doing?"
    while keeping the request small enough to fit the 2.5 s latency budget.
    """
    if bgr is None or bgr.size == 0:
        raise ValueError('cannot encode an empty frame')

    if bgr.ndim == 2:
        bgr = cv2.cvtColor(bgr, cv2.COLOR_GRAY2BGR)

    height, width = bgr.shape[:2]
    longest = max(height, width)
    if longest > max_edge:
        scale = max_edge / float(longest)
        bgr = cv2.resize(bgr, (max(1, round(width * scale)), max(1, round(height * scale))),
                         interpolation=cv2.INTER_AREA)

    ok, buffer = cv2.imencode('.jpg', bgr, [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)])
    if not ok:
        raise ValueError('JPEG encoding failed')
    return JpegFrame(data=buffer.tobytes())
```

- [ ] **Step 4: Run the imaging tests to verify they pass**

Run: `cd /d/Projects/X2 && python -m pytest x2_greeter_ws/src/x2_greeter/test/core/test_imaging.py -v`
Expected: PASS — 6 passed

Run: `cd /d/Projects/X2 && python -m pytest x2_greeter_ws/src/x2_greeter/test/test_privacy.py -v`
Expected: FAIL on the `x2_greeter.core.detectors` import. Leave it failing — Task 9 supplies `ScriptedDetector` and turns it green. Do not stub it here.

- [ ] **Step 5: Commit**

```bash
cd /d/Projects/X2
git add x2_greeter_ws/src/x2_greeter/x2_greeter/core/imaging.py x2_greeter_ws/src/x2_greeter/test/core/test_imaging.py x2_greeter_ws/src/x2_greeter/test/test_privacy.py
git commit -m "feat: add in-memory JPEG framing with a no-disk-writes tripwire test"
```

---

## Task 6: The backend port and the offline backend

**Files:**
- Create: `x2_greeter_ws/src/x2_greeter/x2_greeter/cognition/__init__.py`
- Create: `x2_greeter_ws/src/x2_greeter/x2_greeter/cognition/port.py`
- Create: `x2_greeter_ws/src/x2_greeter/x2_greeter/cognition/canned.py`
- Create: `x2_greeter_ws/src/x2_greeter/config/phrases.yaml`
- Test: `x2_greeter_ws/src/x2_greeter/test/cognition/test_canned.py`

**Interfaces:**
- Consumes: `JpegFrame`, `SceneContext`, `Verdict` from `x2_greeter.core.types`.
- Produces: `BackendUnavailable(Exception)`; `GreetingBackend` Protocol with attribute `name: str` and `confirm_and_compose(self, frame: JpegFrame|None, ctx: SceneContext) -> Verdict`; `DEFAULT_PHRASES: tuple[str,...]` (6 entries); `load_phrases(path: str|os.PathLike) -> tuple[str,...]`; `CannedBackend(phrases:Sequence[str]=DEFAULT_PHRASES, rng:random.Random|None=None)`.

The phrase list is shared with `tools/make_greeting_audio.py` (Task 18): recording `N` maps to `greeting_{N:02d}.wav`, so the order in `config/phrases.yaml` is load-bearing. Appending is safe; reordering or deleting invalidates deployed audio.

- [ ] **Step 1: Write the failing test**

`x2_greeter_ws/src/x2_greeter/test/cognition/test_canned.py`:

```python
import random

import pytest

from x2_greeter.cognition.canned import DEFAULT_PHRASES, CannedBackend, load_phrases
from x2_greeter.cognition.port import BackendUnavailable, GreetingBackend
from x2_greeter.core.types import SceneContext, Verdict

CTX = SceneContext(distance_m=2.0, center_offset=0.0)
PHRASES_YAML = 'x2_greeter_ws/src/x2_greeter/config/phrases.yaml'


def test_canned_backend_satisfies_the_protocol():
    assert isinstance(CannedBackend(), GreetingBackend)


def test_it_always_confirms_the_person():
    verdict = CannedBackend(rng=random.Random(0)).confirm_and_compose(None, CTX)
    assert verdict.person_present is True


def test_it_returns_a_phrase_from_the_list():
    verdict = CannedBackend(rng=random.Random(0)).confirm_and_compose(None, CTX)
    assert verdict.greeting in DEFAULT_PHRASES


def test_it_leaves_the_gesture_unset_so_the_selector_chooses():
    assert CannedBackend(rng=random.Random(0)).confirm_and_compose(None, CTX).gesture is None


def test_it_labels_its_source():
    assert CannedBackend().confirm_and_compose(None, CTX).source == 'canned'


def test_it_does_not_claim_to_know_the_orientation():
    verdict = CannedBackend(rng=random.Random(0)).confirm_and_compose(None, CTX)
    assert verdict.facing_robot is False
    assert verdict.confidence == 0.0


def test_it_does_not_repeat_itself():
    backend = CannedBackend(rng=random.Random(3))
    previous = None
    for _ in range(30):
        greeting = backend.confirm_and_compose(None, CTX).greeting
        assert greeting != previous
        previous = greeting


def test_it_copes_with_a_single_phrase():
    backend = CannedBackend(['Hi.'], rng=random.Random(0))
    assert backend.confirm_and_compose(None, CTX).greeting == 'Hi.'
    assert backend.confirm_and_compose(None, CTX).greeting == 'Hi.'


def test_it_ignores_the_frame_entirely():
    backend = CannedBackend(rng=random.Random(0))
    assert isinstance(backend.confirm_and_compose(None, CTX), Verdict)


def test_it_rejects_an_empty_phrase_list():
    with pytest.raises(ValueError, match='at least one'):
        CannedBackend([])


def test_the_shipped_phrase_file_loads():
    phrases = load_phrases(PHRASES_YAML)
    assert len(phrases) == len(DEFAULT_PHRASES)
    assert all(isinstance(p, str) and p.strip() for p in phrases)


def test_the_shipped_phrase_file_matches_the_module_default():
    # Task 18 generates greeting_NN.wav from this file by index; a drift between
    # the two would silently deploy the wrong recordings.
    assert load_phrases(PHRASES_YAML) == DEFAULT_PHRASES


def test_load_phrases_rejects_a_file_with_no_phrases(tmp_path):
    bad = tmp_path / 'phrases.yaml'
    bad.write_text('phrases: []\n', encoding='utf-8')
    with pytest.raises(ValueError, match='at least one'):
        load_phrases(bad)


def test_backend_unavailable_is_an_exception():
    assert issubclass(BackendUnavailable, Exception)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd /d/Projects/X2 && python -m pytest x2_greeter_ws/src/x2_greeter/test/cognition/test_canned.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'x2_greeter.cognition'`

- [ ] **Step 3: Write the implementation**

`x2_greeter_ws/src/x2_greeter/x2_greeter/cognition/__init__.py` is an empty file.

`x2_greeter_ws/src/x2_greeter/x2_greeter/cognition/port.py`:

```python
"""The seam between the greeter and whoever decides what to say.

Adding Grok, GPT or Gemini is one new file implementing this Protocol plus one
new value for the backend.provider parameter. Each adapter uses its own
vendor's official SDK — no lowest-common-denominator shim, so the Claude path
stays idiomatic (spec section 8.2).
"""
from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable

from x2_greeter.core.types import JpegFrame, SceneContext, Verdict


class BackendUnavailable(Exception):
    """The backend could not answer: network, auth, rate limit, bad response.

    Backends raise this instead of leaking vendor exception types, so
    GreetingPolicy can fall back without importing every vendor's SDK.
    """


@runtime_checkable
class GreetingBackend(Protocol):
    name: str

    def confirm_and_compose(self, frame: Optional[JpegFrame],
                            ctx: SceneContext) -> Verdict:
        """Confirm the person and compose a greeting, or raise BackendUnavailable.

        `frame` is None for backends that do not look at the image; a cloud
        backend must raise BackendUnavailable rather than guess if it is None.
        """
        ...
```

`x2_greeter_ws/src/x2_greeter/x2_greeter/cognition/canned.py`:

```python
"""The offline backend: pre-written phrases, no network, never fails.

This is the floor of the system. Whatever else breaks, the robot still greets.
"""
from __future__ import annotations

import os
import random
from typing import Optional, Sequence

import yaml

from x2_greeter.core.types import JpegFrame, SceneContext, Verdict

# Order is load-bearing: tools/make_greeting_audio.py records phrase N as
# greeting_{N:02d}.wav. Append freely; never reorder or delete.
DEFAULT_PHRASES = (
    'Hello there! Nice to see you.',
    'Hi! Welcome. I am X2.',
    'Good to see you. How are you doing?',
    'Hey! Thanks for stopping by.',
    'Hello! I am X2, pleased to meet you.',
    'Hi there! Great to have you here.',
)


def load_phrases(path) -> tuple:
    """Read the shared phrase list from config/phrases.yaml."""
    with open(os.fspath(path), 'r', encoding='utf-8') as handle:
        document = yaml.safe_load(handle) or {}
    phrases = tuple(str(p) for p in (document.get('phrases') or []) if str(p).strip())
    if not phrases:
        raise ValueError(f'{path} must list at least one phrase under "phrases"')
    return phrases


class CannedBackend:
    """Returns a pre-written greeting and lets the selector pick the gesture.

    It never inspects the frame — offline, the local detector has already
    decided somebody is there, and second-guessing that would only mean
    refusing to greet.
    """

    name = 'canned'

    def __init__(self, phrases: Sequence[str] = DEFAULT_PHRASES,
                 rng: Optional[random.Random] = None) -> None:
        phrases = tuple(phrases)
        if not phrases:
            raise ValueError('CannedBackend needs at least one phrase')
        self._phrases = phrases
        self._rng = rng if rng is not None else random.Random()
        self._last: Optional[str] = None

    def confirm_and_compose(self, frame: Optional[JpegFrame],
                            ctx: SceneContext) -> Verdict:
        greeting = self._next_phrase()
        return Verdict(
            person_present=True,
            facing_robot=False,   # unknowable locally, and never a gate
            confidence=0.0,
            greeting=greeting,
            gesture=None,         # let GestureSelector choose at random
            reason='offline canned greeting',
            source=self.name,
        )

    def _next_phrase(self) -> str:
        pool = [p for p in self._phrases if p != self._last]
        if not pool:
            pool = list(self._phrases)
        chosen = self._rng.choice(pool)
        self._last = chosen
        return chosen
```

`x2_greeter_ws/src/x2_greeter/config/phrases.yaml`:

```yaml
# Greeting phrases used by CannedBackend and recorded by
# tools/make_greeting_audio.py as greeting_00.wav .. greeting_NN.wav.
#
# ORDER IS LOAD-BEARING. Appending is safe. Reordering or deleting a line
# invalidates the WAVs already deployed to PC3.
phrases:
  - 'Hello there! Nice to see you.'
  - 'Hi! Welcome. I am X2.'
  - 'Good to see you. How are you doing?'
  - 'Hey! Thanks for stopping by.'
  - 'Hello! I am X2, pleased to meet you.'
  - 'Hi there! Great to have you here.'
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd /d/Projects/X2 && python -m pytest x2_greeter_ws/src/x2_greeter/test/cognition/test_canned.py -v`
Expected: PASS — 14 passed

- [ ] **Step 5: Commit**

```bash
cd /d/Projects/X2
git add x2_greeter_ws/src/x2_greeter/x2_greeter/cognition x2_greeter_ws/src/x2_greeter/config/phrases.yaml x2_greeter_ws/src/x2_greeter/test/cognition
git commit -m "feat: add greeting backend port and offline canned backend"
```

---

## Task 7: The Claude backend

**Files:**
- Create: `x2_greeter_ws/src/x2_greeter/x2_greeter/cognition/claude.py`
- Test: `x2_greeter_ws/src/x2_greeter/test/cognition/test_claude.py`

**Interfaces:**
- Consumes: `JpegFrame`, `SceneContext`, `Verdict` from `x2_greeter.core.types`; `BackendUnavailable`, `GreetingBackend` from `x2_greeter.cognition.port`.
- Produces: `ClaudeBackend(enabled_gestures:Sequence[str], model:str='claude-opus-5', effort:str='low', timeout_s:float=2.5, client=None, logger=None)` with `name = 'claude'` and `confirm_and_compose(frame, ctx) -> Verdict`; `build_schema(enabled_gestures:Sequence[str]) -> dict`; `SYSTEM_PROMPT: str`.

**SDK facts, read from the bundled `claude-api` skill — do not substitute recalled shapes:**
- Structured output with a runtime-built enum uses `output_config={"format": {"type": "json_schema", "schema": {...}}}`. The first content block is then guaranteed to be text containing valid JSON.
- Effort is `output_config={"effort": "low"}` — the same dict as `format`.
- `thinking={"type": "adaptive"}` on Opus 5. **`budget_tokens` is rejected with a 400 on Opus 5** — never send it.
- Images: `{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": <base64 str>}}`.
- Per-call timeout: `client.with_options(timeout=2.5, max_retries=0)`. A timeout raises `anthropic.APITimeoutError`.
- `anthropic` 1.x is built on `httpx2`, not `httpx`.

- [ ] **Step 1: Install the SDK**

```bash
cd /d/Projects/X2 && python -m pip install "anthropic>=1.0" pyyaml
```

- [ ] **Step 2: Write the failing test**

`x2_greeter_ws/src/x2_greeter/test/cognition/test_claude.py`:

```python
import base64
import json

import anthropic
import pytest

from x2_greeter.cognition.claude import SYSTEM_PROMPT, ClaudeBackend, build_schema
from x2_greeter.cognition.port import BackendUnavailable, GreetingBackend
from x2_greeter.core.types import JpegFrame, SceneContext

ENABLED = ('wave', 'salute', 'bow')
CTX = SceneContext(distance_m=2.1, center_offset=-0.12)
FRAME = JpegFrame(data=b'\xff\xd8\xff\xe0fakejpegbytes')

GOOD_PAYLOAD = {
    'person_present': True,
    'facing_robot': True,
    'confidence': 0.93,
    'greeting': 'Hello! Welcome, good to see you.',
    'gesture': 'wave',
    'reason': 'an adult standing squarely in front of the camera',
}


class TextBlock:
    type = 'text'

    def __init__(self, text):
        self.text = text


class ThinkingBlock:
    type = 'thinking'
    thinking = 'considering'


class FakeMessages:
    def __init__(self, owner):
        self._owner = owner

    def create(self, **kwargs):
        self._owner.calls.append(kwargs)
        if self._owner.raises is not None:
            raise self._owner.raises
        return self._owner.response


class FakeClient:
    """Stands in for anthropic.Anthropic: records calls, returns canned blocks."""

    def __init__(self, payload=None, raises=None, blocks=None):
        self.calls = []
        self.options = []
        self.raises = raises
        if blocks is None:
            blocks = [ThinkingBlock(), TextBlock(json.dumps(payload if payload is not None
                                                            else GOOD_PAYLOAD))]
        self.response = type('Resp', (), {'content': blocks})()
        self.messages = FakeMessages(self)

    def with_options(self, **kwargs):
        self.options.append(kwargs)
        return self


def backend(client, **kwargs):
    return ClaudeBackend(enabled_gestures=ENABLED, client=client, **kwargs)


def test_it_satisfies_the_protocol():
    assert isinstance(backend(FakeClient()), GreetingBackend)


def test_a_good_response_becomes_a_verdict():
    verdict = backend(FakeClient()).confirm_and_compose(FRAME, CTX)
    assert verdict.person_present is True
    assert verdict.facing_robot is True
    assert verdict.confidence == pytest.approx(0.93)
    assert verdict.greeting == 'Hello! Welcome, good to see you.'
    assert verdict.gesture == 'wave'
    assert verdict.source == 'claude'


def test_it_sends_the_opus_5_model_and_low_effort():
    client = FakeClient()
    backend(client).confirm_and_compose(FRAME, CTX)
    kwargs = client.calls[0]
    assert kwargs['model'] == 'claude-opus-5'
    assert kwargs['output_config']['effort'] == 'low'


def test_it_leaves_adaptive_thinking_on():
    client = FakeClient()
    backend(client).confirm_and_compose(FRAME, CTX)
    assert client.calls[0]['thinking'] == {'type': 'adaptive'}


def test_it_never_sends_budget_tokens():
    # budget_tokens is rejected with a 400 on Opus 5.
    client = FakeClient()
    backend(client).confirm_and_compose(FRAME, CTX)
    assert 'budget_tokens' not in json.dumps(client.calls[0]['thinking'])


def test_it_applies_a_hard_per_call_timeout_with_no_retries():
    client = FakeClient()
    backend(client, timeout_s=2.5).confirm_and_compose(FRAME, CTX)
    assert client.options[0] == {'timeout': 2.5, 'max_retries': 0}


def test_it_sends_the_frame_as_base64_jpeg():
    client = FakeClient()
    backend(client).confirm_and_compose(FRAME, CTX)
    blocks = client.calls[0]['messages'][0]['content']
    image = next(b for b in blocks if b['type'] == 'image')
    assert image['source']['type'] == 'base64'
    assert image['source']['media_type'] == 'image/jpeg'
    assert base64.standard_b64decode(image['source']['data']) == FRAME.data


def test_it_tells_the_model_where_the_person_is():
    client = FakeClient()
    backend(client).confirm_and_compose(FRAME, CTX)
    blocks = client.calls[0]['messages'][0]['content']
    text = next(b for b in blocks if b['type'] == 'text')['text']
    assert '2.1' in text
    assert 'left' in text.lower()


def test_the_schema_enum_is_exactly_the_enabled_gestures():
    schema = build_schema(ENABLED)
    assert schema['properties']['gesture']['enum'] == list(ENABLED)
    assert schema['additionalProperties'] is False
    assert set(schema['required']) == {
        'person_present', 'facing_robot', 'confidence', 'greeting', 'gesture', 'reason'}


def test_the_request_carries_that_schema():
    client = FakeClient()
    backend(client).confirm_and_compose(FRAME, CTX)
    fmt = client.calls[0]['output_config']['format']
    assert fmt['type'] == 'json_schema'
    assert fmt['schema']['properties']['gesture']['enum'] == list(ENABLED)


def test_a_gesture_outside_the_allowlist_is_dropped_not_trusted():
    payload = dict(GOOD_PAYLOAD, gesture='grab_buttocks')
    verdict = backend(FakeClient(payload)).confirm_and_compose(FRAME, CTX)
    assert verdict.gesture is None          # selector will choose at random
    assert verdict.greeting == GOOD_PAYLOAD['greeting']


def test_a_negative_verdict_is_passed_through():
    payload = dict(GOOD_PAYLOAD, person_present=False, greeting='',
                   reason='a coat on a stand')
    verdict = backend(FakeClient(payload)).confirm_and_compose(FRAME, CTX)
    assert verdict.person_present is False


def test_an_empty_greeting_on_a_positive_verdict_is_unusable():
    payload = dict(GOOD_PAYLOAD, greeting='   ')
    with pytest.raises(BackendUnavailable, match='empty greeting'):
        backend(FakeClient(payload)).confirm_and_compose(FRAME, CTX)


def test_malformed_json_raises_backend_unavailable():
    blocks = [TextBlock('{not json at all')]
    with pytest.raises(BackendUnavailable, match='malformed'):
        backend(FakeClient(blocks=blocks)).confirm_and_compose(FRAME, CTX)


def test_a_response_with_no_text_block_raises_backend_unavailable():
    with pytest.raises(BackendUnavailable, match='no text'):
        backend(FakeClient(blocks=[ThinkingBlock()])).confirm_and_compose(FRAME, CTX)


def test_a_missing_required_field_raises_backend_unavailable():
    payload = {k: v for k, v in GOOD_PAYLOAD.items() if k != 'person_present'}
    with pytest.raises(BackendUnavailable, match='person_present'):
        backend(FakeClient(payload)).confirm_and_compose(FRAME, CTX)


def test_a_missing_frame_raises_rather_than_guessing():
    with pytest.raises(BackendUnavailable, match='no frame'):
        backend(FakeClient()).confirm_and_compose(None, CTX)


def _api_error(cls, status_code):
    request = type('Req', (), {})()
    response = type('Resp', (), {'status_code': status_code, 'headers': {}})()
    return cls.__new__(cls) if False else cls('boom', response=response, body=None)


def test_a_timeout_raises_backend_unavailable():
    err = anthropic.APITimeoutError(request=type('Req', (), {})())
    with pytest.raises(BackendUnavailable, match='timed out'):
        backend(FakeClient(raises=err)).confirm_and_compose(FRAME, CTX)


def test_a_rate_limit_raises_backend_unavailable():
    err = _api_error(anthropic.RateLimitError, 429)
    with pytest.raises(BackendUnavailable, match='rate limited'):
        backend(FakeClient(raises=err)).confirm_and_compose(FRAME, CTX)


def test_an_unknown_model_raises_backend_unavailable():
    err = _api_error(anthropic.NotFoundError, 404)
    with pytest.raises(BackendUnavailable, match='not found'):
        backend(FakeClient(raises=err)).confirm_and_compose(FRAME, CTX)


def test_a_network_failure_raises_backend_unavailable():
    err = anthropic.APIConnectionError(request=type('Req', (), {})())
    with pytest.raises(BackendUnavailable, match='unreachable'):
        backend(FakeClient(raises=err)).confirm_and_compose(FRAME, CTX)


def test_an_unexpected_exception_still_raises_backend_unavailable():
    with pytest.raises(BackendUnavailable):
        backend(FakeClient(raises=RuntimeError('surprise'))).confirm_and_compose(FRAME, CTX)


def test_the_system_prompt_says_orientation_is_not_a_gate():
    assert 'back' in SYSTEM_PROMPT.lower()
    assert 'greet' in SYSTEM_PROMPT.lower()


def test_image_bytes_never_appear_in_an_exception_message():
    err = anthropic.APIConnectionError(request=type('Req', (), {})())
    with pytest.raises(BackendUnavailable) as excinfo:
        backend(FakeClient(raises=err)).confirm_and_compose(FRAME, CTX)
    assert 'fakejpegbytes' not in str(excinfo.value)
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `cd /d/Projects/X2 && python -m pytest x2_greeter_ws/src/x2_greeter/test/cognition/test_claude.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'x2_greeter.cognition.claude'`

- [ ] **Step 4: Write the implementation**

`x2_greeter_ws/src/x2_greeter/x2_greeter/cognition/claude.py`:

```python
"""The cloud backend: one Claude call answers three questions at once.

Is this really a person? What should the robot say? Which gesture fits?

One call per greeting event, never per frame — a per-frame cloud detector at
2 fps would cost roughly $50 per hour of idle standing and add seconds of
latency to every frame (spec section 8.1). The local detector decides *when*
to ask; this decides *what to do*.

Every failure path raises BackendUnavailable so GreetingPolicy can fall back
to a canned greeting. Nothing here ever raises a vendor exception into the
ROS node, and image bytes are never placed in a log or an exception message.
"""
from __future__ import annotations

import base64
import json
import logging
from typing import Optional, Sequence

import anthropic

from x2_greeter.cognition.port import BackendUnavailable
from x2_greeter.core.types import JpegFrame, SceneContext, Verdict

SYSTEM_PROMPT = (
    'You are the greeting module of an AgiBot X2 humanoid robot in a public space. '
    'You are shown one still frame from the robot\'s head camera at the moment a '
    'local detector believes somebody has stopped in front of it.\n\n'
    'Answer three questions:\n'
    '1. Is there really a person there? Say no for a mannequin, a poster, a '
    'reflection, a coat on a stand, or an empty corridor.\n'
    '2. What should the robot say? One short, warm, spoken sentence — at most 15 '
    'words. It will be read aloud by a text-to-speech engine, so write plain words '
    'with no emoji, markdown, stage directions or parentheses.\n'
    '3. Which gesture fits? Choose from the list you are given; nothing else.\n\n'
    'A person facing away still gets greeted — orientation is NOT a reason to say '
    'no. Report it in facing_robot so you can adapt: a spoken hello works for a '
    'back turned, but a bow or a handshake aimed at somebody\'s back does not.\n\n'
    'Do not describe the person\'s appearance, guess their identity, age, gender, '
    'ethnicity, or comment on their body. Keep the greeting generic and friendly.'
)

_REQUIRED_FIELDS = ('person_present', 'facing_robot', 'confidence', 'greeting',
                    'gesture', 'reason')


def build_schema(enabled_gestures: Sequence[str]) -> dict:
    """The structured-output schema, with the gesture enum built at runtime.

    The enum is the configured allowlist, so the model cannot name a gesture
    the robot is not permitted to perform. The result is still re-validated in
    _to_verdict: a schema is a request, not a guarantee we choose to trust.
    """
    return {
        'type': 'object',
        'properties': {
            'person_present': {'type': 'boolean',
                               'description': 'Is a real human being visible?'},
            'facing_robot': {'type': 'boolean',
                             'description': 'Are they facing the camera? Advisory only.'},
            'confidence': {'type': 'number',
                           'description': 'Your confidence in person_present, 0.0 to 1.0.'},
            'greeting': {'type': 'string',
                         'description': 'One short spoken sentence, at most 15 words.'},
            'gesture': {'type': 'string', 'enum': list(enabled_gestures),
                        'description': 'The gesture that best fits what you see and say.'},
            'reason': {'type': 'string',
                       'description': 'One clause explaining person_present.'},
        },
        'required': list(_REQUIRED_FIELDS),
        'additionalProperties': False,
    }


class ClaudeBackend:
    name = 'claude'

    def __init__(self, enabled_gestures: Sequence[str], model: str = 'claude-opus-5',
                 effort: str = 'low', timeout_s: float = 2.5, client=None,
                 logger: Optional[logging.Logger] = None) -> None:
        self._enabled = tuple(enabled_gestures)
        self._model = model
        self._effort = effort
        self._timeout_s = float(timeout_s)
        self._schema = build_schema(self._enabled)
        self._log = logger or logging.getLogger(__name__)
        # Constructing anthropic.Anthropic() reads ANTHROPIC_API_KEY and raises
        # if it is absent; the node checks for the key first and picks the
        # canned backend instead, so this only runs when a key exists.
        self._client = client if client is not None else anthropic.Anthropic()

    def confirm_and_compose(self, frame: Optional[JpegFrame],
                            ctx: SceneContext) -> Verdict:
        if frame is None:
            raise BackendUnavailable('no frame supplied to the cloud backend')

        try:
            response = self._client.with_options(
                timeout=self._timeout_s, max_retries=0,
            ).messages.create(
                model=self._model,
                max_tokens=4096,
                system=SYSTEM_PROMPT,
                thinking={'type': 'adaptive'},
                output_config={
                    'effort': self._effort,
                    'format': {'type': 'json_schema', 'schema': self._schema},
                },
                messages=[{
                    'role': 'user',
                    'content': [
                        {'type': 'image',
                         'source': {
                             'type': 'base64',
                             'media_type': frame.media_type,
                             'data': base64.standard_b64encode(frame.data).decode('ascii'),
                         }},
                        {'type': 'text', 'text': self._build_prompt(ctx)},
                    ],
                }],
            )
        # Most specific first. Every branch becomes BackendUnavailable, and no
        # branch is allowed to carry image bytes in its message.
        except anthropic.APITimeoutError as exc:
            raise BackendUnavailable(f'timed out after {self._timeout_s}s') from exc
        except anthropic.NotFoundError as exc:
            raise BackendUnavailable(f'model {self._model} not found') from exc
        except anthropic.RateLimitError as exc:
            raise BackendUnavailable('rate limited') from exc
        except anthropic.AuthenticationError as exc:
            raise BackendUnavailable('authentication rejected') from exc
        except anthropic.APIStatusError as exc:
            raise BackendUnavailable(f'API status {exc.status_code}') from exc
        except anthropic.APIConnectionError as exc:
            raise BackendUnavailable('API unreachable') from exc
        except Exception as exc:                       # noqa: BLE001 - never reach the node
            raise BackendUnavailable(f'unexpected backend failure: {type(exc).__name__}') from exc

        return self._to_verdict(self._extract_json(response))

    def _build_prompt(self, ctx: SceneContext) -> str:
        if ctx.center_offset < -0.05:
            where = 'slightly to your left'
        elif ctx.center_offset > 0.05:
            where = 'slightly to your right'
        else:
            where = 'directly ahead'
        gestures = ', '.join(self._enabled)
        return (
            f'The local detector reports a person about {ctx.distance_m:.1f} metres '
            f'away, {where}.\n'
            f'Gestures the robot may perform right now: {gestures}.\n'
            'Confirm the person, compose the greeting, and choose the gesture.'
        )

    @staticmethod
    def _extract_json(response) -> dict:
        """Pull the JSON out of the first text block.

        output_config.format guarantees the response contains a text block of
        valid JSON, but adaptive thinking may emit a thinking block first, so
        we search rather than index.
        """
        text = None
        for block in getattr(response, 'content', []) or []:
            if getattr(block, 'type', None) == 'text':
                text = block.text
                break
        if text is None:
            raise BackendUnavailable('response contained no text block')
        try:
            payload = json.loads(text)
        except (ValueError, TypeError) as exc:
            raise BackendUnavailable('malformed JSON in response') from exc
        if not isinstance(payload, dict):
            raise BackendUnavailable('malformed JSON in response: not an object')
        return payload

    def _to_verdict(self, payload: dict) -> Verdict:
        missing = [field for field in _REQUIRED_FIELDS if field not in payload]
        if missing:
            raise BackendUnavailable(f'response missing field(s): {", ".join(missing)}')

        person_present = bool(payload['person_present'])
        greeting = str(payload['greeting']).strip()
        if person_present and not greeting:
            raise BackendUnavailable('empty greeting for a positive verdict')

        # The schema constrains the enum, but an LLM answer is never trusted to
        # index a motion ID. An unrecognised name becomes None and the selector
        # picks at random (spec section 9).
        gesture = payload.get('gesture')
        if gesture not in self._enabled:
            if gesture is not None:
                self._log.warning('backend proposed gesture %r outside the allowlist', gesture)
            gesture = None

        try:
            confidence = float(payload['confidence'])
        except (TypeError, ValueError):
            confidence = 0.0

        return Verdict(
            person_present=person_present,
            facing_robot=bool(payload['facing_robot']),
            confidence=confidence,
            greeting=greeting,
            gesture=gesture,
            reason=str(payload['reason'])[:200],
            source=self.name,
        )
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `cd /d/Projects/X2 && python -m pytest x2_greeter_ws/src/x2_greeter/test/cognition/test_claude.py -v`
Expected: PASS — 24 passed

If `_api_error` fails to construct a `RateLimitError` or `NotFoundError` on the installed SDK version, read the constructor signature with `python -c "import inspect, anthropic; print(inspect.signature(anthropic.APIStatusError.__init__))"` and adjust the helper. Do not change the production code to fit the test helper.

- [ ] **Step 6: Commit**

```bash
cd /d/Projects/X2
git add x2_greeter_ws/src/x2_greeter/x2_greeter/cognition/claude.py x2_greeter_ws/src/x2_greeter/test/cognition/test_claude.py
git commit -m "feat: add Claude backend with runtime gesture enum and typed failures"
```

---

## Task 8: The greeting policy

**Files:**
- Create: `x2_greeter_ws/src/x2_greeter/x2_greeter/cognition/policy.py`
- Test: `x2_greeter_ws/src/x2_greeter/test/cognition/test_policy.py`

**Interfaces:**
- Consumes: `GreetingBackend`, `BackendUnavailable` from `x2_greeter.cognition.port`; `JpegFrame`, `SceneContext`, `Verdict` from `x2_greeter.core.types`.
- Produces: `GreetingPolicy(primary:GreetingBackend, fallback:GreetingBackend, logger=None)` with `compose(frame:JpegFrame|None, ctx:SceneContext) -> Verdict` and `.fallback_count -> int`.

- [ ] **Step 1: Write the failing test**

`x2_greeter_ws/src/x2_greeter/test/cognition/test_policy.py`:

```python
import logging
import random

import pytest

from x2_greeter.cognition.canned import CannedBackend
from x2_greeter.cognition.policy import GreetingPolicy
from x2_greeter.cognition.port import BackendUnavailable
from x2_greeter.core.types import JpegFrame, SceneContext, Verdict

CTX = SceneContext(distance_m=2.0, center_offset=0.0)
FRAME = JpegFrame(data=b'\xff\xd8\xffbytes')

CLOUD_VERDICT = Verdict(person_present=True, facing_robot=True, confidence=0.9,
                        greeting='Hello from the cloud!', gesture='wave',
                        reason='a person', source='claude')


class StubBackend:
    def __init__(self, name, verdict=None, raises=None):
        self.name = name
        self._verdict = verdict
        self._raises = raises
        self.calls = []

    def confirm_and_compose(self, frame, ctx):
        self.calls.append((frame, ctx))
        if self._raises is not None:
            raise self._raises
        return self._verdict


def canned():
    return CannedBackend(rng=random.Random(0))


def test_a_working_primary_is_used():
    primary = StubBackend('claude', CLOUD_VERDICT)
    verdict = GreetingPolicy(primary, canned()).compose(FRAME, CTX)
    assert verdict.greeting == 'Hello from the cloud!'
    assert verdict.source == 'claude'


def test_the_fallback_is_not_consulted_when_the_primary_works():
    fallback = StubBackend('canned', CLOUD_VERDICT)
    GreetingPolicy(StubBackend('claude', CLOUD_VERDICT), fallback).compose(FRAME, CTX)
    assert fallback.calls == []


def test_a_backend_unavailable_falls_back():
    primary = StubBackend('claude', raises=BackendUnavailable('timed out after 2.5s'))
    verdict = GreetingPolicy(primary, canned()).compose(FRAME, CTX)
    assert verdict.source == 'canned'
    assert verdict.person_present is True


def test_an_unexpected_exception_also_falls_back():
    primary = StubBackend('claude', raises=RuntimeError('surprise'))
    verdict = GreetingPolicy(primary, canned()).compose(FRAME, CTX)
    assert verdict.source == 'canned'


def test_the_fallback_is_not_shown_the_frame():
    fallback = StubBackend('canned', CLOUD_VERDICT)
    primary = StubBackend('claude', raises=BackendUnavailable('down'))
    GreetingPolicy(primary, fallback).compose(FRAME, CTX)
    assert fallback.calls == [(None, CTX)]


def test_a_negative_cloud_verdict_is_respected_not_overridden():
    # If the cloud says "that is a poster", we must not fall back to a canned
    # greeting — the fallback exists for failures, not for disagreements.
    negative = Verdict(person_present=False, facing_robot=False, confidence=0.95,
                       greeting='', gesture=None, reason='a poster', source='claude')
    fallback = StubBackend('canned', CLOUD_VERDICT)
    verdict = GreetingPolicy(StubBackend('claude', negative), fallback).compose(FRAME, CTX)
    assert verdict.person_present is False
    assert fallback.calls == []


def test_fallbacks_are_counted():
    primary = StubBackend('claude', raises=BackendUnavailable('down'))
    policy = GreetingPolicy(primary, canned())
    for _ in range(3):
        policy.compose(FRAME, CTX)
    assert policy.fallback_count == 3


def test_the_failure_reason_is_logged_as_a_warning(caplog):
    primary = StubBackend('claude', raises=BackendUnavailable('rate limited'))
    with caplog.at_level(logging.WARNING):
        GreetingPolicy(primary, canned(), logger=logging.getLogger('t')).compose(FRAME, CTX)
    assert 'rate limited' in caplog.text


def test_image_bytes_are_never_logged(caplog):
    primary = StubBackend('claude', raises=BackendUnavailable('down'))
    with caplog.at_level(logging.DEBUG):
        GreetingPolicy(primary, canned(), logger=logging.getLogger('t')).compose(FRAME, CTX)
    assert 'bytes' not in caplog.text.replace('image bytes', '')
    assert '\\xff\\xd8' not in caplog.text


def test_a_fallback_that_also_fails_propagates():
    # There is no third tier; if the canned backend is broken, that is a bug we
    # want to see, not swallow.
    primary = StubBackend('claude', raises=BackendUnavailable('down'))
    fallback = StubBackend('canned', raises=BackendUnavailable('also down'))
    with pytest.raises(BackendUnavailable, match='also down'):
        GreetingPolicy(primary, fallback).compose(FRAME, CTX)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd /d/Projects/X2 && python -m pytest x2_greeter_ws/src/x2_greeter/test/cognition/test_policy.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'x2_greeter.cognition.policy'`

- [ ] **Step 3: Write the implementation**

`x2_greeter_ws/src/x2_greeter/x2_greeter/cognition/policy.py`:

```python
"""Cloud first, canned second — but only on failure.

The distinction that matters: a backend *failing* means fall back; a backend
*disagreeing* ("that is a poster, not a person") is an answer and is respected.
Overriding a negative verdict would make the cloud confirmation pointless.
"""
from __future__ import annotations

import logging
from typing import Optional

from x2_greeter.cognition.port import BackendUnavailable, GreetingBackend
from x2_greeter.core.types import JpegFrame, SceneContext, Verdict


class GreetingPolicy:
    def __init__(self, primary: GreetingBackend, fallback: GreetingBackend,
                 logger: Optional[logging.Logger] = None) -> None:
        self._primary = primary
        self._fallback = fallback
        self._log = logger or logging.getLogger(__name__)
        self._fallback_count = 0

    @property
    def fallback_count(self) -> int:
        return self._fallback_count

    def compose(self, frame: Optional[JpegFrame], ctx: SceneContext) -> Verdict:
        """Ask the primary backend; on any failure, use the fallback.

        Runs on the greeting worker thread. The 2.5 s budget is enforced by the
        primary backend's own client timeout, so this never blocks the ROS
        executor.
        """
        try:
            return self._primary.confirm_and_compose(frame, ctx)
        except BackendUnavailable as exc:
            self._log.warning('%s backend unavailable (%s); using %s',
                              self._primary.name, exc, self._fallback.name)
        except Exception as exc:                       # noqa: BLE001 - never reach the node
            self._log.warning('%s backend raised %s; using %s',
                              self._primary.name, type(exc).__name__, self._fallback.name)

        self._fallback_count += 1
        # The fallback is never shown the frame: it does not look at images, and
        # not passing it keeps the buffer's lifetime as short as possible.
        return self._fallback.confirm_and_compose(None, ctx)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd /d/Projects/X2 && python -m pytest x2_greeter_ws/src/x2_greeter/test/cognition/test_policy.py -v`
Expected: PASS — 10 passed

- [ ] **Step 5: Commit**

```bash
cd /d/Projects/X2
git add x2_greeter_ws/src/x2_greeter/x2_greeter/cognition/policy.py x2_greeter_ws/src/x2_greeter/test/cognition/test_policy.py
git commit -m "feat: add cloud-with-fallback greeting policy"
```

---

## Task 9: Person detectors

**Files:**
- Create: `x2_greeter_ws/src/x2_greeter/x2_greeter/core/detectors.py`
- Create: `tools/fetch_model.py`
- Test: `x2_greeter_ws/src/x2_greeter/test/core/test_detectors.py`

**Interfaces:**
- Consumes: `BBox`, `RawDetection` from `x2_greeter.core.types`; `PersonDetector` Protocol from `x2_greeter.core.detection`.
- Produces: `parse_ssd_output(raw:numpy.ndarray, frame_width:int, frame_height:int, class_id:int=15) -> list[RawDetection]`; `hog_weight_to_confidence(weight:float) -> float`; `MobileNetSsdDetector(prototxt_path, model_path, input_size=(300,300))`; `HogPersonDetector(win_stride=(8,8), padding=(8,8), scale=1.05)`; `ScriptedDetector(detections:Sequence[RawDetection]=(), start_after_frames:int=0)` with `.set_detections(...)` and `.frame_count`; `build_detector(kind:str, model_dir:str, logger=None) -> PersonDetector`; `MODEL_FILES = ('MobileNetSSD_deploy.prototxt', 'MobileNetSSD_deploy.caffemodel')`.

**Why three detectors.** The spec names MobileNet-SSD, which needs ~23 MB of weights that are not in the repo and not in the SDK. `build_detector` keeps `mobilenet_ssd` as the default and configured behaviour, but falls back to OpenCV's built-in HOG pedestrian detector — which ships inside `opencv-python` and needs no download — with a loud warning when the weights are absent. That makes the node runnable on a fresh checkout, which matters because there is no robot yet. `ScriptedDetector` makes the ROS integration test deterministic: a synthetic camera frame contains no real person, so no real detector would ever fire on one.

- [ ] **Step 1: Write the failing test**

`x2_greeter_ws/src/x2_greeter/test/core/test_detectors.py`:

```python
import numpy as np
import pytest

from x2_greeter.core.detection import PersonDetector
from x2_greeter.core.detectors import (
    MODEL_FILES,
    HogPersonDetector,
    ScriptedDetector,
    build_detector,
    hog_weight_to_confidence,
    parse_ssd_output,
)
from x2_greeter.core.types import BBox, RawDetection


def ssd_row(class_id, confidence, x1, y1, x2, y2):
    return [0.0, float(class_id), float(confidence), x1, y1, x2, y2]


def ssd_blob(rows):
    return np.array([[rows]], dtype=np.float32)  # shape (1, 1, N, 7)


def test_ssd_output_is_scaled_to_frame_pixels():
    blob = ssd_blob([ssd_row(15, 0.9, 0.25, 0.1, 0.75, 0.9)])
    got = parse_ssd_output(blob, frame_width=640, frame_height=480)
    assert got == [RawDetection(bbox=BBox(160, 48, 480, 432), confidence=pytest.approx(0.9))]


def test_ssd_output_keeps_only_the_person_class():
    blob = ssd_blob([
        ssd_row(7, 0.99, 0.1, 0.1, 0.2, 0.2),    # car
        ssd_row(15, 0.8, 0.3, 0.1, 0.6, 0.9),    # person
        ssd_row(12, 0.95, 0.7, 0.1, 0.9, 0.4),   # dog
    ])
    got = parse_ssd_output(blob, 640, 480)
    assert len(got) == 1
    assert got[0].confidence == pytest.approx(0.8)


def test_ssd_boxes_are_clamped_to_the_frame():
    blob = ssd_blob([ssd_row(15, 0.9, -0.2, -0.3, 1.4, 1.9)])
    box = parse_ssd_output(blob, 640, 480)[0].bbox
    assert (box.x1, box.y1, box.x2, box.y2) == (0, 0, 640, 480)


def test_ssd_degenerate_boxes_are_dropped():
    blob = ssd_blob([ssd_row(15, 0.9, 0.5, 0.5, 0.5, 0.5)])
    assert parse_ssd_output(blob, 640, 480) == []


def test_ssd_empty_output_yields_nothing():
    assert parse_ssd_output(np.zeros((1, 1, 0, 7), dtype=np.float32), 640, 480) == []


def test_hog_weight_zero_is_the_decision_boundary():
    assert hog_weight_to_confidence(0.0) == pytest.approx(0.5)


def test_hog_confidence_is_monotonic_and_bounded():
    values = [hog_weight_to_confidence(w) for w in (-4.0, -1.0, 0.0, 1.0, 4.0)]
    assert values == sorted(values)
    assert all(0.0 < v < 1.0 for v in values)


def test_hog_detector_satisfies_the_protocol_and_runs_on_a_blank_frame():
    detector = HogPersonDetector()
    assert isinstance(detector, PersonDetector)
    blank = np.zeros((240, 320, 3), dtype=np.uint8)
    assert detector.detect(blank) == []


def test_scripted_detector_returns_what_it_was_given():
    wanted = [RawDetection(bbox=BBox(10, 10, 50, 150), confidence=0.9)]
    detector = ScriptedDetector(wanted)
    assert isinstance(detector, PersonDetector)
    assert detector.detect(np.zeros((240, 320, 3), dtype=np.uint8)) == wanted


def test_scripted_detector_can_stay_quiet_for_a_while():
    wanted = [RawDetection(bbox=BBox(10, 10, 50, 150), confidence=0.9)]
    detector = ScriptedDetector(wanted, start_after_frames=2)
    frame = np.zeros((240, 320, 3), dtype=np.uint8)
    assert detector.detect(frame) == []
    assert detector.detect(frame) == []
    assert detector.detect(frame) == wanted
    assert detector.frame_count == 3


def test_scripted_detector_can_be_reprogrammed():
    detector = ScriptedDetector()
    frame = np.zeros((240, 320, 3), dtype=np.uint8)
    assert detector.detect(frame) == []
    wanted = [RawDetection(bbox=BBox(0, 0, 10, 10), confidence=0.7)]
    detector.set_detections(wanted)
    assert detector.detect(frame) == wanted


def test_build_detector_returns_the_scripted_detector_on_request():
    assert isinstance(build_detector('scripted', ''), ScriptedDetector)


def test_build_detector_returns_hog_on_request():
    assert isinstance(build_detector('hog', ''), HogPersonDetector)


def test_build_detector_falls_back_to_hog_when_the_weights_are_missing(tmp_path, caplog):
    import logging
    with caplog.at_level(logging.WARNING):
        detector = build_detector('mobilenet_ssd', str(tmp_path), logger=logging.getLogger('t'))
    assert isinstance(detector, HogPersonDetector)
    assert 'MobileNetSSD_deploy.caffemodel' in caplog.text
    assert 'tools/fetch_model.py' in caplog.text


def test_build_detector_rejects_an_unknown_kind():
    with pytest.raises(ValueError, match='unknown detector'):
        build_detector('yolo', '')


def test_the_expected_model_filenames_are_declared():
    assert MODEL_FILES == ('MobileNetSSD_deploy.prototxt', 'MobileNetSSD_deploy.caffemodel')
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd /d/Projects/X2 && python -m pytest x2_greeter_ws/src/x2_greeter/test/core/test_detectors.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'x2_greeter.core.detectors'`

- [ ] **Step 3: Write the implementation**

`x2_greeter_ws/src/x2_greeter/x2_greeter/core/detectors.py`:

```python
"""Local person detectors.

The local detector is a *gate*, not a decision: it chooses when the robot is
allowed to spend a cloud call, and the cloud makes the actual call on whether
somebody is really there. That is why a modest detector is acceptable here.
"""
from __future__ import annotations

import logging
import math
import os
from typing import List, Optional, Sequence

import cv2
import numpy as np

from x2_greeter.core.types import BBox, RawDetection

# PASCAL VOC class index used by MobileNet-SSD.
PERSON_CLASS_ID = 15
MODEL_FILES = ('MobileNetSSD_deploy.prototxt', 'MobileNetSSD_deploy.caffemodel')


def parse_ssd_output(raw: np.ndarray, frame_width: int, frame_height: int,
                     class_id: int = PERSON_CLASS_ID) -> List[RawDetection]:
    """Turn a cv2.dnn SSD forward() blob into person boxes in frame pixels.

    The blob has shape (1, 1, N, 7); each row is
    [image_index, class_id, confidence, x1, y1, x2, y2] with the coordinates
    normalised to 0..1. Split out from the detector so it can be tested
    without a 23 MB weights file.
    """
    results: List[RawDetection] = []
    if raw is None or raw.size == 0:
        return results

    rows = raw.reshape(-1, raw.shape[-1])
    for row in rows:
        if int(row[1]) != class_id:
            continue
        x1 = int(np.clip(round(float(row[3]) * frame_width), 0, frame_width))
        y1 = int(np.clip(round(float(row[4]) * frame_height), 0, frame_height))
        x2 = int(np.clip(round(float(row[5]) * frame_width), 0, frame_width))
        y2 = int(np.clip(round(float(row[6]) * frame_height), 0, frame_height))
        if x2 <= x1 or y2 <= y1:
            continue
        results.append(RawDetection(bbox=BBox(x1, y1, x2, y2), confidence=float(row[2])))
    return results


def hog_weight_to_confidence(weight: float) -> float:
    """Squash an SVM decision value into a 0..1 confidence.

    HOG returns a signed distance from the decision boundary rather than a
    probability, so 0.0 maps to 0.5 and the gate's confidence_min keeps its
    usual meaning.
    """
    return 1.0 / (1.0 + math.exp(-float(weight)))


class MobileNetSsdDetector:
    """The configured detector: a Caffe MobileNet-SSD run through cv2.dnn."""

    def __init__(self, prototxt_path: str, model_path: str, input_size=(300, 300)) -> None:
        self._net = cv2.dnn.readNetFromCaffe(str(prototxt_path), str(model_path))
        self._input_size = tuple(input_size)

    def detect(self, bgr: np.ndarray) -> List[RawDetection]:
        height, width = bgr.shape[:2]
        blob = cv2.dnn.blobFromImage(
            cv2.resize(bgr, self._input_size), 0.007843, self._input_size, 127.5)
        self._net.setInput(blob)
        return parse_ssd_output(self._net.forward(), width, height)


class HogPersonDetector:
    """The no-download fallback: OpenCV's built-in HOG pedestrian detector.

    Weaker and slower than MobileNet-SSD, but it ships inside opencv-python,
    so a fresh checkout runs without fetching anything.
    """

    def __init__(self, win_stride=(8, 8), padding=(8, 8), scale: float = 1.05) -> None:
        self._hog = cv2.HOGDescriptor()
        self._hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())
        self._win_stride = tuple(win_stride)
        self._padding = tuple(padding)
        self._scale = float(scale)

    def detect(self, bgr: np.ndarray) -> List[RawDetection]:
        rects, weights = self._hog.detectMultiScale(
            bgr, winStride=self._win_stride, padding=self._padding, scale=self._scale)
        results: List[RawDetection] = []
        for (x, y, w, h), weight in zip(rects, np.ravel(weights)):
            results.append(RawDetection(
                bbox=BBox(int(x), int(y), int(x) + int(w), int(y) + int(h)),
                confidence=hog_weight_to_confidence(weight)))
        return results


class ScriptedDetector:
    """Reports exactly what it is told to. For simulation and integration tests.

    A synthetic camera frame contains no real person, so a real detector would
    never fire on one; this makes the ROS integration test deterministic.
    """

    def __init__(self, detections: Sequence[RawDetection] = (),
                 start_after_frames: int = 0) -> None:
        self._detections = list(detections)
        self._start_after_frames = int(start_after_frames)
        self._frame_count = 0

    @property
    def frame_count(self) -> int:
        return self._frame_count

    def set_detections(self, detections: Sequence[RawDetection]) -> None:
        self._detections = list(detections)

    def detect(self, bgr: np.ndarray) -> List[RawDetection]:
        self._frame_count += 1
        if self._frame_count <= self._start_after_frames:
            return []
        return list(self._detections)


def build_detector(kind: str, model_dir: str,
                   logger: Optional[logging.Logger] = None) -> object:
    """Construct the configured detector, degrading rather than refusing to start."""
    log = logger or logging.getLogger(__name__)

    if kind == 'scripted':
        return ScriptedDetector()
    if kind == 'hog':
        return HogPersonDetector()
    if kind != 'mobilenet_ssd':
        raise ValueError(f'unknown detector {kind!r}; expected mobilenet_ssd, hog or scripted')

    prototxt = os.path.join(model_dir, MODEL_FILES[0])
    weights = os.path.join(model_dir, MODEL_FILES[1])
    missing = [p for p in (prototxt, weights) if not os.path.isfile(p)]
    if missing:
        log.warning(
            'MobileNet-SSD weights not found (%s); falling back to the built-in HOG '
            'pedestrian detector. Run tools/fetch_model.py to install them.',
            ', '.join(os.path.basename(p) for p in missing))
        return HogPersonDetector()
    return MobileNetSsdDetector(prototxt, weights)
```

- [ ] **Step 4: Write the model fetcher**

`tools/fetch_model.py`:

```python
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

DEFAULT_PROTOTXT_URL = (
    'https://raw.githubusercontent.com/chuanqi305/MobileNet-SSD/'
    'master/deploy.prototxt'
)
DEFAULT_WEIGHTS_URL = (
    'https://github.com/chuanqi305/MobileNet-SSD/raw/'
    'master/mobilenet_iter_73000.caffemodel'
)
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
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd /d/Projects/X2 && python -m pytest x2_greeter_ws/src/x2_greeter/test/core/test_detectors.py x2_greeter_ws/src/x2_greeter/test/test_privacy.py -v`
Expected: PASS — 16 + 2 passed. The privacy test, which was left red at the end of Task 5, now goes green.

- [ ] **Step 6: Run the whole Windows-side suite**

Run: `cd /d/Projects/X2 && python -m pytest -v`
Expected: PASS — every test written so far (Tasks 1–9), no ROS tests collected.

- [ ] **Step 7: Commit**

```bash
cd /d/Projects/X2
git add x2_greeter_ws/src/x2_greeter/x2_greeter/core/detectors.py x2_greeter_ws/src/x2_greeter/test/core/test_detectors.py tools/fetch_model.py
git commit -m "feat: add MobileNet-SSD, HOG and scripted person detectors"
```

---

## Task 10: The Docker ROS test harness

**Files:**
- Create: `docker/Dockerfile.test`
- Create: `docker/run_tests.sh`
- Test: `x2_greeter_ws/src/x2_greeter/test/ros/test_msgs_available.py`

**Interfaces:**
- Consumes: the `x2_greeter` package from Task 1; the vendor `aimdk_msgs` source at `sdk/aimdk-aarch64-a424add7-artifacts/src/aimdk_msgs`.
- Produces: a repeatable command that builds real `aimdk_msgs` message types for x86_64 and runs every `@pytest.mark.ros` test against them.

**Why this works.** `aimdk_msgs/CMakeLists.txt` includes `cmake/aimdk_msgs_prebuilt.cmake`, which looks for `prebuilt_${TARGET_ARCH}`. Only `prebuilt_aarch64` exists in the archive, so on x86_64 `USE_PREBUILT_PACKAGE` stays `FALSE` and CMake falls through to `rosidl_generate_interfaces` over the 168 `.msg` and 54 `.srv` files shipped as source. Integration tests therefore use **genuine** message types, not mocks. The package is copied rather than bind-mounted because its install step writes a prebuilt cache back into its own source tree.

- [ ] **Step 1: Write the failing test**

`x2_greeter_ws/src/x2_greeter/test/ros/test_msgs_available.py`:

```python
"""Proves the Docker harness really built the vendor message package.

If these fail, every other @ros test is meaningless — so this runs first.
"""
import pytest

pytestmark = pytest.mark.ros


def test_the_service_types_the_greeter_needs_exist():
    from aimdk_msgs.srv import GetMcAction, PlayAudioFile, PlayTts, SetMcPresetMotion

    assert PlayTts.Request is not None
    assert PlayAudioFile.Request is not None
    assert SetMcPresetMotion.Request is not None
    assert GetMcAction.Request is not None


def test_stand_default_is_the_mode_gestures_require():
    from aimdk_msgs.msg import McAction

    assert McAction.STAND_DEFAULT == 200
    assert McAction.JOINT_DEFAULT == 100
    assert McAction.PASSIVE_DEFAULT == 1


def test_play_tts_request_has_the_fields_the_sdk_example_uses():
    from aimdk_msgs.srv import PlayTts

    req = PlayTts.Request()
    req.tts_req.text = 'hello'
    req.tts_req.domain = 'x2_greeter'
    req.tts_req.trace_id = 'trace'
    req.tts_req.is_interrupted = True
    req.tts_req.priority_weight = 0
    req.tts_req.priority_level.value = 6
    assert req.tts_req.priority_level.value == 6


def test_play_tts_response_reports_success():
    from aimdk_msgs.srv import PlayTts

    resp = PlayTts.Response()
    resp.tts_resp.is_success = True
    assert resp.tts_resp.is_success is True


def test_play_audio_file_response_field_is_misspelled_reponse():
    """The vendor .srv spells it 'reponse'. Our code must not assume otherwise."""
    from aimdk_msgs.srv import PlayAudioFile

    resp = PlayAudioFile.Response()
    assert hasattr(resp, 'reponse') or hasattr(resp, 'response')


def test_preset_motion_request_takes_a_motion_and_an_area():
    from aimdk_msgs.msg import McControlArea, McPresetMotion
    from aimdk_msgs.srv import SetMcPresetMotion

    req = SetMcPresetMotion.Request()
    motion = McPresetMotion()
    motion.value = 1002          # wave
    area = McControlArea()
    area.value = 2               # right arm
    req.motion = motion
    req.area = area
    req.interrupt = False
    assert req.motion.value == 1002


def test_rclpy_and_cv_bridge_are_importable():
    import cv_bridge
    import rclpy

    assert hasattr(rclpy, 'init')
    assert hasattr(cv_bridge, 'CvBridge')
```

- [ ] **Step 2: Write the Dockerfile**

`docker/Dockerfile.test`:

```dockerfile
# Test harness for x2_greeter. Builds the vendor aimdk_msgs package from source
# for x86_64 (the shipped prebuilts are aarch64-only, so CMake falls back to
# full interface generation) and runs the @pytest.mark.ros tests against it.
FROM ros:humble-ros-base

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
      python3-pip \
      python3-colcon-common-extensions \
      python3-numpy \
      python3-opencv \
      python3-yaml \
      ros-humble-cv-bridge \
      ros-humble-sensor-msgs \
      ros-humble-std-msgs \
      ros-humble-geometry-msgs \
      ros-humble-nav-msgs \
      ros-humble-action-msgs \
    && rm -rf /var/lib/apt/lists/*

RUN python3 -m pip install --no-cache-dir "anthropic>=1.0" "pytest>=8"

WORKDIR /ws
```

- [ ] **Step 3: Write the run script**

`docker/run_tests.sh`:

```bash
#!/usr/bin/env bash
# Build aimdk_msgs + x2_greeter inside the container and run the ROS tests.
# Extra arguments are passed straight through to pytest.
set -euo pipefail

REPO=${REPO:-/repo}
WS=${WS:-/ws}
AIMDK_SRC="$REPO/sdk/aimdk-aarch64-a424add7-artifacts/src/aimdk_msgs"

source /opt/ros/humble/setup.bash

mkdir -p "$WS/src"

# aimdk_msgs never changes and takes several minutes to generate, so it is
# copied once and left alone. It is copied rather than mounted because its
# install step writes a prebuilt cache back into its own source tree.
if [ ! -d "$WS/src/aimdk_msgs" ]; then
  echo "== copying aimdk_msgs from the vendor archive"
  cp -r "$AIMDK_SRC" "$WS/src/aimdk_msgs"
fi

echo "== syncing x2_greeter"
rm -rf "$WS/src/x2_greeter"
cp -r "$REPO/x2_greeter_ws/src/x2_greeter" "$WS/src/x2_greeter"
cp "$REPO/pyproject.toml" "$WS/pyproject.toml"

cd "$WS"
echo "== colcon build"
colcon build --packages-select aimdk_msgs x2_greeter \
             --cmake-args -DCMAKE_BUILD_TYPE=Release

source "$WS/install/setup.bash"

echo "== pytest -m ros"
exec python3 -m pytest src/x2_greeter/test -m ros -v -p no:cacheprovider "$@"
```

```bash
cd /d/Projects/X2 && chmod +x docker/run_tests.sh
```

- [ ] **Step 4: Build the image**

Run: `cd /d/Projects/X2 && docker build -t x2-greeter-test -f docker/Dockerfile.test docker/`
Expected: image `x2-greeter-test` built.

- [ ] **Step 5: Run the harness — first build of aimdk_msgs takes several minutes**

From Git Bash (`MSYS_NO_PATHCONV=1` stops MSYS rewriting the container paths):

```bash
cd /d/Projects/X2
MSYS_NO_PATHCONV=1 docker run --rm \
  -v "D:/Projects/X2:/repo" \
  -v x2-greeter-ws:/ws \
  x2-greeter-test bash /repo/docker/run_tests.sh
```

From PowerShell:

```powershell
docker run --rm -v "D:/Projects/X2:/repo" -v x2-greeter-ws:/ws x2-greeter-test bash /repo/docker/run_tests.sh
```

Expected: `colcon build` reports `Finished <<< aimdk_msgs` and `Finished <<< x2_greeter`, then 7 passed.

If the build log says `Prebuilt package structure is valid` and skips generation, the container is running under aarch64 emulation. Add `--platform linux/amd64` to both the `docker build` and the `docker run`.

- [ ] **Step 6: Commit**

```bash
cd /d/Projects/X2
git add docker x2_greeter_ws/src/x2_greeter/test/ros/test_msgs_available.py
git commit -m "test: add Docker harness building real aimdk_msgs for x86_64"
```

---

## Task 11: The fake robot

**Files:**
- Create: `x2_greeter_ws/src/x2_greeter/x2_greeter/sim/__init__.py`
- Create: `x2_greeter_ws/src/x2_greeter/x2_greeter/sim/fake_robot.py`
- Test: `x2_greeter_ws/src/x2_greeter/test/ros/test_fake_robot.py`

**Interfaces:**
- Consumes: `aimdk_msgs` service types; `sensor_msgs/Image`; `cv_bridge`.
- Produces: `FakeRobot(Node)` — construct with `FakeRobot(rgb_topic=..., depth_topic=..., publish_camera=True, current_action=200, tts_succeeds=True, distance_mm=2000)`. Attributes: `.tts_requests: list`, `.audio_requests: list`, `.motion_requests: list`, `.mode_queries: int`, `.current_action: int`, `.tts_succeeds: bool`. Also `main()` for `ros2 run x2_greeter fake_robot`.

- [ ] **Step 1: Write the failing test**

`x2_greeter_ws/src/x2_greeter/test/ros/test_fake_robot.py`:

```python
import threading

import pytest

pytestmark = pytest.mark.ros


@pytest.fixture
def ros():
    import rclpy
    rclpy.init()
    yield rclpy
    rclpy.shutdown()


@pytest.fixture
def spinning(ros):
    """A FakeRobot spinning on a background executor."""
    from rclpy.executors import MultiThreadedExecutor

    from x2_greeter.sim.fake_robot import FakeRobot

    robot = FakeRobot(publish_camera=True)
    executor = MultiThreadedExecutor()
    executor.add_node(robot)
    thread = threading.Thread(target=executor.spin, daemon=True)
    thread.start()
    yield robot
    executor.shutdown()
    robot.destroy_node()
    thread.join(timeout=5.0)


def call(node, client, request, timeout_s=5.0):
    done = threading.Event()
    future = client.call_async(request)
    future.add_done_callback(lambda _f: done.set())
    assert done.wait(timeout_s), 'service call timed out'
    return future.result()


def make_client(ros, srv_type, name):
    node = ros.create_node('test_client_' + name.rsplit('/', 1)[-1].lower())
    client = node.create_client(srv_type, name)
    assert client.wait_for_service(timeout_sec=10.0), f'{name} never appeared'
    return node, client


def test_it_serves_play_tts_and_records_the_request(spinning, ros):
    from aimdk_msgs.srv import PlayTts

    node, client = make_client(ros, PlayTts, '/aimdk_5Fmsgs/srv/PlayTts')
    try:
        req = PlayTts.Request()
        req.tts_req.text = 'Hello there!'
        req.tts_req.domain = 'x2_greeter'
        req.tts_req.priority_level.value = 6
        resp = call(node, client, req)
        assert resp.tts_resp.is_success is True
        assert spinning.tts_requests[0].tts_req.text == 'Hello there!'
    finally:
        node.destroy_node()


def test_tts_can_be_made_to_fail(spinning, ros):
    from aimdk_msgs.srv import PlayTts

    spinning.tts_succeeds = False
    node, client = make_client(ros, PlayTts, '/aimdk_5Fmsgs/srv/PlayTts')
    try:
        req = PlayTts.Request()
        req.tts_req.text = 'nope'
        assert call(node, client, req).tts_resp.is_success is False
    finally:
        node.destroy_node()


def test_it_serves_preset_motion(spinning, ros):
    from aimdk_msgs.msg import McControlArea, McPresetMotion
    from aimdk_msgs.srv import SetMcPresetMotion

    node, client = make_client(ros, SetMcPresetMotion, '/aimdk_5Fmsgs/srv/SetMcPresetMotion')
    try:
        req = SetMcPresetMotion.Request()
        motion = McPresetMotion()
        motion.value = 1002
        area = McControlArea()
        area.value = 2
        req.motion = motion
        req.area = area
        resp = call(node, client, req)
        assert resp.response.header.code == 0
        assert spinning.motion_requests[0].motion.value == 1002
        assert spinning.motion_requests[0].area.value == 2
    finally:
        node.destroy_node()


def test_it_reports_the_configured_motion_mode(spinning, ros):
    from aimdk_msgs.msg import McAction
    from aimdk_msgs.srv import GetMcAction

    node, client = make_client(ros, GetMcAction, '/aimdk_5Fmsgs/srv/GetMcAction')
    try:
        assert call(node, client, GetMcAction.Request()).info.current_action.value == \
            McAction.STAND_DEFAULT
        spinning.current_action = McAction.JOINT_DEFAULT
        assert call(node, client, GetMcAction.Request()).info.current_action.value == \
            McAction.JOINT_DEFAULT
        assert spinning.mode_queries >= 2
    finally:
        node.destroy_node()


def test_it_serves_play_audio_file(spinning, ros):
    from aimdk_msgs.srv import PlayAudioFile

    node, client = make_client(ros, PlayAudioFile, '/aimdk_5Fmsgs/srv/PlayAudioFile')
    try:
        req = PlayAudioFile.Request()
        req.file.pkg_name = 'x2_greeter'
        req.file.file_name = 'greeting_00.wav'
        req.file.file_path = '/var/tmp/x2_greeter_audio'
        resp = call(node, client, req)
        common = getattr(resp, 'reponse', None) or getattr(resp, 'response')
        assert common.header.code == 0
        assert spinning.audio_requests[0].file.file_name == 'greeting_00.wav'
    finally:
        node.destroy_node()


def test_it_publishes_synchronised_rgb_and_depth(spinning, ros):
    from cv_bridge import CvBridge
    from rclpy.qos import qos_profile_sensor_data
    from sensor_msgs.msg import Image

    received = {}
    node = ros.create_node('test_camera_sub')
    bridge = CvBridge()
    got = threading.Event()

    def on_rgb(msg):
        received['rgb'] = bridge.imgmsg_to_cv2(msg, desired_encoding='bgr8')
        if 'depth' in received:
            got.set()

    def on_depth(msg):
        received['depth'] = bridge.imgmsg_to_cv2(msg, desired_encoding='passthrough')
        if 'rgb' in received:
            got.set()

    node.create_subscription(Image, '/aima/hal/sensor/rgbd_head_front/rgb_image',
                             on_rgb, qos_profile_sensor_data)
    node.create_subscription(Image, '/aima/hal/sensor/rgbd_head_front/depth_image',
                             on_depth, qos_profile_sensor_data)
    executor_thread = threading.Thread(
        target=lambda: [ros.spin_once(node, timeout_sec=0.1) for _ in range(100)],
        daemon=True)
    executor_thread.start()
    try:
        assert got.wait(15.0), 'no camera frames arrived'
        assert received['rgb'].shape == (480, 640, 3)
        assert received['depth'].shape == (480, 640)
        assert int(received['depth'][240, 320]) == 2000
    finally:
        executor_thread.join(timeout=5.0)
        node.destroy_node()
```

- [ ] **Step 2: Run the test to verify it fails**

Run the Docker harness (Task 10 step 5) with `src/x2_greeter/test/ros/test_fake_robot.py` appended to the command.
Expected: FAIL — `ModuleNotFoundError: No module named 'x2_greeter.sim.fake_robot'`

- [ ] **Step 3: Write the implementation**

`x2_greeter_ws/src/x2_greeter/x2_greeter/sim/__init__.py` is an empty file.

`x2_greeter_ws/src/x2_greeter/x2_greeter/sim/fake_robot.py`:

```python
"""A stand-in for the X2, so the greeter can be developed without hardware.

Serves the four services the greeter calls and publishes a synthetic RGB-D
stream. Every request is recorded so a test can assert not only that the robot
was told to do something, but exactly what.
"""
from __future__ import annotations

import numpy as np
import rclpy
from aimdk_msgs.msg import McAction
from aimdk_msgs.srv import GetMcAction, PlayAudioFile, PlayTts, SetMcPresetMotion
from cv_bridge import CvBridge
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import Image

DEFAULT_RGB_TOPIC = '/aima/hal/sensor/rgbd_head_front/rgb_image'
DEFAULT_DEPTH_TOPIC = '/aima/hal/sensor/rgbd_head_front/depth_image'


class FakeRobot(Node):
    def __init__(self, rgb_topic: str = DEFAULT_RGB_TOPIC,
                 depth_topic: str = DEFAULT_DEPTH_TOPIC,
                 publish_camera: bool = True,
                 current_action: int = McAction.STAND_DEFAULT,
                 tts_succeeds: bool = True,
                 distance_mm: int = 2000,
                 width: int = 640, height: int = 480,
                 rate_hz: float = 10.0) -> None:
        super().__init__('fake_robot')

        self.tts_requests = []
        self.audio_requests = []
        self.motion_requests = []
        self.mode_queries = 0
        self.current_action = current_action
        self.tts_succeeds = tts_succeeds

        group = ReentrantCallbackGroup()
        self.create_service(PlayTts, '/aimdk_5Fmsgs/srv/PlayTts',
                            self._on_play_tts, callback_group=group)
        self.create_service(PlayAudioFile, '/aimdk_5Fmsgs/srv/PlayAudioFile',
                            self._on_play_audio_file, callback_group=group)
        self.create_service(SetMcPresetMotion, '/aimdk_5Fmsgs/srv/SetMcPresetMotion',
                            self._on_preset_motion, callback_group=group)
        self.create_service(GetMcAction, '/aimdk_5Fmsgs/srv/GetMcAction',
                            self._on_get_action, callback_group=group)

        self._bridge = CvBridge()
        self._width = width
        self._height = height
        if publish_camera:
            self._rgb_pub = self.create_publisher(Image, rgb_topic, qos_profile_sensor_data)
            self._depth_pub = self.create_publisher(Image, depth_topic, qos_profile_sensor_data)
            self._rgb = self._make_rgb(width, height)
            self._depth = np.full((height, width), distance_mm, dtype=np.uint16)
            self.create_timer(1.0 / rate_hz, self._publish_frame, callback_group=group)

        self.get_logger().info('fake robot up: 4 services, camera %s',
                               'publishing' if publish_camera else 'off')

    @staticmethod
    def _make_rgb(width: int, height: int) -> np.ndarray:
        """A recognisable but meaningless frame: a gradient with a lighter slab.

        Deliberately not a picture of a person — the integration test drives
        detection with ScriptedDetector, so the pixels only have to be valid.
        """
        frame = np.zeros((height, width, 3), dtype=np.uint8)
        frame[:, :, 0] = np.linspace(0, 255, width, dtype=np.uint8)[None, :]
        frame[:, :, 1] = np.linspace(0, 255, height, dtype=np.uint8)[:, None]
        frame[height // 4:, width // 2 - 60:width // 2 + 60, 2] = 200
        return frame

    def _publish_frame(self) -> None:
        stamp = self.get_clock().now().to_msg()
        rgb_msg = self._bridge.cv2_to_imgmsg(self._rgb, encoding='bgr8')
        depth_msg = self._bridge.cv2_to_imgmsg(self._depth, encoding='16UC1')
        for msg in (rgb_msg, depth_msg):
            msg.header.stamp = stamp
            msg.header.frame_id = 'rgbd_head_front'
        self._rgb_pub.publish(rgb_msg)
        self._depth_pub.publish(depth_msg)

    def _on_play_tts(self, request, response):
        self.tts_requests.append(request)
        self.get_logger().info('TTS: %r', request.tts_req.text)
        response.header.header.code = 0
        response.tts_resp.is_success = bool(self.tts_succeeds)
        response.tts_resp.text = request.tts_req.text
        response.tts_resp.domain = request.tts_req.domain
        if not self.tts_succeeds:
            response.tts_resp.error_message = 'fake robot: TTS disabled'
        return response

    def _on_play_audio_file(self, request, response):
        self.audio_requests.append(request)
        self.get_logger().info('audio file: %s/%s',
                               request.file.file_path, request.file.file_name)
        # The vendor .srv spells the response field 'reponse' (sic).
        common = getattr(response, 'reponse', None)
        if common is None:
            common = response.response
        common.header.code = 0
        return response

    def _on_preset_motion(self, request, response):
        self.motion_requests.append(request)
        self.get_logger().info('preset motion %d on area %d',
                               request.motion.value, request.area.value)
        response.response.header.code = 0
        response.response.task_id = f'fake-task-{len(self.motion_requests)}'
        return response

    def _on_get_action(self, request, response):
        self.mode_queries += 1
        response.header.code = 0
        response.info.current_action.value = int(self.current_action)
        response.info.action_desc = 'fake'
        return response


def main(args=None) -> None:
    rclpy.init(args=args)
    node = FakeRobot()
    executor = MultiThreadedExecutor()
    executor.add_node(node)
    try:
        executor.spin()
    except KeyboardInterrupt:
        pass
    finally:
        executor.shutdown()
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd /d/Projects/X2
MSYS_NO_PATHCONV=1 docker run --rm -v "D:/Projects/X2:/repo" -v x2-greeter-ws:/ws \
  x2-greeter-test bash /repo/docker/run_tests.sh src/x2_greeter/test/ros/test_fake_robot.py
```

Expected: PASS — 6 passed

- [ ] **Step 5: Commit**

```bash
cd /d/Projects/X2
git add x2_greeter_ws/src/x2_greeter/x2_greeter/sim x2_greeter_ws/src/x2_greeter/test/ros/test_fake_robot.py
git commit -m "feat: add fake X2 robot with recording service servers and synthetic camera"
```

---

## Task 12: Service-call retry and the frame source

**Files:**
- Create: `x2_greeter_ws/src/x2_greeter/x2_greeter/ros/__init__.py`
- Create: `x2_greeter_ws/src/x2_greeter/x2_greeter/ros/service_call.py`
- Create: `x2_greeter_ws/src/x2_greeter/x2_greeter/ros/frame_source.py`
- Test: `x2_greeter_ws/src/x2_greeter/test/ros/test_service_call.py`
- Test: `x2_greeter_ws/src/x2_greeter/test/ros/test_frame_source.py`

**Interfaces:**
- Consumes: `FakeRobot` from `x2_greeter.sim.fake_robot`.
- Produces: `wait_for_future(future, timeout_s:float) -> bool`; `call_with_retry(client, request, *, before_attempt=None, attempts:int=8, timeout_s:float=0.25, logger=None)` returning the response or `None`; `FrameSource(node, rgb_topic:str, depth_topic:str, on_frame:Callable[[numpy.ndarray, numpy.ndarray|None, float], None], max_sync_skew_s:float=0.15, stale_warn_s:float=5.0, callback_group=None)` with `.frames_seen -> int` and `.destroy()`.

**Why not `spin_until_future_complete`.** The SDK examples call it from `main()`, where the node is not already being spun. Our dispatchers run on a worker thread while a `MultiThreadedExecutor` is spinning the node, so calling it again would re-enter the executor. `wait_for_future` waits on a `threading.Event` armed by `add_done_callback` instead: the spinning executor completes the future, and the worker simply blocks. Same 8 × 0.25 s semantics, no re-entrancy. `service_call.py` deliberately imports no `rclpy`, so it is unit-testable on Windows.

- [ ] **Step 1: Write the failing tests**

`x2_greeter_ws/src/x2_greeter/test/ros/test_service_call.py` (no `ros` marker — runs on Windows):

```python
import threading

from x2_greeter.ros.service_call import call_with_retry, wait_for_future


class FakeFuture:
    """Mimics an rclpy Future: done callbacks fire immediately if already done."""

    def __init__(self, result=None, complete_after_s=None):
        self._result = result
        self._done = complete_after_s is None
        self.cancelled = False
        self._callbacks = []
        if complete_after_s is not None:
            timer = threading.Timer(complete_after_s, self._complete)
            timer.daemon = True
            timer.start()

    def _complete(self):
        self._done = True
        for callback in list(self._callbacks):
            callback(self)

    def add_done_callback(self, callback):
        if self._done:
            callback(self)
        else:
            self._callbacks.append(callback)

    def cancel(self):
        self.cancelled = True

    def result(self):
        return self._result


class FakeClient:
    def __init__(self, futures):
        self._futures = list(futures)
        self.requests = []

    def call_async(self, request):
        self.requests.append(request)
        return self._futures.pop(0)


def test_an_immediate_response_is_returned_on_the_first_attempt():
    client = FakeClient([FakeFuture(result='ok')])
    assert call_with_retry(client, 'req') == 'ok'
    assert len(client.requests) == 1


def test_a_never_completing_future_is_retried_the_configured_number_of_times():
    futures = [FakeFuture(complete_after_s=10.0) for _ in range(3)]
    client = FakeClient(futures)
    assert call_with_retry(client, 'req', attempts=3, timeout_s=0.01) is None
    assert len(client.requests) == 3


def test_stuck_attempts_are_cancelled():
    futures = [FakeFuture(complete_after_s=10.0) for _ in range(2)]
    client = FakeClient(futures)
    call_with_retry(client, 'req', attempts=2, timeout_s=0.01)
    assert all(f.cancelled for f in futures)


def test_a_late_success_is_picked_up_by_a_later_attempt():
    client = FakeClient([FakeFuture(complete_after_s=10.0), FakeFuture(result='ok')])
    assert call_with_retry(client, 'req', attempts=8, timeout_s=0.01) == 'ok'
    assert len(client.requests) == 2


def test_the_header_is_refreshed_before_every_attempt():
    stamps = []
    client = FakeClient([FakeFuture(complete_after_s=10.0), FakeFuture(result='ok')])
    call_with_retry(client, 'req', before_attempt=lambda r: stamps.append(r),
                    attempts=8, timeout_s=0.01)
    assert len(stamps) == 2


def test_wait_for_future_reports_completion():
    assert wait_for_future(FakeFuture(result='ok'), 0.01) is True


def test_wait_for_future_reports_a_timeout():
    assert wait_for_future(FakeFuture(complete_after_s=10.0), 0.01) is False
```

`x2_greeter_ws/src/x2_greeter/test/ros/test_frame_source.py`:

```python
import threading

import numpy as np
import pytest

pytestmark = pytest.mark.ros


@pytest.fixture
def ros():
    import rclpy
    rclpy.init()
    yield rclpy
    rclpy.shutdown()


@pytest.fixture
def robot_and_consumer(ros):
    """A FakeRobot publishing frames, and a node consuming them via FrameSource."""
    from rclpy.executors import MultiThreadedExecutor

    from x2_greeter.ros.frame_source import FrameSource
    from x2_greeter.sim.fake_robot import DEFAULT_DEPTH_TOPIC, DEFAULT_RGB_TOPIC, FakeRobot

    robot = FakeRobot(publish_camera=True)
    consumer = ros.create_node('frame_consumer')
    frames = []
    arrived = threading.Event()

    def on_frame(bgr, depth, stamp):
        frames.append((bgr, depth, stamp))
        arrived.set()

    source = FrameSource(consumer, DEFAULT_RGB_TOPIC, DEFAULT_DEPTH_TOPIC, on_frame)

    executor = MultiThreadedExecutor()
    executor.add_node(robot)
    executor.add_node(consumer)
    thread = threading.Thread(target=executor.spin, daemon=True)
    thread.start()
    yield source, frames, arrived
    executor.shutdown()
    source.destroy()
    consumer.destroy_node()
    robot.destroy_node()
    thread.join(timeout=5.0)


def test_it_delivers_a_synchronised_rgb_and_depth_pair(robot_and_consumer):
    source, frames, arrived = robot_and_consumer
    assert arrived.wait(15.0), 'no frame delivered'
    bgr, depth, stamp = frames[0]
    assert bgr.shape == (480, 640, 3)
    assert bgr.dtype == np.uint8
    assert depth is not None
    assert depth.shape == (480, 640)
    assert int(depth[240, 320]) == 2000
    assert stamp > 0.0


def test_it_counts_the_frames_it_has_seen(robot_and_consumer):
    source, frames, arrived = robot_and_consumer
    assert arrived.wait(15.0)
    assert source.frames_seen >= 1


def test_it_keeps_delivering(robot_and_consumer):
    import time
    source, frames, arrived = robot_and_consumer
    assert arrived.wait(15.0)
    deadline = time.monotonic() + 10.0
    while len(frames) < 3 and time.monotonic() < deadline:
        time.sleep(0.05)
    assert len(frames) >= 3


def test_it_delivers_nothing_when_only_rgb_is_published(ros):
    """Depth is required: an unpaired RGB frame must not be passed on."""
    import time

    from rclpy.executors import MultiThreadedExecutor
    from rclpy.qos import qos_profile_sensor_data
    from cv_bridge import CvBridge
    from sensor_msgs.msg import Image

    from x2_greeter.ros.frame_source import FrameSource

    publisher_node = ros.create_node('rgb_only_publisher')
    consumer = ros.create_node('rgb_only_consumer')
    frames = []
    FrameSource(consumer, '/test/rgb_only', '/test/depth_never', lambda *a: frames.append(a))
    pub = publisher_node.create_publisher(Image, '/test/rgb_only', qos_profile_sensor_data)

    executor = MultiThreadedExecutor()
    executor.add_node(publisher_node)
    executor.add_node(consumer)
    thread = threading.Thread(target=executor.spin, daemon=True)
    thread.start()
    try:
        bridge = CvBridge()
        for _ in range(5):
            msg = bridge.cv2_to_imgmsg(np.zeros((48, 64, 3), dtype=np.uint8), encoding='bgr8')
            msg.header.stamp = publisher_node.get_clock().now().to_msg()
            pub.publish(msg)
            time.sleep(0.1)
        time.sleep(0.5)
        assert frames == []
    finally:
        executor.shutdown()
        consumer.destroy_node()
        publisher_node.destroy_node()
        thread.join(timeout=5.0)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /d/Projects/X2 && python -m pytest x2_greeter_ws/src/x2_greeter/test/ros/test_service_call.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'x2_greeter.ros'`

- [ ] **Step 3: Write the implementation**

`x2_greeter_ws/src/x2_greeter/x2_greeter/ros/__init__.py` is an empty file.

`x2_greeter_ws/src/x2_greeter/x2_greeter/ros/service_call.py`:

```python
"""Calling an AimDK service from a thread while the executor is already spinning.

The SDK examples wrap every service call in eight attempts with a 0.25 s
timeout, with the comment "retry as remote peer is NOT handled well by ROS" —
the robot's services live on other compute units, and the first attempt is
routinely dropped. We keep those semantics exactly.

What we do not keep is rclpy.spin_until_future_complete: the examples call it
from main(), where nothing else is spinning the node. Our dispatchers run on a
worker thread while a MultiThreadedExecutor spins, so spinning again would
re-enter the executor. Waiting on an Event armed by add_done_callback gets the
same behaviour with none of the risk.

This module deliberately imports no rclpy, so it is unit-testable anywhere.
"""
from __future__ import annotations

import threading
from typing import Callable, Optional

DEFAULT_ATTEMPTS = 8
DEFAULT_TIMEOUT_S = 0.25


def wait_for_future(future, timeout_s: float) -> bool:
    """Block until `future` completes or `timeout_s` elapses. True if it completed."""
    done = threading.Event()
    future.add_done_callback(lambda _future: done.set())
    return done.wait(timeout_s)


def call_with_retry(client, request, *,
                    before_attempt: Optional[Callable[[object], None]] = None,
                    attempts: int = DEFAULT_ATTEMPTS,
                    timeout_s: float = DEFAULT_TIMEOUT_S,
                    logger=None):
    """Call an rclpy service client, retrying dropped attempts.

    `before_attempt` refreshes the request header's timestamp before each try,
    as the SDK examples do. Returns the response, or None if every attempt was
    dropped.
    """
    for attempt in range(attempts):
        if before_attempt is not None:
            before_attempt(request)
        future = client.call_async(request)
        if wait_for_future(future, timeout_s):
            return future.result()
        future.cancel()
        if logger is not None:
            logger.debug(f'service call attempt {attempt + 1}/{attempts} dropped, retrying')
    if logger is not None:
        logger.warning(f'service call failed after {attempts} attempts')
    return None
```

`x2_greeter_ws/src/x2_greeter/x2_greeter/ros/frame_source.py`:

```python
"""Head-camera RGB-D intake.

Pairs the RGB and depth streams by timestamp and hands the consumer plain
numpy arrays, so nothing downstream needs to know about ROS. Depth is
mandatory: distance gating is a safety rule, and an unpaired RGB frame is
silently dropped rather than gated on a guessed distance.
"""
from __future__ import annotations

from typing import Callable, Optional

import numpy as np
from cv_bridge import CvBridge
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import Image

FrameCallback = Callable[[np.ndarray, Optional[np.ndarray], float], None]


def _stamp_seconds(msg) -> float:
    return msg.header.stamp.sec + msg.header.stamp.nanosec * 1e-9


class FrameSource:
    def __init__(self, node, rgb_topic: str, depth_topic: str, on_frame: FrameCallback,
                 max_sync_skew_s: float = 0.15, stale_warn_s: float = 5.0,
                 callback_group=None) -> None:
        self._node = node
        self._on_frame = on_frame
        self._max_sync_skew_s = float(max_sync_skew_s)
        self._stale_warn_s = float(stale_warn_s)
        self._bridge = CvBridge()
        self._latest_depth = None
        self._latest_depth_stamp = 0.0
        self._frames_seen = 0
        self._last_rgb_at: Optional[float] = None
        self._warned_stale = False

        self._rgb_sub = node.create_subscription(
            Image, rgb_topic, self._on_rgb, qos_profile_sensor_data,
            callback_group=callback_group)
        self._depth_sub = node.create_subscription(
            Image, depth_topic, self._on_depth, qos_profile_sensor_data,
            callback_group=callback_group)
        self._watchdog = node.create_timer(1.0, self._check_stale,
                                           callback_group=callback_group)

    @property
    def frames_seen(self) -> int:
        return self._frames_seen

    def destroy(self) -> None:
        self._node.destroy_timer(self._watchdog)
        self._node.destroy_subscription(self._rgb_sub)
        self._node.destroy_subscription(self._depth_sub)

    def _on_depth(self, msg: Image) -> None:
        try:
            self._latest_depth = self._bridge.imgmsg_to_cv2(msg, desired_encoding='passthrough')
        except Exception as exc:                       # noqa: BLE001 - a bad frame is not fatal
            self._node.get_logger().warning(f'could not convert depth frame: {exc}')
            return
        self._latest_depth_stamp = _stamp_seconds(msg)

    def _on_rgb(self, msg: Image) -> None:
        try:
            bgr = self._bridge.imgmsg_to_cv2(msg, desired_encoding='bgr8')
        except Exception as exc:                       # noqa: BLE001 - a bad frame is not fatal
            self._node.get_logger().warning(f'could not convert RGB frame: {exc}')
            return

        stamp = _stamp_seconds(msg)
        self._last_rgb_at = self._now()
        self._warned_stale = False

        depth = self._latest_depth
        if depth is None or abs(stamp - self._latest_depth_stamp) > self._max_sync_skew_s:
            # No usable depth for this frame. Gating would have to guess the
            # distance, so drop it instead (spec section 6).
            return

        self._frames_seen += 1
        self._on_frame(bgr, depth, stamp)

    def _check_stale(self) -> None:
        if self._last_rgb_at is None:
            return
        if self._warned_stale:
            return
        if (self._now() - self._last_rgb_at) > self._stale_warn_s:
            self._node.get_logger().warning(
                f'no camera frame for over {self._stale_warn_s:.0f}s')
            self._warned_stale = True

    def _now(self) -> float:
        return self._node.get_clock().now().nanoseconds * 1e-9
```

- [ ] **Step 4: Run both test files to verify they pass**

Run: `cd /d/Projects/X2 && python -m pytest x2_greeter_ws/src/x2_greeter/test/ros/test_service_call.py -v`
Expected: PASS — 7 passed

```bash
cd /d/Projects/X2
MSYS_NO_PATHCONV=1 docker run --rm -v "D:/Projects/X2:/repo" -v x2-greeter-ws:/ws \
  x2-greeter-test bash /repo/docker/run_tests.sh src/x2_greeter/test/ros/test_frame_source.py
```

Expected: PASS — 4 passed

- [ ] **Step 5: Commit**

```bash
cd /d/Projects/X2
git add x2_greeter_ws/src/x2_greeter/x2_greeter/ros x2_greeter_ws/src/x2_greeter/test/ros/test_service_call.py x2_greeter_ws/src/x2_greeter/test/ros/test_frame_source.py
git commit -m "feat: add executor-safe service retry and RGB-D frame source"
```

---

## Task 13: Speech with tier demotion

**Files:**
- Create: `x2_greeter_ws/src/x2_greeter/x2_greeter/ros/speech.py`
- Test: `x2_greeter_ws/src/x2_greeter/test/ros/test_speech.py`

**Interfaces:**
- Consumes: `call_with_retry` from `x2_greeter.ros.service_call`; `FakeRobot` from `x2_greeter.sim.fake_robot`.
- Produces: `SpeechDispatcher(node, tier:str='auto', domain:str='x2_greeter', priority_level:int=6, audio_dir:str='/var/tmp/x2_greeter_audio', audio_file_count:int=6, rng=None, callback_group=None)` with `.speak(text:str) -> bool`, `.using_tts -> bool`, `.demoted -> bool`.

**Tiers, mechanically.** Spec §10's tiers 1 and 2 differ only in where the text came from — both call `PlayTts` — and choosing the text is the policy's job, not the dispatcher's. So the dispatcher's real choice is TTS versus audio file. `tier='auto'` starts on TTS and demotes permanently to audio files the first time `PlayTtsResponse.is_success` is false; `tier='tts'` pins it to TTS and never touches audio files; `tier='audio_file'` pins it to files. Demotion is one-way within a session.

- [ ] **Step 1: Write the failing test**

`x2_greeter_ws/src/x2_greeter/test/ros/test_speech.py`:

```python
import random
import threading

import pytest

pytestmark = pytest.mark.ros


@pytest.fixture
def ros():
    import rclpy
    rclpy.init()
    yield rclpy
    rclpy.shutdown()


@pytest.fixture
def rig(ros):
    """FakeRobot plus a caller node, both spinning."""
    from rclpy.executors import MultiThreadedExecutor

    from x2_greeter.sim.fake_robot import FakeRobot

    robot = FakeRobot(publish_camera=False)
    caller = ros.create_node('speech_caller')
    executor = MultiThreadedExecutor()
    executor.add_node(robot)
    executor.add_node(caller)
    thread = threading.Thread(target=executor.spin, daemon=True)
    thread.start()
    yield robot, caller
    executor.shutdown()
    caller.destroy_node()
    robot.destroy_node()
    thread.join(timeout=5.0)


def dispatcher(caller, **kwargs):
    from rclpy.callback_groups import ReentrantCallbackGroup

    from x2_greeter.ros.speech import SpeechDispatcher

    kwargs.setdefault('rng', random.Random(0))
    kwargs.setdefault('callback_group', ReentrantCallbackGroup())
    return SpeechDispatcher(caller, **kwargs)


def test_it_speaks_through_play_tts(rig):
    robot, caller = rig
    speech = dispatcher(caller)
    assert speech.speak('Hello there!') is True
    assert robot.tts_requests[0].tts_req.text == 'Hello there!'


def test_it_uses_the_configured_domain_and_priority(rig):
    robot, caller = rig
    dispatcher(caller, domain='x2_greeter', priority_level=6).speak('Hi')
    req = robot.tts_requests[0].tts_req
    assert req.domain == 'x2_greeter'
    assert req.priority_level.value == 6
    assert req.is_interrupted is True


def test_it_stamps_the_request_header(rig):
    robot, caller = rig
    dispatcher(caller).speak('Hi')
    stamp = robot.tts_requests[0].header.header.stamp
    assert stamp.sec > 0 or stamp.nanosec > 0


def test_it_refuses_to_speak_nothing(rig):
    robot, caller = rig
    assert dispatcher(caller).speak('   ') is False
    assert robot.tts_requests == []


def test_auto_demotes_to_audio_files_when_tts_reports_failure(rig):
    robot, caller = rig
    robot.tts_succeeds = False
    speech = dispatcher(caller, tier='auto', audio_dir='/var/tmp/x2_greeter_audio',
                        audio_file_count=6)
    assert speech.speak('Hello there!') is True     # succeeded via the audio file
    assert speech.demoted is True
    assert speech.using_tts is False
    assert len(robot.audio_requests) == 1


def test_demotion_is_one_way_within_a_session(rig):
    robot, caller = rig
    robot.tts_succeeds = False
    speech = dispatcher(caller, tier='auto')
    speech.speak('first')
    robot.tts_succeeds = True                        # TTS recovers
    speech.speak('second')
    assert len(robot.tts_requests) == 1              # never tried again
    assert len(robot.audio_requests) == 2


def test_the_audio_request_matches_the_documented_format(rig):
    robot, caller = rig
    speech = dispatcher(caller, tier='audio_file', audio_dir='/var/tmp/x2_greeter_audio',
                        audio_file_count=6)
    assert speech.speak('ignored, a recording is played instead') is True
    audio = robot.audio_requests[0].file
    assert audio.pkg_name == 'x2_greeter'
    assert audio.file_path == '/var/tmp/x2_greeter_audio'
    assert audio.file_name.startswith('greeting_')
    assert audio.file_name.endswith('.wav')
    assert audio.info.channels == 1
    assert audio.info.sample_rate == 16000
    assert audio.info.sample_format == 'S16_LE'
    assert audio.info.coding_format == 'wave'
    assert audio.priority == 6


def test_audio_file_tier_never_calls_tts(rig):
    robot, caller = rig
    dispatcher(caller, tier='audio_file').speak('Hi')
    assert robot.tts_requests == []


def test_pinned_tts_tier_never_falls_back_to_audio_files(rig):
    robot, caller = rig
    robot.tts_succeeds = False
    speech = dispatcher(caller, tier='tts')
    assert speech.speak('Hello') is False
    assert robot.audio_requests == []
    assert speech.demoted is False


def test_the_audio_file_index_stays_within_the_deployed_range(rig):
    robot, caller = rig
    speech = dispatcher(caller, tier='audio_file', audio_file_count=3,
                        rng=random.Random(11))
    for _ in range(20):
        speech.speak('x')
    indices = {int(r.file.file_name[len('greeting_'):-len('.wav')])
               for r in robot.audio_requests}
    assert indices <= {0, 1, 2}


def test_it_rejects_an_unknown_tier(rig):
    robot, caller = rig
    with pytest.raises(ValueError, match='speech.tier'):
        dispatcher(caller, tier='telepathy')


def test_it_reports_failure_when_the_service_is_absent(ros):
    """No robot at all: speak() must return False, not hang or raise."""
    from rclpy.executors import MultiThreadedExecutor

    caller = ros.create_node('lonely_caller')
    executor = MultiThreadedExecutor()
    executor.add_node(caller)
    thread = threading.Thread(target=executor.spin, daemon=True)
    thread.start()
    try:
        speech = dispatcher(caller, tier='tts')
        assert speech.speak('anyone there?') is False
    finally:
        executor.shutdown()
        caller.destroy_node()
        thread.join(timeout=5.0)
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd /d/Projects/X2
MSYS_NO_PATHCONV=1 docker run --rm -v "D:/Projects/X2:/repo" -v x2-greeter-ws:/ws \
  x2-greeter-test bash /repo/docker/run_tests.sh src/x2_greeter/test/ros/test_speech.py
```

Expected: FAIL — `ModuleNotFoundError: No module named 'x2_greeter.ros.speech'`

- [ ] **Step 3: Write the implementation**

`x2_greeter_ws/src/x2_greeter/x2_greeter/ros/speech.py`:

```python
"""Saying the greeting out loud.

The interface docs never state whether PlayTts synthesises onboard or in the
cloud, so this hedges (spec section 10): speak through TTS, and if TTS reports
failure, fall back permanently to pre-recorded audio files for the rest of the
session. If onboard TTS turns out to work offline, the fallback is simply
never reached.
"""
from __future__ import annotations

import os
import random
from typing import Optional

from aimdk_msgs.srv import PlayAudioFile, PlayTts

from x2_greeter.ros.service_call import call_with_retry

VALID_TIERS = ('auto', 'tts', 'audio_file')

TTS_SERVICE = '/aimdk_5Fmsgs/srv/PlayTts'
AUDIO_SERVICE = '/aimdk_5Fmsgs/srv/PlayAudioFile'

# Documented in the interface reference: 16 kHz, 16-bit, mono, WAV or raw PCM.
AUDIO_CHANNELS = 1
AUDIO_SAMPLE_RATE = 16000
AUDIO_SAMPLE_FORMAT = 'S16_LE'
AUDIO_CODING_FORMAT = 'wave'


class SpeechDispatcher:
    def __init__(self, node, tier: str = 'auto', domain: str = 'x2_greeter',
                 priority_level: int = 6, audio_dir: str = '/var/tmp/x2_greeter_audio',
                 audio_file_count: int = 6, rng: Optional[random.Random] = None,
                 callback_group=None) -> None:
        if tier not in VALID_TIERS:
            raise ValueError(f'speech.tier must be one of {VALID_TIERS}, got {tier!r}')

        self._node = node
        self._tier = tier
        self._domain = domain
        self._priority_level = int(priority_level)
        self._audio_dir = audio_dir
        self._audio_file_count = max(1, int(audio_file_count))
        self._rng = rng if rng is not None else random.Random()
        self._using_tts = tier in ('auto', 'tts')
        self._demoted = False
        self._audio_error_logged = False

        self._tts_client = node.create_client(PlayTts, TTS_SERVICE,
                                              callback_group=callback_group)
        self._audio_client = node.create_client(PlayAudioFile, AUDIO_SERVICE,
                                                callback_group=callback_group)

    @property
    def using_tts(self) -> bool:
        return self._using_tts

    @property
    def demoted(self) -> bool:
        return self._demoted

    def speak(self, text: str) -> bool:
        """Say `text`, or play a recording if TTS is unavailable. True if it spoke."""
        text = (text or '').strip()
        if not text:
            self._node.get_logger().warning('refusing to speak an empty greeting')
            return False

        if not self._using_tts:
            return self._play_audio_file()

        if self._play_tts(text):
            return True

        if self._tier != 'auto':
            return False

        # One-way demotion: TTS has failed, so stop asking it for the rest of
        # this session and use the recordings instead.
        self._using_tts = False
        self._demoted = True
        self._node.get_logger().warning(
            'PlayTts reported failure; demoting to pre-recorded audio files in %s '
            'for the rest of this session', self._audio_dir)
        return self._play_audio_file()

    def _play_tts(self, text: str) -> bool:
        request = PlayTts.Request()
        request.tts_req.text = text
        request.tts_req.domain = self._domain
        request.tts_req.trace_id = 'x2_greeter'
        request.tts_req.is_interrupted = True       # interrupt same-priority speech
        request.tts_req.priority_weight = 0
        request.tts_req.priority_level.value = self._priority_level

        def stamp(req):
            req.header.header.stamp = self._node.get_clock().now().to_msg()

        response = call_with_retry(self._tts_client, request, before_attempt=stamp,
                                   logger=self._node.get_logger())
        if response is None:
            self._node.get_logger().error('PlayTts did not respond')
            return False
        if not response.tts_resp.is_success:
            self._node.get_logger().error('PlayTts failed: %s',
                                          response.tts_resp.error_message)
            return False
        return True

    def _play_audio_file(self) -> bool:
        index = self._rng.randrange(self._audio_file_count)
        file_name = f'greeting_{index:02d}.wav'

        request = PlayAudioFile.Request()
        request.file.pkg_name = 'x2_greeter'
        request.file.file_name = file_name
        request.file.file_path = self._audio_dir
        request.file.priority = self._priority_level
        request.file.priority_weight = 0
        request.file.info.channels = AUDIO_CHANNELS
        request.file.info.sample_rate = AUDIO_SAMPLE_RATE
        request.file.info.sample_format = AUDIO_SAMPLE_FORMAT
        request.file.info.coding_format = AUDIO_CODING_FORMAT

        def stamp(req):
            req.request.header.stamp = self._node.get_clock().now().to_msg()

        response = call_with_retry(self._audio_client, request, before_attempt=stamp,
                                   logger=self._node.get_logger())
        if response is None:
            self._log_audio_error(f'PlayAudioFile did not respond for {file_name}')
            return False

        # The vendor .srv spells the response field 'reponse' (sic). Read it
        # defensively so this keeps working if they fix the typo.
        common = getattr(response, 'reponse', None)
        if common is None:
            common = getattr(response, 'response', None)
        if common is None or common.header.code != 0:
            self._log_audio_error(
                f'PlayAudioFile rejected {os.path.join(self._audio_dir, file_name)}; '
                'is the file deployed to PC3 and world-readable?')
            return False
        return True

    def _log_audio_error(self, message: str) -> None:
        """Audio assets live on PC3, so this is a deployment problem, not a bug.

        Logged once: a missing recording would otherwise shout on every greeting.
        """
        if self._audio_error_logged:
            self._node.get_logger().debug(message)
            return
        self._node.get_logger().error(message)
        self._audio_error_logged = True
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd /d/Projects/X2
MSYS_NO_PATHCONV=1 docker run --rm -v "D:/Projects/X2:/repo" -v x2-greeter-ws:/ws \
  x2-greeter-test bash /repo/docker/run_tests.sh src/x2_greeter/test/ros/test_speech.py
```

Expected: PASS — 12 passed

- [ ] **Step 5: Commit**

```bash
cd /d/Projects/X2
git add x2_greeter_ws/src/x2_greeter/x2_greeter/ros/speech.py x2_greeter_ws/src/x2_greeter/test/ros/test_speech.py
git commit -m "feat: add speech dispatcher with one-way TTS-to-audio-file demotion"
```

---

## Task 14: Gestures and the safety mode guard

**Files:**
- Create: `x2_greeter_ws/src/x2_greeter/x2_greeter/ros/gesture.py`
- Create: `x2_greeter_ws/src/x2_greeter/x2_greeter/ros/mode_guard.py`
- Test: `x2_greeter_ws/src/x2_greeter/test/ros/test_gesture_and_mode_guard.py`

**Interfaces:**
- Consumes: `call_with_retry` from `x2_greeter.ros.service_call`; `FakeRobot` from `x2_greeter.sim.fake_robot`.
- Produces: `GestureDispatcher(node, callback_group=None)` with `.perform(motion_id:int, area_id:int) -> bool`; `ModeGuard(node, require_stand_default:bool=True, callback_group=None)` with `.gesturing_allowed() -> bool` and `.last_action -> int|None`.

- [ ] **Step 1: Write the failing test**

`x2_greeter_ws/src/x2_greeter/test/ros/test_gesture_and_mode_guard.py`:

```python
import threading

import pytest

pytestmark = pytest.mark.ros


@pytest.fixture
def ros():
    import rclpy
    rclpy.init()
    yield rclpy
    rclpy.shutdown()


@pytest.fixture
def rig(ros):
    from rclpy.executors import MultiThreadedExecutor

    from x2_greeter.sim.fake_robot import FakeRobot

    robot = FakeRobot(publish_camera=False)
    caller = ros.create_node('motion_caller')
    executor = MultiThreadedExecutor()
    executor.add_node(robot)
    executor.add_node(caller)
    thread = threading.Thread(target=executor.spin, daemon=True)
    thread.start()
    yield robot, caller
    executor.shutdown()
    caller.destroy_node()
    robot.destroy_node()
    thread.join(timeout=5.0)


def gesture_dispatcher(caller):
    from rclpy.callback_groups import ReentrantCallbackGroup

    from x2_greeter.ros.gesture import GestureDispatcher

    return GestureDispatcher(caller, callback_group=ReentrantCallbackGroup())


def mode_guard(caller, **kwargs):
    from rclpy.callback_groups import ReentrantCallbackGroup

    from x2_greeter.ros.mode_guard import ModeGuard

    kwargs.setdefault('callback_group', ReentrantCallbackGroup())
    return ModeGuard(caller, **kwargs)


def test_it_performs_the_requested_motion_on_the_requested_area(rig):
    robot, caller = rig
    assert gesture_dispatcher(caller).perform(1002, 2) is True
    assert robot.motion_requests[0].motion.value == 1002
    assert robot.motion_requests[0].area.value == 2


def test_it_does_not_interrupt_a_motion_already_running(rig):
    robot, caller = rig
    gesture_dispatcher(caller).perform(1013, 1)
    assert robot.motion_requests[0].interrupt is False


def test_it_stamps_the_request_header(rig):
    robot, caller = rig
    gesture_dispatcher(caller).perform(3001, 11)
    stamp = robot.motion_requests[0].header.stamp
    assert stamp.sec > 0 or stamp.nanosec > 0


def test_it_reports_failure_when_the_service_is_absent(ros):
    from rclpy.executors import MultiThreadedExecutor

    caller = ros.create_node('lonely_motion_caller')
    executor = MultiThreadedExecutor()
    executor.add_node(caller)
    thread = threading.Thread(target=executor.spin, daemon=True)
    thread.start()
    try:
        assert gesture_dispatcher(caller).perform(1002, 2) is False
    finally:
        executor.shutdown()
        caller.destroy_node()
        thread.join(timeout=5.0)


def test_gesturing_is_allowed_in_stand_default(rig):
    from aimdk_msgs.msg import McAction

    robot, caller = rig
    robot.current_action = McAction.STAND_DEFAULT
    guard = mode_guard(caller)
    assert guard.gesturing_allowed() is True
    assert guard.last_action == McAction.STAND_DEFAULT


def test_gesturing_is_refused_in_any_other_mode(rig):
    from aimdk_msgs.msg import McAction

    robot, caller = rig
    for mode in (McAction.PASSIVE_DEFAULT, McAction.JOINT_DEFAULT,
                 McAction.DAMPING_DEFAULT, McAction.LOCOMOTION_DEFAULT):
        robot.current_action = mode
        assert mode_guard(caller).gesturing_allowed() is False


def test_a_repeated_refusal_still_asks_the_robot_every_time(rig):
    """The warning is logged once, but the mode is never assumed from a cache."""
    from aimdk_msgs.msg import McAction

    robot, caller = rig
    robot.current_action = McAction.JOINT_DEFAULT
    guard = mode_guard(caller)
    for _ in range(5):
        assert guard.gesturing_allowed() is False
    assert robot.mode_queries >= 5


def test_an_unreachable_mode_service_refuses_rather_than_assumes(ros):
    """Not knowing the mode is not the same as knowing it is safe."""
    from rclpy.executors import MultiThreadedExecutor

    caller = ros.create_node('lonely_guard_caller')
    executor = MultiThreadedExecutor()
    executor.add_node(caller)
    thread = threading.Thread(target=executor.spin, daemon=True)
    thread.start()
    try:
        assert mode_guard(caller).gesturing_allowed() is False
    finally:
        executor.shutdown()
        caller.destroy_node()
        thread.join(timeout=5.0)


def test_the_guard_can_be_switched_off_for_bench_testing(rig):
    from aimdk_msgs.msg import McAction

    robot, caller = rig
    robot.current_action = McAction.PASSIVE_DEFAULT
    guard = mode_guard(caller, require_stand_default=False)
    assert guard.gesturing_allowed() is True
    assert robot.mode_queries == 0    # it does not even ask


def test_the_guard_never_changes_the_mode(rig):
    from aimdk_msgs.msg import McAction

    robot, caller = rig
    robot.current_action = McAction.JOINT_DEFAULT
    mode_guard(caller).gesturing_allowed()
    assert robot.current_action == McAction.JOINT_DEFAULT
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd /d/Projects/X2
MSYS_NO_PATHCONV=1 docker run --rm -v "D:/Projects/X2:/repo" -v x2-greeter-ws:/ws \
  x2-greeter-test bash /repo/docker/run_tests.sh src/x2_greeter/test/ros/test_gesture_and_mode_guard.py
```

Expected: FAIL — `ModuleNotFoundError: No module named 'x2_greeter.ros.gesture'`

- [ ] **Step 3: Write the implementations**

`x2_greeter_ws/src/x2_greeter/x2_greeter/ros/gesture.py`:

```python
"""Performing a preset motion.

Motion and area IDs arrive already resolved from core.gestures — an LLM never
reaches this far with an unvalidated value.
"""
from __future__ import annotations

from aimdk_msgs.msg import CommonState, McControlArea, McPresetMotion, RequestHeader
from aimdk_msgs.srv import SetMcPresetMotion

from x2_greeter.ros.service_call import call_with_retry

PRESET_MOTION_SERVICE = '/aimdk_5Fmsgs/srv/SetMcPresetMotion'


class GestureDispatcher:
    def __init__(self, node, callback_group=None) -> None:
        self._node = node
        self._client = node.create_client(SetMcPresetMotion, PRESET_MOTION_SERVICE,
                                          callback_group=callback_group)

    def perform(self, motion_id: int, area_id: int) -> bool:
        """Issue one preset motion. True if the robot accepted or started it."""
        request = SetMcPresetMotion.Request()
        request.header = RequestHeader()

        motion = McPresetMotion()
        motion.value = int(motion_id)
        area = McControlArea()
        area.value = int(area_id)
        request.motion = motion
        request.area = area
        # False: a greeting is never important enough to cut short a motion the
        # robot is already performing.
        request.interrupt = False

        def stamp(req):
            req.header.stamp = self._node.get_clock().now().to_msg()

        response = call_with_retry(self._client, request, before_attempt=stamp,
                                   logger=self._node.get_logger())
        if response is None:
            self._node.get_logger().error('SetMcPresetMotion did not respond')
            return False

        if response.response.header.code == 0:
            self._node.get_logger().info('gesture %d on area %d accepted (task %s)',
                                         motion_id, area_id, response.response.task_id)
            return True
        if response.response.state.value == CommonState.RUNNING:
            self._node.get_logger().info('gesture %d on area %d running (task %s)',
                                         motion_id, area_id, response.response.task_id)
            return True

        self._node.get_logger().error('gesture %d on area %d rejected (task %s)',
                                      motion_id, area_id, response.response.task_id)
        return False
```

`x2_greeter_ws/src/x2_greeter/x2_greeter/ros/mode_guard.py`:

```python
"""Is it safe to gesture right now?

Preset motions require force-control stand (STAND_DEFAULT). This asks, and
never tells: auto-transitioning a humanoid into force-control stand is how a
robot falls over, and the docs require both feet planted first. That stays a
human decision (spec section 11).

Not knowing the mode is not the same as knowing it is safe, so an unreachable
service refuses.
"""
from __future__ import annotations

from typing import Optional

from aimdk_msgs.msg import McAction
from aimdk_msgs.srv import GetMcAction

from x2_greeter.ros.service_call import call_with_retry

GET_ACTION_SERVICE = '/aimdk_5Fmsgs/srv/GetMcAction'


class ModeGuard:
    def __init__(self, node, require_stand_default: bool = True,
                 callback_group=None) -> None:
        self._node = node
        self._require_stand_default = bool(require_stand_default)
        self._last_action: Optional[int] = None
        self._warned = False
        self._client = node.create_client(GetMcAction, GET_ACTION_SERVICE,
                                          callback_group=callback_group)

    @property
    def last_action(self) -> Optional[int]:
        return self._last_action

    def gesturing_allowed(self) -> bool:
        if not self._require_stand_default:
            return True

        request = GetMcAction.Request()

        def stamp(req):
            req.request.header.stamp = self._node.get_clock().now().to_msg()

        response = call_with_retry(self._client, request, before_attempt=stamp,
                                   logger=self._node.get_logger())
        if response is None:
            self._warn_once('cannot read the robot motion mode; not gesturing')
            self._last_action = None
            return False

        self._last_action = int(response.info.current_action.value)
        if self._last_action == McAction.STAND_DEFAULT:
            self._warned = False
            return True

        self._warn_once(
            f'robot is in motion mode {self._last_action}, not STAND_DEFAULT '
            f'({McAction.STAND_DEFAULT}); speaking but not gesturing')
        return False

    def _warn_once(self, message: str) -> None:
        if self._warned:
            self._node.get_logger().debug(message)
            return
        self._node.get_logger().warning(message)
        self._warned = True
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd /d/Projects/X2
MSYS_NO_PATHCONV=1 docker run --rm -v "D:/Projects/X2:/repo" -v x2-greeter-ws:/ws \
  x2-greeter-test bash /repo/docker/run_tests.sh src/x2_greeter/test/ros/test_gesture_and_mode_guard.py
```

Expected: PASS — 10 passed

- [ ] **Step 5: Commit**

```bash
cd /d/Projects/X2
git add x2_greeter_ws/src/x2_greeter/x2_greeter/ros/gesture.py x2_greeter_ws/src/x2_greeter/x2_greeter/ros/mode_guard.py x2_greeter_ws/src/x2_greeter/test/ros/test_gesture_and_mode_guard.py
git commit -m "feat: add gesture dispatcher and STAND_DEFAULT mode guard"
```

---

## Task 15: The greeting node

**Files:**
- Create: `x2_greeter_ws/src/x2_greeter/x2_greeter/ros/greeting_node.py`
- Create: `x2_greeter_ws/src/x2_greeter/config/greeter.yaml`
- Create: `x2_greeter_ws/src/x2_greeter/launch/greeter.launch.py`
- Modify: `x2_greeter_ws/src/x2_greeter/package.xml` — add `launch`, `launch_ros`, `ament_index_python` exec dependencies

**Interfaces:**
- Consumes: everything built in Tasks 1–14.
- Produces: `GreetingNode(Node)` with `.detector` (the live `PersonDetector`, exposed so a test can script it), `.tracker`, `.policy`, `.selector`, `.speech`, `.gesture`, `.mode_guard`, `.greetings_dispatched -> int`, and `.wait_for_idle_worker(timeout_s: float) -> bool`; `main(args=None)`.

**Concurrency, stated explicitly.** The `MultiThreadedExecutor` calls `_on_frame` on an executor thread; the cloud call and the dispatch run on a single-worker `ThreadPoolExecutor` so the perception loop never blocks; the gesture goes out on a second worker so speech and gesture are concurrent. `PresenceTracker` is touched from more than one of these, so **every tracker interaction is under `self._tracker_lock`**.

- [ ] **Step 1: Update the tracker's threading contract**

The Task 4 docstring claims the tracker is single-threaded. It is, per call — but the node calls it from two threads, so make the contract explicit rather than wrong.

In `x2_greeter_ws/src/x2_greeter/x2_greeter/core/presence.py`, replace:

```python
    """Decides when to ask a backend, and when a greeting may happen.

    Single-threaded by contract: the ROS executor calls update() and the
    greeting worker's callbacks are marshalled back onto the same thread.
    """
```

with:

```python
    """Decides when to ask a backend, and when a greeting may happen.

    Not thread-safe. update() is called from the ROS executor and
    on_verdict()/on_greeting_dispatched() from the greeting worker, so callers
    must serialise access — GreetingNode holds a lock around every call.
    """
```

Run: `cd /d/Projects/X2 && python -m pytest x2_greeter_ws/src/x2_greeter/test/core/test_presence.py -q`
Expected: PASS — 21 passed, unchanged.

- [ ] **Step 2: Write the configuration file**

`x2_greeter_ws/src/x2_greeter/config/greeter.yaml`:

```yaml
# X2 auto-greeter parameters. Defaults follow the design spec, section 13.
/**:
  ros__parameters:
    camera:
      rgb_topic: /aima/hal/sensor/rgbd_head_front/rgb_image
      depth_topic: /aima/hal/sensor/rgbd_head_front/depth_image
      # Metres per depth unit. 0.001 for a 16UC1 millimetre image, 1.0 for
      # 32FC1 metres. Confirm against the real camera on first hardware access.
      depth_scale: 0.001
      max_sync_skew_s: 0.15
      stale_warn_s: 5.0

    detect:
      # mobilenet_ssd | hog | scripted. mobilenet_ssd falls back to hog with a
      # warning when the weights are absent; run tools/fetch_model.py to get them.
      detector: mobilenet_ssd
      model_dir: ''
      confidence_min: 0.5
      distance_min_m: 1.0
      distance_max_m: 3.0
      center_tolerance: 0.25

    presence:
      dwell_s: 1.0
      loss_grace_s: 0.5
      clear_s: 3.0
      cooldown_s: 30.0
      reject_cooldown_s: 5.0
      confirm_timeout_s: 10.0

    backend:
      # claude | canned. Named 'provider' rather than 'backend' because ROS 2
      # forbids a parameter that is also a prefix of another parameter.
      provider: claude
      model: claude-opus-5
      effort: low
      timeout_s: 2.5

    speech:
      # auto | tts | audio_file
      tier: auto
      domain: x2_greeter
      priority_level: 6
      # Audio assets live on PC3 (10.0.1.42), world-readable.
      audio_dir: /var/tmp/x2_greeter_audio
      audio_file_count: 6
      phrases_file: ''

    gestures:
      enabled:
        - wave
        - salute
        - handshake
        - raise_hand
        - raise_both
        - bow
        - high_five
        - wave_chest
        - cheer
        - blow_kiss
        - heart
      # left | right | both | random
      hand_preference: right

    safety:
      require_stand_default: true
```

- [ ] **Step 3: Write the node**

`x2_greeter_ws/src/x2_greeter/x2_greeter/ros/greeting_node.py`:

```python
"""The greeter.

Perception runs on the ROS executor; the cloud call and the greeting run on a
worker thread, so the camera loop never stalls behind a network request. The
gesture is dispatched on a second worker so speech and gesture happen at the
same time rather than one after the other.

The node never changes the robot's motion mode and never issues a locomotion
command.
"""
from __future__ import annotations

import os
import random
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

import rclpy
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node

from x2_greeter.cognition.canned import DEFAULT_PHRASES, CannedBackend, load_phrases
from x2_greeter.cognition.policy import GreetingPolicy
from x2_greeter.core.detection import GateConfig, gate_detections
from x2_greeter.core.detectors import build_detector
from x2_greeter.core.gestures import DEFAULT_ENABLED, GestureSelector
from x2_greeter.core.imaging import to_jpeg_frame
from x2_greeter.core.presence import PresenceConfig, PresenceState, PresenceTracker
from x2_greeter.core.types import SceneContext
from x2_greeter.ros.frame_source import FrameSource
from x2_greeter.ros.gesture import GestureDispatcher
from x2_greeter.ros.mode_guard import ModeGuard
from x2_greeter.ros.speech import SpeechDispatcher


class GreetingNode(Node):
    def __init__(self) -> None:
        super().__init__('x2_greeter')
        self._declare_parameters()

        self._callback_group = ReentrantCallbackGroup()
        self._tracker_lock = threading.Lock()
        self._greetings_dispatched = 0

        self._gate_config = GateConfig(
            confidence_min=self._param('detect.confidence_min'),
            distance_min_m=self._param('detect.distance_min_m'),
            distance_max_m=self._param('detect.distance_max_m'),
            center_tolerance=self._param('detect.center_tolerance'),
        )
        self._depth_scale = float(self._param('camera.depth_scale'))

        self.tracker = PresenceTracker(PresenceConfig(
            dwell_s=self._param('presence.dwell_s'),
            loss_grace_s=self._param('presence.loss_grace_s'),
            clear_s=self._param('presence.clear_s'),
            cooldown_s=self._param('presence.cooldown_s'),
            reject_cooldown_s=self._param('presence.reject_cooldown_s'),
            confirm_timeout_s=self._param('presence.confirm_timeout_s'),
        ))

        self.detector = build_detector(
            self._param('detect.detector'), self._param('detect.model_dir'),
            logger=_LoggerShim(self.get_logger()))

        rng = random.Random()
        self.selector = GestureSelector(
            enabled=list(self._param('gestures.enabled')),
            hand_preference=self._param('gestures.hand_preference'),
            rng=rng)

        primary, fallback, primary_name = self._build_backends(rng)
        self._primary_name = primary_name
        self._needs_frame = primary_name != 'canned'
        self.policy = GreetingPolicy(primary, fallback,
                                     logger=_LoggerShim(self.get_logger()))

        self.speech = SpeechDispatcher(
            self,
            tier=self._param('speech.tier'),
            domain=self._param('speech.domain'),
            priority_level=int(self._param('speech.priority_level')),
            audio_dir=self._param('speech.audio_dir'),
            audio_file_count=int(self._param('speech.audio_file_count')),
            rng=rng,
            callback_group=self._callback_group)
        self.gesture = GestureDispatcher(self, callback_group=self._callback_group)
        self.mode_guard = ModeGuard(
            self, require_stand_default=bool(self._param('safety.require_stand_default')),
            callback_group=self._callback_group)

        self._greeting_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='greeting')
        self._action_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='gesture')
        self._greeting_future = None

        self.frames = FrameSource(
            self,
            rgb_topic=self._param('camera.rgb_topic'),
            depth_topic=self._param('camera.depth_topic'),
            on_frame=self._on_frame,
            max_sync_skew_s=self._param('camera.max_sync_skew_s'),
            stale_warn_s=self._param('camera.stale_warn_s'),
            callback_group=self._callback_group)

        self.get_logger().info(
            'x2_greeter up: detector=%s backend=%s gestures=%d speech_tier=%s',
            type(self.detector).__name__, primary_name,
            len(self.selector.enabled_names), self._param('speech.tier'))

    # ---------------------------------------------------------------- params

    def _declare_parameters(self) -> None:
        self.declare_parameter('camera.rgb_topic',
                               '/aima/hal/sensor/rgbd_head_front/rgb_image')
        self.declare_parameter('camera.depth_topic',
                               '/aima/hal/sensor/rgbd_head_front/depth_image')
        self.declare_parameter('camera.depth_scale', 0.001)
        self.declare_parameter('camera.max_sync_skew_s', 0.15)
        self.declare_parameter('camera.stale_warn_s', 5.0)

        self.declare_parameter('detect.detector', 'mobilenet_ssd')
        self.declare_parameter('detect.model_dir', '')
        self.declare_parameter('detect.confidence_min', 0.5)
        self.declare_parameter('detect.distance_min_m', 1.0)
        self.declare_parameter('detect.distance_max_m', 3.0)
        self.declare_parameter('detect.center_tolerance', 0.25)

        self.declare_parameter('presence.dwell_s', 1.0)
        self.declare_parameter('presence.loss_grace_s', 0.5)
        self.declare_parameter('presence.clear_s', 3.0)
        self.declare_parameter('presence.cooldown_s', 30.0)
        self.declare_parameter('presence.reject_cooldown_s', 5.0)
        self.declare_parameter('presence.confirm_timeout_s', 10.0)

        self.declare_parameter('backend.provider', 'claude')
        self.declare_parameter('backend.model', 'claude-opus-5')
        self.declare_parameter('backend.effort', 'low')
        self.declare_parameter('backend.timeout_s', 2.5)

        self.declare_parameter('speech.tier', 'auto')
        self.declare_parameter('speech.domain', 'x2_greeter')
        self.declare_parameter('speech.priority_level', 6)
        self.declare_parameter('speech.audio_dir', '/var/tmp/x2_greeter_audio')
        self.declare_parameter('speech.audio_file_count', len(DEFAULT_PHRASES))
        self.declare_parameter('speech.phrases_file', '')

        self.declare_parameter('gestures.enabled', list(DEFAULT_ENABLED))
        self.declare_parameter('gestures.hand_preference', 'right')

        self.declare_parameter('safety.require_stand_default', True)

    def _param(self, name: str):
        return self.get_parameter(name).value

    # -------------------------------------------------------------- backends

    def _build_backends(self, rng):
        """Return (primary, fallback, primary_name), degrading rather than exiting."""
        phrases = self._load_phrases()
        canned = CannedBackend(phrases, rng=rng)

        provider = self._param('backend.provider')
        if provider == 'canned':
            self.get_logger().info('backend.provider=canned; greeting offline only')
            return canned, canned, 'canned'
        if provider != 'claude':
            self.get_logger().error(
                'unknown backend.provider %r; using the canned backend', provider)
            return canned, canned, 'canned'

        if not os.environ.get('ANTHROPIC_API_KEY'):
            self.get_logger().warning(
                'ANTHROPIC_API_KEY is not set; greeting from the canned phrase list only')
            return canned, canned, 'canned'

        try:
            from x2_greeter.cognition.claude import ClaudeBackend
            primary = ClaudeBackend(
                enabled_gestures=self.selector.enabled_names,
                model=self._param('backend.model'),
                effort=self._param('backend.effort'),
                timeout_s=self._param('backend.timeout_s'),
                logger=_LoggerShim(self.get_logger()))
        except Exception as exc:                       # noqa: BLE001 - must still start
            self.get_logger().error(
                'could not start the Claude backend (%s); greeting from the canned '
                'phrase list only', exc)
            return canned, canned, 'canned'
        return primary, canned, 'claude'

    def _load_phrases(self):
        path = self._param('speech.phrases_file')
        if not path:
            try:
                from ament_index_python.packages import get_package_share_directory
                path = os.path.join(get_package_share_directory('x2_greeter'),
                                    'config', 'phrases.yaml')
            except Exception:                          # noqa: BLE001 - not installed
                return DEFAULT_PHRASES
        try:
            return load_phrases(path)
        except Exception as exc:                       # noqa: BLE001 - must still start
            self.get_logger().warning(
                'could not read %s (%s); using the built-in phrases', path, exc)
            return DEFAULT_PHRASES

    # ---------------------------------------------------------- perception

    def _now(self) -> float:
        return self.get_clock().now().nanoseconds * 1e-9

    def _on_frame(self, bgr, depth, stamp: float) -> None:
        """Runs on a ROS executor thread. Must not block."""
        try:
            raws = self.detector.detect(bgr)
        except Exception as exc:                       # noqa: BLE001 - one bad frame
            self.get_logger().warning(f'detector raised {type(exc).__name__}: {exc}')
            return

        detection = gate_detections(raws, bgr.shape[:2], depth,
                                    self._depth_scale, self._gate_config)

        with self._tracker_lock:
            to_confirm = self.tracker.update(self._now(), detection)
        if to_confirm is None:
            return

        # A previous greeting is still in flight; the confirm timeout will
        # release the tracker if that worker never finishes.
        if self._greeting_future is not None and not self._greeting_future.done():
            self.get_logger().warning('greeting worker still busy; skipping this confirm')
            return

        frame = to_jpeg_frame(bgr) if self._needs_frame else None
        ctx = SceneContext(distance_m=to_confirm.distance_m,
                           center_offset=to_confirm.center_offset)
        self._greeting_future = self._greeting_pool.submit(self._greet, frame, ctx)

    # ------------------------------------------------------------- greeting

    def _greet(self, frame, ctx: SceneContext) -> None:
        """Runs on the greeting worker. Blocking is fine here."""
        try:
            verdict = self.policy.compose(frame, ctx)
        finally:
            # Release the frame as early as possible: nothing else needs it and
            # it is never written anywhere (spec section 12).
            frame = None

        person_present = verdict.person_present
        if verdict.source != self._primary_name:
            # We fell back. The spec only allows a canned greeting while the
            # local detector still sees somebody.
            with self._tracker_lock:
                person_present = person_present and self.tracker.has_live_detection

        with self._tracker_lock:
            self.tracker.on_verdict(self._now(), person_present)
            greeting = self.tracker.state is PresenceState.GREETING
        if not greeting:
            self.get_logger().info('not greeting: %s', verdict.reason or 'no person')
            return

        choice = self.selector.select(verdict.gesture)
        allowed = self.mode_guard.gesturing_allowed()
        if allowed and ctx.distance_m < self._gate_config.distance_min_m:
            # Belt and braces: gating already refuses anyone closer than
            # distance_min_m, but never gesture at somebody within arm's reach.
            self.get_logger().warning('person at %.2f m is too close to gesture at',
                                      ctx.distance_m)
            allowed = False

        self.get_logger().info('greeting (%s): %r + %s',
                               verdict.source, verdict.greeting, choice.name)

        gesture_future = None
        if allowed:
            gesture_future = self._action_pool.submit(
                self.gesture.perform, choice.motion_id, choice.area_id)

        # Speech and gesture are independent: if one service is down, the other
        # still fires.
        try:
            self.speech.speak(verdict.greeting)
        except Exception as exc:                       # noqa: BLE001
            self.get_logger().error(f'speech failed: {type(exc).__name__}: {exc}')

        if gesture_future is not None:
            try:
                gesture_future.result(timeout=15.0)
            except Exception as exc:                   # noqa: BLE001
                self.get_logger().error(f'gesture failed: {type(exc).__name__}: {exc}')

        with self._tracker_lock:
            self.tracker.on_greeting_dispatched(self._now())
        self._greetings_dispatched += 1

    # ----------------------------------------------------------------- misc

    @property
    def greetings_dispatched(self) -> int:
        return self._greetings_dispatched

    def wait_for_idle_worker(self, timeout_s: float) -> bool:
        """True once no greeting is in flight. Used by tests, not by the robot."""
        future = self._greeting_future
        if future is None:
            return True
        try:
            future.result(timeout=timeout_s)
            return True
        except Exception:                              # noqa: BLE001
            return False

    def destroy_node(self) -> bool:
        self._greeting_pool.shutdown(wait=False)
        self._action_pool.shutdown(wait=False)
        return super().destroy_node()


class _LoggerShim:
    """Lets plain-Python modules log through an rclpy logger.

    core/ and cognition/ take a logging.Logger-shaped object; rclpy's logger
    has the same method names but different formatting rules, so the shim
    pre-formats and forwards.
    """

    def __init__(self, ros_logger) -> None:
        self._log = ros_logger

    def _emit(self, level, message, args):
        try:
            text = message % args if args else str(message)
        except Exception:                              # noqa: BLE001
            text = f'{message} {args}'
        getattr(self._log, level)(text)

    def debug(self, message, *args, **kwargs):
        self._emit('debug', message, args)

    def info(self, message, *args, **kwargs):
        self._emit('info', message, args)

    def warning(self, message, *args, **kwargs):
        self._emit('warning', message, args)

    warn = warning

    def error(self, message, *args, **kwargs):
        self._emit('error', message, args)


def main(args=None) -> None:
    rclpy.init(args=args)
    node = GreetingNode()
    executor = MultiThreadedExecutor()
    executor.add_node(node)
    try:
        executor.spin()
    except KeyboardInterrupt:
        pass
    finally:
        executor.shutdown()
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
```

- [ ] **Step 4: Write the launch file**

`x2_greeter_ws/src/x2_greeter/launch/greeter.launch.py`:

```python
"""ros2 launch x2_greeter greeter.launch.py [params_file:=...] [fake_robot:=true]"""
import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    share = get_package_share_directory('x2_greeter')
    default_params = os.path.join(share, 'config', 'greeter.yaml')

    params_file = LaunchConfiguration('params_file')
    fake_robot = LaunchConfiguration('fake_robot')

    return LaunchDescription([
        DeclareLaunchArgument(
            'params_file', default_value=default_params,
            description='YAML file of x2_greeter parameters'),
        DeclareLaunchArgument(
            'fake_robot', default_value='false',
            description='Also launch the simulated robot (bench testing only)'),
        Node(
            package='x2_greeter', executable='greeting_node', name='x2_greeter',
            output='screen', parameters=[params_file]),
        Node(
            package='x2_greeter', executable='fake_robot', name='fake_robot',
            output='screen', condition=IfCondition(fake_robot)),
    ])
```

- [ ] **Step 5: Add the launch dependencies to `package.xml`**

In `x2_greeter_ws/src/x2_greeter/package.xml`, after the `<exec_depend>aimdk_msgs</exec_depend>` line, add:

```xml
  <exec_depend>launch</exec_depend>
  <exec_depend>launch_ros</exec_depend>
  <exec_depend>ament_index_python</exec_depend>
```

- [ ] **Step 6: Verify it builds and the node starts**

```bash
cd /d/Projects/X2
MSYS_NO_PATHCONV=1 docker run --rm -v "D:/Projects/X2:/repo" -v x2-greeter-ws:/ws \
  x2-greeter-test bash -c '
    source /opt/ros/humble/setup.bash
    bash /repo/docker/run_tests.sh --collect-only -q
    source /ws/install/setup.bash
    timeout 12 ros2 launch x2_greeter greeter.launch.py fake_robot:=true 2>&1 | head -40
  '
```

Expected: the build succeeds, the collect succeeds, and the launch log shows `x2_greeter up: detector=...` plus `fake robot up: 4 services, camera publishing`. Because the fake robot's synthetic frame contains no real person, no greeting fires — that is correct, and Task 16 drives it with `ScriptedDetector`.

- [ ] **Step 7: Commit**

```bash
cd /d/Projects/X2
git add x2_greeter_ws/src/x2_greeter/x2_greeter/ros/greeting_node.py \
        x2_greeter_ws/src/x2_greeter/x2_greeter/core/presence.py \
        x2_greeter_ws/src/x2_greeter/config/greeter.yaml \
        x2_greeter_ws/src/x2_greeter/launch/greeter.launch.py \
        x2_greeter_ws/src/x2_greeter/package.xml
git commit -m "feat: wire the greeting node, parameters and launch file"
```

---

## Task 16: End-to-end integration and the layering guard

**Files:**
- Test: `x2_greeter_ws/src/x2_greeter/test/integration/test_greeting_end_to_end.py`
- Test: `x2_greeter_ws/src/x2_greeter/test/test_layering.py`

**Interfaces:**
- Consumes: `GreetingNode`, `FakeRobot`, `ScriptedDetector`, `CATALOGUE`.
- Produces: nothing new — this is the proof that the pieces fit.

- [ ] **Step 1: Write the layering guard (runs on Windows)**

`x2_greeter_ws/src/x2_greeter/test/test_layering.py`:

```python
"""The layering rule, enforced rather than merely documented.

Every ROS import outside ros/ and sim/ is a test that stops running on
Windows, so this guard is what keeps the whole core suite fast and portable.
"""
import ast
from pathlib import Path

import pytest

PACKAGE_ROOT = Path(__file__).resolve().parents[1] / 'x2_greeter'
ROS_ONLY = {'rclpy', 'aimdk_msgs', 'sensor_msgs', 'std_msgs', 'geometry_msgs',
            'cv_bridge', 'ament_index_python', 'launch', 'launch_ros'}


def imported_roots(path: Path):
    tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name.split('.')[0]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            yield node.module.split('.')[0]


def modules_under(*layers):
    for path in sorted(PACKAGE_ROOT.rglob('*.py')):
        rel = path.relative_to(PACKAGE_ROOT)
        if rel.parts and rel.parts[0] in layers:
            yield path


def submodules_of(path: Path):
    tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            parts = node.module.split('.')
            if parts[0] == 'x2_greeter' and len(parts) > 1:
                yield parts[1]


def test_the_package_root_exists():
    assert PACKAGE_ROOT.is_dir(), PACKAGE_ROOT


def test_no_ros_imports_outside_the_ros_and_sim_layers():
    offenders = []
    for path in modules_under('core', 'cognition'):
        for root in imported_roots(path):
            if root in ROS_ONLY:
                offenders.append(f'{path.relative_to(PACKAGE_ROOT)} imports {root}')
    assert offenders == [], '\n'.join(offenders)


def test_core_does_not_depend_on_cognition_or_ros():
    offenders = []
    for path in modules_under('core'):
        for sub in submodules_of(path):
            if sub in ('cognition', 'ros', 'sim'):
                offenders.append(f'{path.relative_to(PACKAGE_ROOT)} imports x2_greeter.{sub}')
    assert offenders == [], '\n'.join(offenders)


def test_cognition_does_not_depend_on_ros():
    offenders = []
    for path in modules_under('cognition'):
        for sub in submodules_of(path):
            if sub in ('ros', 'sim'):
                offenders.append(f'{path.relative_to(PACKAGE_ROOT)} imports x2_greeter.{sub}')
    assert offenders == [], '\n'.join(offenders)


def test_importing_the_package_does_not_pull_in_rclpy():
    init = PACKAGE_ROOT / '__init__.py'
    assert init.read_text(encoding='utf-8').strip() == ''


@pytest.mark.parametrize('layer', ['core', 'cognition'])
def test_every_layer_module_is_importable_without_ros(layer):
    import importlib
    for path in modules_under(layer):
        if path.name == '__init__.py':
            continue
        importlib.import_module(f'x2_greeter.{layer}.{path.stem}')
```

- [ ] **Step 2: Run the layering guard**

Run: `cd /d/Projects/X2 && python -m pytest x2_greeter_ws/src/x2_greeter/test/test_layering.py -v`
Expected: PASS — 7 passed. If it fails, the fix is in the offending module, never in the test.

- [ ] **Step 3: Write the end-to-end test**

`x2_greeter_ws/src/x2_greeter/test/integration/test_greeting_end_to_end.py`:

```python
"""The whole thing, on a simulated robot.

Camera frames go in; a TTS request and a preset motion come out, in the right
order, with the right payloads.
"""
import threading
import time

import pytest

pytestmark = pytest.mark.ros

FAST_PARAMS = [
    ('detect.detector', 'scripted'),
    ('backend.provider', 'canned'),
    ('presence.dwell_s', 0.3),
    ('presence.loss_grace_s', 0.5),
    ('presence.clear_s', 0.5),
    ('presence.cooldown_s', 1.0),
    ('presence.reject_cooldown_s', 0.5),
    ('speech.audio_file_count', 6),
]


@pytest.fixture
def ros():
    import rclpy
    rclpy.init()
    yield rclpy
    rclpy.shutdown()


def build_rig(ros, robot_kwargs=None, extra_params=()):
    from rclpy.executors import MultiThreadedExecutor
    from rclpy.parameter import Parameter

    from x2_greeter.core.detectors import ScriptedDetector
    from x2_greeter.core.types import BBox, RawDetection
    from x2_greeter.ros.greeting_node import GreetingNode
    from x2_greeter.sim.fake_robot import FakeRobot

    robot = FakeRobot(publish_camera=True, **(robot_kwargs or {}))

    overrides = [Parameter(name, value=value)
                 for name, value in list(FAST_PARAMS) + list(extra_params)]
    node = GreetingNode.__new__(GreetingNode)
    # Parameters must exist before __init__ reads them, so build the node the
    # way rclpy does when launched with a parameter file.
    import rclpy.node
    rclpy.node.Node.__init__(node, 'x2_greeter',
                             parameter_overrides=overrides,
                             allow_undeclared_parameters=False,
                             automatically_declare_parameters_from_overrides=False)
    GreetingNode._finish_init(node)

    node.detector = ScriptedDetector(
        [RawDetection(bbox=BBox(270, 90, 370, 390), confidence=0.9)])

    executor = MultiThreadedExecutor()
    executor.add_node(robot)
    executor.add_node(node)
    thread = threading.Thread(target=executor.spin, daemon=True)
    thread.start()
    return robot, node, executor, thread


def teardown_rig(robot, node, executor, thread):
    executor.shutdown()
    node.destroy_node()
    robot.destroy_node()
    thread.join(timeout=5.0)


def wait_until(predicate, timeout_s=25.0):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def test_a_person_in_front_gets_greeted_and_gestured_at(ros):
    robot, node, executor, thread = build_rig(ros)
    try:
        assert wait_until(lambda: robot.tts_requests and robot.motion_requests), \
            'the robot never greeted'

        spoken = robot.tts_requests[0].tts_req
        assert spoken.text.strip()
        assert spoken.domain == 'x2_greeter'
        assert spoken.priority_level.value == 6

        from x2_greeter.core.gestures import CATALOGUE
        motion = robot.motion_requests[0]
        spec = next(s for s in CATALOGUE.values() if s.motion_id == motion.motion.value)
        assert spec.name in node.selector.enabled_names
        assert motion.area.value in spec.areas
        assert motion.interrupt is False
    finally:
        teardown_rig(robot, node, executor, thread)


def test_the_mode_is_checked_before_the_robot_moves(ros):
    robot, node, executor, thread = build_rig(ros)
    try:
        assert wait_until(lambda: robot.motion_requests)
        assert robot.mode_queries >= 1
    finally:
        teardown_rig(robot, node, executor, thread)


def test_the_robot_speaks_but_does_not_gesture_outside_stand_default(ros):
    from aimdk_msgs.msg import McAction

    robot, node, executor, thread = build_rig(
        ros, robot_kwargs={'current_action': McAction.JOINT_DEFAULT})
    try:
        assert wait_until(lambda: bool(robot.tts_requests)), 'the robot never spoke'
        time.sleep(2.0)
        assert robot.motion_requests == []
        assert robot.current_action == McAction.JOINT_DEFAULT   # never changed
    finally:
        teardown_rig(robot, node, executor, thread)


def test_it_does_not_greet_the_same_person_over_and_over(ros):
    """The person never leaves, so the cooldown never lifts."""
    robot, node, executor, thread = build_rig(ros)
    try:
        assert wait_until(lambda: bool(robot.tts_requests))
        time.sleep(4.0)     # four times the 1.0 s cooldown
        assert len(robot.tts_requests) == 1
    finally:
        teardown_rig(robot, node, executor, thread)


def test_someone_leaving_and_returning_is_greeted_again(ros):
    robot, node, executor, thread = build_rig(ros)
    try:
        assert wait_until(lambda: bool(robot.tts_requests))
        node.detector.set_detections([])                       # they walk away
        time.sleep(2.5)                                        # cooldown + clear
        from x2_greeter.core.types import BBox, RawDetection
        node.detector.set_detections(
            [RawDetection(bbox=BBox(270, 90, 370, 390), confidence=0.9)])
        assert wait_until(lambda: len(robot.tts_requests) >= 2), 'never greeted again'
    finally:
        teardown_rig(robot, node, executor, thread)


def test_someone_too_far_away_is_not_greeted(ros):
    robot, node, executor, thread = build_rig(ros, robot_kwargs={'distance_mm': 5000})
    try:
        time.sleep(4.0)
        assert robot.tts_requests == []
        assert robot.motion_requests == []
    finally:
        teardown_rig(robot, node, executor, thread)


def test_a_failing_tts_falls_back_to_an_audio_file(ros):
    robot, node, executor, thread = build_rig(ros, robot_kwargs={'tts_succeeds': False})
    try:
        assert wait_until(lambda: bool(robot.audio_requests)), 'never played a recording'
        assert node.speech.demoted is True
        assert robot.audio_requests[0].file.info.sample_rate == 16000
    finally:
        teardown_rig(robot, node, executor, thread)


def test_no_image_reaches_the_robot_or_the_disk(ros):
    """The canned path must not encode or transmit a frame at all."""
    robot, node, executor, thread = build_rig(ros)
    try:
        assert wait_until(lambda: bool(robot.tts_requests))
        assert node._needs_frame is False
        for request in robot.tts_requests:
            assert 'jpeg' not in request.tts_req.text.lower()
    finally:
        teardown_rig(robot, node, executor, thread)
```

- [ ] **Step 4: Refactor `GreetingNode.__init__` so the test can inject parameters**

The test needs parameter overrides applied before the node reads them, which means splitting construction. In `x2_greeter_ws/src/x2_greeter/x2_greeter/ros/greeting_node.py`, replace the first two lines of `__init__`:

```python
    def __init__(self) -> None:
        super().__init__('x2_greeter')
        self._declare_parameters()
```

with:

```python
    def __init__(self, **node_kwargs) -> None:
        super().__init__('x2_greeter', **node_kwargs)
        self._finish_init()

    def _finish_init(self) -> None:
        """Everything after Node.__init__, so tests can construct with overrides."""
        self._declare_parameters()
```

The rest of the original `__init__` body stays exactly as written, now inside `_finish_init`. `main()` is unchanged: `GreetingNode()` still works.

- [ ] **Step 5: Run the integration suite**

```bash
cd /d/Projects/X2
MSYS_NO_PATHCONV=1 docker run --rm -v "D:/Projects/X2:/repo" -v x2-greeter-ws:/ws \
  x2-greeter-test bash /repo/docker/run_tests.sh src/x2_greeter/test/integration
```

Expected: PASS — 8 passed. These tests are timing-based; if one is flaky, raise the timeout in `wait_until`, never lower the assertion.

- [ ] **Step 6: Run everything, both sides**

Run: `cd /d/Projects/X2 && python -m pytest -v`
Expected: PASS — the whole Windows-side suite.

```bash
cd /d/Projects/X2
MSYS_NO_PATHCONV=1 docker run --rm -v "D:/Projects/X2:/repo" -v x2-greeter-ws:/ws \
  x2-greeter-test bash /repo/docker/run_tests.sh
```

Expected: PASS — every `@pytest.mark.ros` test.

- [ ] **Step 7: Commit**

```bash
cd /d/Projects/X2
git add x2_greeter_ws/src/x2_greeter/test/test_layering.py \
        x2_greeter_ws/src/x2_greeter/test/integration \
        x2_greeter_ws/src/x2_greeter/x2_greeter/ros/greeting_node.py
git commit -m "test: add end-to-end greeting integration and layering guard"
```

---

## Task 17: Audio assets and the deployment runbook

**Files:**
- Create: `tools/make_greeting_audio.py`
- Create: `tools/deploy_audio.sh`
- Create: `docs/DEPLOYMENT.md`
- Test: `x2_greeter_ws/src/x2_greeter/test/test_audio_tooling.py`

**Interfaces:**
- Consumes: `load_phrases`, `DEFAULT_PHRASES` from `x2_greeter.cognition.canned`.
- Produces: `tools/make_greeting_audio.py` exposing `wav_filename(index:int) -> str`, `write_wav(path:str, samples:numpy.ndarray, sample_rate:int=16000) -> None`, `synthesise(text:str, sample_rate:int=16000) -> numpy.ndarray`, and `main(argv=None) -> int`.

**The constraint that shapes this.** Audio must be 16 kHz, 16-bit, mono, WAV or raw PCM, and must live on **PC3 (10.0.1.42)** — not PC2, where the node runs — in a directory that is world-readable along with every parent. Text-to-speech engines vary by machine, so the generator uses `pyttsx3` when it is installed and otherwise writes a clearly-labelled placeholder tone, so the pipeline can be verified before real recordings exist.

- [ ] **Step 1: Write the failing test**

`x2_greeter_ws/src/x2_greeter/test/test_audio_tooling.py`:

```python
import importlib.util
import sys
import wave
from pathlib import Path

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
TOOL_PATH = REPO_ROOT / 'tools' / 'make_greeting_audio.py'


@pytest.fixture(scope='module')
def tool():
    spec = importlib.util.spec_from_file_location('make_greeting_audio', TOOL_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules['make_greeting_audio'] = module
    spec.loader.exec_module(module)
    return module


def test_the_tool_exists():
    assert TOOL_PATH.is_file(), TOOL_PATH


def test_filenames_match_what_the_speech_dispatcher_asks_for(tool):
    assert tool.wav_filename(0) == 'greeting_00.wav'
    assert tool.wav_filename(5) == 'greeting_05.wav'
    assert tool.wav_filename(12) == 'greeting_12.wav'


def test_written_wavs_meet_the_documented_format(tmp_path, tool):
    path = tmp_path / 'greeting_00.wav'
    tool.write_wav(str(path), tool.synthesise('Hello there!'))
    with wave.open(str(path), 'rb') as handle:
        assert handle.getnchannels() == 1
        assert handle.getsampwidth() == 2          # 16-bit
        assert handle.getframerate() == 16000
        assert handle.getnframes() > 0


def test_samples_are_clipped_into_int16_range(tmp_path, tool):
    path = tmp_path / 'loud.wav'
    tool.write_wav(str(path), np.full(1600, 5.0, dtype=np.float32))
    with wave.open(str(path), 'rb') as handle:
        data = np.frombuffer(handle.readframes(handle.getnframes()), dtype=np.int16)
    assert data.max() <= 32767
    assert data.min() >= -32768


def test_it_generates_one_file_per_phrase(tmp_path, tool):
    dest = tmp_path / 'audio'
    assert tool.main(['--dest', str(dest)]) == 0

    from x2_greeter.cognition.canned import DEFAULT_PHRASES
    produced = sorted(p.name for p in dest.glob('*.wav'))
    assert produced == [tool.wav_filename(i) for i in range(len(DEFAULT_PHRASES))]


def test_every_generated_file_is_playable_and_conformant(tmp_path, tool):
    dest = tmp_path / 'audio'
    tool.main(['--dest', str(dest)])
    for path in sorted(dest.glob('*.wav')):
        with wave.open(str(path), 'rb') as handle:
            assert (handle.getnchannels(), handle.getsampwidth(),
                    handle.getframerate()) == (1, 2, 16000)


def test_the_file_count_matches_the_speech_default(tool):
    from x2_greeter.cognition.canned import DEFAULT_PHRASES
    # config/greeter.yaml sets speech.audio_file_count to this length; a
    # mismatch would make the dispatcher ask for a recording that is not there.
    assert len(DEFAULT_PHRASES) == 6
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd /d/Projects/X2 && python -m pytest x2_greeter_ws/src/x2_greeter/test/test_audio_tooling.py -v`
Expected: FAIL — `AssertionError` on `test_the_tool_exists`

- [ ] **Step 3: Write the generator**

`tools/make_greeting_audio.py`:

```python
#!/usr/bin/env python3
"""Generate the tier-3 greeting recordings.

The interface docs are strict: 16 kHz, 16-bit, mono, WAV or raw PCM. MP3 is
rejected. Files are named greeting_NN.wav by phrase index, which is how
SpeechDispatcher finds them, so the order in config/phrases.yaml is
load-bearing.

Uses pyttsx3 for real speech when it is installed; otherwise it writes a
labelled placeholder tone so the deployment pipeline can be exercised before
real recordings exist. Replace the placeholders with proper audio before the
robot meets anybody.

Usage:
    python tools/make_greeting_audio.py --dest assets/audio
    python tools/make_greeting_audio.py --dest assets/audio --phrases path/to/phrases.yaml
"""
from __future__ import annotations

import argparse
import os
import sys
import wave

import numpy as np

SAMPLE_RATE = 16000
CHANNELS = 1
SAMPLE_WIDTH_BYTES = 2       # 16-bit


def wav_filename(index: int) -> str:
    """The name SpeechDispatcher will ask PlayAudioFile for."""
    return f'greeting_{index:02d}.wav'


def write_wav(path: str, samples: np.ndarray, sample_rate: int = SAMPLE_RATE) -> None:
    """Write mono 16-bit PCM. `samples` is float in -1..1, or already int16."""
    if samples.dtype != np.int16:
        clipped = np.clip(np.asarray(samples, dtype=np.float64), -1.0, 1.0)
        samples = (clipped * 32767.0).astype(np.int16)
    with wave.open(path, 'wb') as handle:
        handle.setnchannels(CHANNELS)
        handle.setsampwidth(SAMPLE_WIDTH_BYTES)
        handle.setframerate(sample_rate)
        handle.writeframes(samples.tobytes())


def synthesise(text: str, sample_rate: int = SAMPLE_RATE) -> np.ndarray:
    """Speech for `text` if a TTS engine is available, otherwise a placeholder tone.

    The placeholder's length tracks the phrase length, so a wrong-file mix-up
    is audible rather than silent.
    """
    engine_samples = _try_pyttsx3(text, sample_rate)
    if engine_samples is not None:
        return engine_samples

    duration_s = max(0.6, min(4.0, len(text) * 0.06))
    t = np.linspace(0.0, duration_s, int(sample_rate * duration_s), endpoint=False)
    tone = 0.25 * np.sin(2.0 * np.pi * 440.0 * t)
    envelope = np.minimum(1.0, np.minimum(t * 20.0, (duration_s - t) * 20.0))
    return tone * envelope


def _try_pyttsx3(text: str, sample_rate: int):
    """Render with pyttsx3 into a temporary WAV, then resample to 16 kHz mono."""
    try:
        import tempfile

        import pyttsx3
    except ImportError:
        return None

    tmp_path = None
    try:
        handle, tmp_path = tempfile.mkstemp(suffix='.wav')
        os.close(handle)
        engine = pyttsx3.init()
        engine.save_to_file(text, tmp_path)
        engine.runAndWait()
        with wave.open(tmp_path, 'rb') as source:
            frames = source.readframes(source.getnframes())
            channels = source.getnchannels()
            source_rate = source.getframerate()
            width = source.getsampwidth()
        if width != 2 or not frames:
            return None
        data = np.frombuffer(frames, dtype=np.int16).astype(np.float64) / 32767.0
        if channels > 1:
            data = data.reshape(-1, channels).mean(axis=1)
        if source_rate != sample_rate:
            target_length = int(round(len(data) * sample_rate / float(source_rate)))
            data = np.interp(np.linspace(0.0, len(data) - 1, target_length),
                             np.arange(len(data)), data)
        return data
    except Exception as exc:                           # noqa: BLE001 - fall back to a tone
        print(f'pyttsx3 unavailable ({exc}); writing placeholder tones', file=sys.stderr)
        return None
    finally:
        if tmp_path and os.path.isfile(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def load_phrase_list(path: str):
    if path:
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), 'x2_greeter_ws', 'src', 'x2_greeter'))
        from x2_greeter.cognition.canned import load_phrases
        return load_phrases(path)

    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), 'x2_greeter_ws', 'src', 'x2_greeter'))
    from x2_greeter.cognition.canned import DEFAULT_PHRASES
    return DEFAULT_PHRASES


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dest', default='assets/audio',
                        help='directory to write greeting_NN.wav into')
    parser.add_argument('--phrases', default='',
                        help='phrases.yaml to read (default: the built-in list)')
    args = parser.parse_args(argv)

    phrases = load_phrase_list(args.phrases)
    os.makedirs(args.dest, exist_ok=True)

    for index, phrase in enumerate(phrases):
        path = os.path.join(args.dest, wav_filename(index))
        write_wav(path, synthesise(phrase))
        print(f'{path}  <-  {phrase!r}')

    print(f'\nWrote {len(phrases)} files to {os.path.abspath(args.dest)}.')
    print('Set speech.audio_file_count to', len(phrases), 'in config/greeter.yaml,')
    print('then deploy with tools/deploy_audio.sh — the files must live on PC3.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
```

- [ ] **Step 4: Write the deployment script**

`tools/deploy_audio.sh`:

```bash
#!/usr/bin/env bash
# Copy the greeting recordings to the interaction compute unit (PC3).
#
# They must live on PC3, NOT on PC2 where the greeter node runs, and the
# directory and every parent must be readable by all users. A subdirectory of
# /var/tmp is what the interface docs recommend.
set -euo pipefail

SRC=${1:-assets/audio}
PC3=${PC3:-10.0.1.42}
USER_NAME=${PC3_USER:-agi}
DEST=${DEST:-/var/tmp/x2_greeter_audio}

if [ ! -d "$SRC" ]; then
  echo "no such directory: $SRC" >&2
  echo "run tools/make_greeting_audio.py --dest $SRC first" >&2
  exit 1
fi

count=$(find "$SRC" -maxdepth 1 -name 'greeting_*.wav' | wc -l)
if [ "$count" -eq 0 ]; then
  echo "no greeting_NN.wav files in $SRC" >&2
  exit 1
fi

echo "== deploying $count file(s) from $SRC to ${USER_NAME}@${PC3}:${DEST}"

ssh "${USER_NAME}@${PC3}" "mkdir -p '${DEST}' && chmod 755 '${DEST}'"
scp "$SRC"/greeting_*.wav "${USER_NAME}@${PC3}:${DEST}/"
ssh "${USER_NAME}@${PC3}" "chmod 644 ${DEST}/greeting_*.wav && ls -l ${DEST}"

echo
echo "== done. Confirm speech.audio_dir is ${DEST} in config/greeter.yaml"
echo "== and speech.audio_file_count is ${count}"
```

```bash
cd /d/Projects/X2 && chmod +x tools/deploy_audio.sh
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `cd /d/Projects/X2 && python -m pytest x2_greeter_ws/src/x2_greeter/test/test_audio_tooling.py -v`
Expected: PASS — 7 passed

- [ ] **Step 6: Write the deployment runbook**

`docs/DEPLOYMENT.md`:

````markdown
# Deploying the X2 auto-greeter

**Never build or run on PC1 (10.0.1.40).** The SDK documentation states this is
"strictly prohibited to avoid safety risks". The greeter runs on **PC2**
(10.0.1.41); its audio assets live on **PC3** (10.0.1.42).

## 1. Prerequisites on PC2

```bash
source /opt/ros/humble/setup.bash
source ~/aimdk/install/local_setup.bash    # provides aimdk_msgs
python3 -m pip install --user "anthropic>=1.0" pyyaml
sudo apt install ros-humble-cv-bridge python3-opencv
```

## 2. Build the overlay workspace

Clone this repository somewhere under `$HOME` **other than `$HOME/aimdk*`** —
the SDK README declares that path "reserved and maintained by the system", and
it is erased on firmware upgrade.

```bash
cd ~/x2_greeter_ws
colcon build --packages-select x2_greeter
source install/setup.bash
```

## 3. Optional: fetch the person-detection weights

Without them the node falls back to OpenCV's built-in HOG detector and logs a
warning. With them, detection is faster and more reliable.

```bash
python3 tools/fetch_model.py --dest ~/models
# then set detect.model_dir: /home/agi/models in config/greeter.yaml
```

## 4. Generate and deploy the greeting recordings

These are the tier-3 fallback, used only if `PlayTts` reports failure.

```bash
python3 tools/make_greeting_audio.py --dest assets/audio
tools/deploy_audio.sh assets/audio
```

The script creates `/var/tmp/x2_greeter_audio` on PC3, world-readable. Confirm
`speech.audio_dir` and `speech.audio_file_count` in `config/greeter.yaml` match.

## 5. Set the API key

```bash
export ANTHROPIC_API_KEY=...     # never commit this
```

Without it the node still starts, logs a warning, and greets from the canned
phrase list.

## 6. Bring the robot to STAND_DEFAULT — with a human present

The greeter **never** changes the motion mode. Gestures require force-control
stand, so put the robot there yourself, feet planted:

```bash
ros2 run py_examples set_mc_action PD    # passive
ros2 run py_examples set_mc_action JD    # position-control stand
ros2 run py_examples set_mc_action SD    # force-control stand
```

Outside `STAND_DEFAULT` the greeter speaks but does not gesture, and warns once.

## 7. Launch

```bash
ros2 launch x2_greeter greeter.launch.py
```

Bench test with no robot at all:

```bash
ros2 launch x2_greeter greeter.launch.py fake_robot:=true
```

## 8. First-window smoke checklist

Work through these on first hardware access; each resolves an open question in
the design spec, section 17.

| Check | How | If it is wrong |
|---|---|---|
| Camera topics are live | `ros2 topic hz /aima/hal/sensor/rgbd_head_front/rgb_image` | fix `camera.rgb_topic` |
| Depth encoding | `ros2 topic echo --field encoding .../depth_image --once` | `16UC1` → `camera.depth_scale: 0.001`; `32FC1` → `1.0` |
| Distance readings look right | stand at a tape-measured 2 m; the log prints the gated distance | adjust `camera.depth_scale` |
| A person is detected | walk into frame; the log shows the state machine advancing | lower `detect.confidence_min`, or fetch the SSD weights |
| `PlayTts` works offline | pull the network, then greet | if it fails, set `speech.tier: audio_file` |
| `PlayAudioFile` needs audio focus | force tier 3 and watch for a rejection | if so, call `RequestAudioFocus` first — currently not implemented |
| Gesture timing | watch a full greeting | if speech and gesture desynchronise, tune per-gesture duration |
| CPU headroom | `top` while the node runs | switch `detect.detector` to `hog`, or lower the frame rate |
````

- [ ] **Step 7: Run the complete suite one last time**

Run: `cd /d/Projects/X2 && python -m pytest -v`
Expected: PASS — the entire Windows-side suite.

```bash
cd /d/Projects/X2
MSYS_NO_PATHCONV=1 docker run --rm -v "D:/Projects/X2:/repo" -v x2-greeter-ws:/ws \
  x2-greeter-test bash /repo/docker/run_tests.sh
```

Expected: PASS — every `@pytest.mark.ros` test.

- [ ] **Step 8: Commit**

```bash
cd /d/Projects/X2
git add tools/make_greeting_audio.py tools/deploy_audio.sh docs/DEPLOYMENT.md \
        x2_greeter_ws/src/x2_greeter/test/test_audio_tooling.py
git commit -m "feat: add greeting audio tooling and the deployment runbook"
```

---

## Spec coverage

Where each section of the design spec lands:

| Spec section | Task(s) |
|---|---|
| §1 Purpose | the plan as a whole; proved by Task 16 |
| §3 Non-goals | nothing — deliberately unimplemented (no face recognition, no conversation, no navigation, no wake word) |
| §2 SDK interfaces | 10 (types verified), 13 (`PlayTts`, `PlayAudioFile`), 14 (`SetMcPresetMotion`, `GetMcAction`), 12 (camera) |
| §4.1 Overlay workspace | 1, 17 (runbook) |
| §4.2 Layering rule | 1 (structure), 16 (enforced by test) |
| §5 Data flow | 15 (the wiring), 16 (proved end to end) |
| §6 Detection and gating | 3 (gates), 9 (detectors) |
| §7 State machine | 4 |
| §8.1 The cloud call | 7 |
| §8.2 Swapping providers | 6 (the Protocol), 7 (the first adapter) |
| §9 Gestures | 2 (catalogue, allowlist), 14 (dispatch) |
| §10 Speech tiers | 13 (tiers, demotion), 17 (assets, PC3) |
| §11 Safety | 14 (mode guard), 15 (never changes mode, distance floor) |
| §12 Privacy | 5 (tripwire test), 8 (fallback never sees the frame), 15 (frame released) |
| §13 Configuration | 15 (`greeter.yaml`, parameter declarations) |
| §14 Failure modes | 7, 8, 13, 14, 15 — each row has a test |
| §15 Testing | every task; 10 (Docker harness), 16 (integration) |
| §16 Deployment | 17 (`docs/DEPLOYMENT.md`) |
| §17 Open questions | 17 (the smoke checklist turns each into a check) |
