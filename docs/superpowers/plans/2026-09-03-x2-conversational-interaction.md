# X2 Conversational Interaction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the one-shot auto-greeter into a multi-turn, venue-aware conversational partner: the robot sees who is in front of it and what kind of place it is standing in, hears them through the vendor's `only_voice` agent pipeline, transcribes locally on PC2, replies with Claude, and answers with speech plus a gesture and a face emoji — for up to 8 turns before closing and cooling down.

**Architecture:** Phase 1's hexagonal layering is unchanged and non-negotiable. `core/` grows the pure session machine (`conversation.py`), the scene reader (`scene.py`), the venue profile (`venue.py`), the language policy (`language.py`), the gaze geometry (`gaze.py`) and the emoji catalogue (`faces.py`); `cognition/` grows a `Transcriber` port with a `faster-whisper` adapter and a `DialogueBackend` port with a Claude adapter; `ros/` grows one adapter per vendor surface (`audio_source`, `agent_mode`, `face`, `head`, `env_camera`) and one orchestrating node (`conversation_node.py`). Everything that needs hardware we do not yet have — head motion, emoji on the real face screen, the unverified gestures — ships behind a disabled-by-default flag so the first hardware window is spent measuring, not debugging.

**Tech Stack:** ROS 2 Humble / Ubuntu 22.04 / Python 3.10 / ament_python; AimDK `v1.0.0-ga424add` (`aimdk_msgs`); `faster-whisper` (`small`, int8, PC2-local, no network); `anthropic` Python SDK (`claude-opus-5`); OpenCV + `cv_bridge`; PyYAML; pytest with the `ros` marker.

**Spec:** `docs/superpowers/specs/2026-09-03-x2-conversational-interaction-design.md`

## Global Constraints

Every task's requirements implicitly include this section. Values are copied verbatim from the spec.

- **No image is ever written to disk, anywhere, ever** — not a cache, not a debug artefact, not a log line. Image bytes never appear in any log message at any level. `test/test_privacy.py` is the tripwire and must stay green.
- **Phase 2 extends the same rule to audio.** Raw PCM lives in memory for the length of one utterance and is dropped when the turn ends. No `.wav` is written, no PCM bytes are logged, no transcript is persisted.
- **Zero `rclpy` / `aimdk_msgs` / `sensor_msgs` / `std_msgs` / `geometry_msgs` / `cv_bridge` / `ament_index_python` / `launch` / `launch_ros` imports outside `x2_greeter/ros/` and `x2_greeter/sim/`.** `test/test_layering.py` enforces it. `core/` must not import `cognition/`, `ros/` or `sim/`; `cognition/` must not import `ros/` or `sim/`.
- **The node never changes the robot's motion mode and never issues a locomotion command.** Phase 2 adds no locomotion. Head yaw is the only new motion surface and it ships `enabled: false`.
- **Gesture safety interlocks are unchanged and load-bearing:** refuse to gesture when the mode is not `STAND_DEFAULT`, when the subject is closer than `GESTURE_MIN_DISTANCE_M = 1.0`, or on a stale/absent distance reading — **but still speak in every refusal case.**
- **MC input source** stays `x2_greeter`, priority `30`, inside the documented SDK band 20–39. The remote controller arbitrates at 80 and keeps its override.
- **`ANTHROPIC_API_KEY` comes from the environment only** and is never committed. **No test makes a real API call. No test loads the Whisper model.**
- **`only_voice` is a deployment step, never a runtime write.** The node verifies the mode indirectly and refuses to start a session if it cannot. There is no `GetAgentProperties` service in the SDK — read-back is impossible, so indirect verification is the only option.
- **Child mode:** never ask a child for personal information, never promise anything, and **never tell a child to come closer, follow, or reach out** — the 1.0 m arm's-reach interlock means an approaching child silently disables the gesture the robot just invited.
- **Languages this phase: English (default) and Chinese only.** `ALLOWED_LANGUAGES = ('en', 'zh')`. Malay is deferred.
- **Zero new third-party vendors.** Vendor `PlayTts` for speech, local `faster-whisper` for ASR, Claude for dialogue.
- **Every `(motion_id, area_id)` pair in the gesture catalogue must have a named vendor source** — the `McPresetMotion` enum or the interface-doc preset-motion table. Never a description of either.
- **Head yaw is clamped to ±0.262 rad (±15°)** against the vendor's documented ±20°, and returns to 0.0 on every exit path.
- Build and run on **PC2 (10.0.1.41)** only. Building or running on **PC1 (10.0.1.40) is strictly prohibited.** Never write under `$HOME/aimdk*`.
- A human must be within reach of the stop control for any run that can produce motion.

---

## File Structure

```
x2_greeter_ws/src/x2_greeter/
  x2_greeter/
    core/
      language.py       NEW  allowed languages, normalisation, the follow-the-person switch policy
      venue.py          NEW  VenueProfile dataclass, YAML loader, prompt rendering
      scene.py          NEW  multi-person gating, stature/child heuristic, addressing choice
      gaze.py           NEW  yaw clamp, bbox->yaw, sweep waypoints, group drift (pure geometry)
      faces.py          NEW  emoji catalogue pinned to the PlayEmoji enum
      conversation.py   NEW  the session state machine (pure, no I/O, no clock)
      gestures.py       MOD  heart correction; GestureSpec gains source + hw_verified
      detection.py      ---  untouched (spec section 2)
    cognition/
      transcriber.py    NEW  Transcriber port + faster-whisper adapter
      dialogue.py       NEW  Turn, DialogueBackend port, turn validation, response schema
      claude.py         MOD  add ClaudeDialogueBackend beside the Phase 1 ClaudeBackend
    ros/
      audio_source.py   NEW  ProcessedAudioOutput -> utterance buffer
      agent_mode.py     NEW  indirect only_voice verification via /interaction/tts_status
      env_camera.py     NEW  compressed wide-FOV base frame
      face.py           NEW  PlayEmoji dispatcher
      head.py           NEW  JointCommandArray yaw dispatcher + JointStateArray feedback
      conversation_node.py NEW  the orchestrating node
    sim/
      fakes.py          NEW  pure-Python fakes for every port (no ROS)
      fake_robot.py     MOD  serve PlayEmoji, head joint topics, publish ProcessedAudioOutput
  config/
    conversation.yaml   NEW  Phase 2 parameters; merges over greeter.yaml
    venues/clothing_store.yaml  NEW
    venues/mall_atrium.yaml     NEW
  launch/
    conversation.launch.py NEW
  test/
    core/test_language.py, test_venue.py, test_scene.py, test_gaze.py,
      test_faces.py, test_conversation.py                       NEW
    cognition/test_transcriber.py, test_dialogue.py,
      test_claude_dialogue.py                                   NEW
    ros/test_audio_source.py, test_agent_mode.py, test_face.py,
      test_head.py, test_env_camera.py                          NEW
    ros/test_gesture_catalogue_ids.py                           MOD (pin widens to (motion_id, area_id) + source)
    integration/test_conversation_session.py                    NEW
    test_privacy.py         MOD (audio tripwire)
    test_shipped_config.py  MOD (conversation.yaml assertions)
  setup.py                MOD (entry point + data_files for config/venues, launch)
  package.xml             MOD (no new ROS deps expected; verify)
```

Sequencing rationale: tasks 1–8 are pure Python and need no robot, no ROS and no network — they are the whole session brain and can be written and reviewed on the host. Tasks 9–12 are one thin ROS adapter each. Task 13 is the shipped configuration and its tripwire. Task 14 wires the node. Task 15 makes the simulator able to run a whole conversation end to end.

---

## Task 1: Language policy and venue profiles

**Files:**
- Create: `x2_greeter_ws/src/x2_greeter/x2_greeter/core/language.py`
- Create: `x2_greeter_ws/src/x2_greeter/x2_greeter/core/venue.py`
- Create: `x2_greeter_ws/src/x2_greeter/config/venues/clothing_store.yaml`
- Create: `x2_greeter_ws/src/x2_greeter/config/venues/mall_atrium.yaml`
- Test: `x2_greeter_ws/src/x2_greeter/test/core/test_language.py`
- Test: `x2_greeter_ws/src/x2_greeter/test/core/test_venue.py`

**Interfaces:**
- Consumes: nothing (first task).
- Produces:
  - `core.language.ALLOWED_LANGUAGES: tuple[str, ...] == ('en', 'zh')`
  - `core.language.DEFAULT_LANGUAGE: str == 'en'`
  - `core.language.normalise_language(raw: Optional[str]) -> Optional[str]`
  - `core.language.LanguagePolicy(default: str = 'en', switch_confidence: float = 0.7)` with `.next_language(current: str, detected: Optional[str], confidence: float) -> str`
  - `core.venue.VenueProfile` frozen dataclass: `kind: str`, `role: str`, `language_default: str`, `opening: Dict[str, str]`, `facts: Tuple[str, ...]`, `topics_encouraged: Tuple[str, ...]`, `topics_forbidden: Tuple[str, ...]`, `deflect_to_human: str`, and `.to_prompt() -> str`, `.opening_for(language: str) -> str`
  - `core.venue.VenueError(Exception)`
  - `core.venue.parse_venue(doc: dict) -> VenueProfile`
  - `core.venue.load_venue(path) -> VenueProfile`

`language.py` exists as its own module so `venue.py` can validate `language_default` and `conversation.py` can own the switch policy without either importing the other. It is the smallest module in the package and deliberately so.

- [ ] **Step 1: Write the failing language tests**

Create `test/core/test_language.py`:

```python
"""The language state is explicit and follows the person (spec section 10).

Malay is deferred to a later phase; a Malay tag must be treated exactly like
any other unsupported tag -- ignored, not crashed on, and never switched to.
"""
import pytest

from x2_greeter.core.language import (
    ALLOWED_LANGUAGES, DEFAULT_LANGUAGE, LanguagePolicy, normalise_language)


def test_this_phase_ships_english_and_chinese_only():
    assert ALLOWED_LANGUAGES == ('en', 'zh')
    assert DEFAULT_LANGUAGE == 'en'


@pytest.mark.parametrize('raw, expected', [
    ('en', 'en'), ('EN', 'en'), ('en-US', 'en'), ('en_GB', 'en'),
    ('zh', 'zh'), ('zh-CN', 'zh'), ('ZH_Hans', 'zh'), (' zh ', 'zh'),
])
def test_regional_tags_normalise_to_the_base_language(raw, expected):
    assert normalise_language(raw) == expected


@pytest.mark.parametrize('raw', ['ms', 'ms-MY', 'ja', '', None, 'unknown', 'e'])
def test_unsupported_or_missing_tags_normalise_to_none(raw):
    assert normalise_language(raw) is None


def test_the_language_holds_when_detection_is_not_confident():
    policy = LanguagePolicy(switch_confidence=0.7)
    assert policy.next_language('en', 'zh', 0.69) == 'en'


def test_the_language_follows_the_person_when_detection_is_confident():
    policy = LanguagePolicy(switch_confidence=0.7)
    assert policy.next_language('en', 'zh', 0.7) == 'zh'
    assert policy.next_language('zh', 'en', 0.95) == 'en'


def test_an_unsupported_detection_never_switches_however_confident():
    # Malay is deferred: a confident 'ms' must not move the state off English.
    policy = LanguagePolicy()
    assert policy.next_language('en', 'ms', 1.0) == 'en'
    assert policy.next_language('en', None, 1.0) == 'en'


def test_an_invalid_current_language_falls_back_to_the_default():
    policy = LanguagePolicy(default='en')
    assert policy.next_language('ms', None, 1.0) == 'en'
```

- [ ] **Step 2: Run it and watch it fail**

Run: `python -m pytest x2_greeter_ws/src/x2_greeter/test/core/test_language.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'x2_greeter.core.language'`

- [ ] **Step 3: Write `core/language.py`**

```python
"""Which language the robot is speaking, and when that is allowed to change.

The language is explicit session state, not something re-derived per turn: a
Chinese speaker who says one English word mid-sentence should not flip the
robot, and a low-confidence guess from the transcriber should never move it
at all. This phase ships English and Chinese; Malay is deferred, so a Malay
tag is treated like any other unsupported tag -- recognised as "not one of
ours" and ignored, never crashed on.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

ALLOWED_LANGUAGES = ('en', 'zh')
DEFAULT_LANGUAGE = 'en'


def normalise_language(raw: Optional[str]) -> Optional[str]:
    """Reduce a BCP-47-ish tag to a supported base language, or None.

    Whisper reports 'zh', browsers and vendor configs report 'zh-CN', and the
    LLM will occasionally answer 'en_US'. All three mean the same thing here.
    """
    if not raw:
        return None
    base = str(raw).strip().lower().replace('_', '-').split('-')[0]
    return base if base in ALLOWED_LANGUAGES else None


@dataclass(frozen=True)
class LanguagePolicy:
    """Decides whether a detected language replaces the session language."""

    default: str = DEFAULT_LANGUAGE
    switch_confidence: float = 0.7

    def next_language(self, current: str, detected: Optional[str],
                      confidence: float) -> str:
        current = normalise_language(current) or self.default
        candidate = normalise_language(detected)
        if candidate is None or candidate == current:
            return current
        if confidence < self.switch_confidence:
            return current
        return candidate
```

- [ ] **Step 4: Run the language tests**

Run: `python -m pytest x2_greeter_ws/src/x2_greeter/test/core/test_language.py -v`
Expected: PASS (11 tests)

- [ ] **Step 5: Write the failing venue tests**

Create `test/core/test_venue.py`:

```python
"""A venue profile is the authority over what the robot may assert.

The camera can tell the robot there are clothes on a rail. It cannot tell it
whether the shop takes returns, where the fitting rooms are, or whether the
sale ends on Sunday. Those come from the profile or they are not said at all
-- so a malformed profile must refuse to load rather than silently produce a
robot with no facts and no forbidden topics.
"""
from pathlib import Path

import pytest

from x2_greeter.core.venue import VenueError, VenueProfile, load_venue, parse_venue

VENUE_DIR = Path(__file__).resolve().parents[2] / 'config' / 'venues'

MINIMAL = {
    'venue': {
        'kind': 'clothing_store',
        'role': 'a greeter standing near the entrance of a clothing store',
        'language_default': 'en',
        'opening': {'en': 'Hello! Welcome in.', 'zh': '你好！欢迎光临。'},
        'facts': ['The fitting rooms are at the back on the left.'],
        'topics_encouraged': ['what the customer is looking for'],
        'topics_forbidden': ['prices', 'stock levels'],
        'deflect_to_human': 'A staff member can help you with that.',
    }
}


def test_a_minimal_profile_parses():
    profile = parse_venue(MINIMAL)
    assert isinstance(profile, VenueProfile)
    assert profile.kind == 'clothing_store'
    assert profile.facts == ('The fitting rooms are at the back on the left.',)
    assert profile.topics_forbidden == ('prices', 'stock levels')


def test_the_opening_line_is_available_per_language():
    profile = parse_venue(MINIMAL)
    assert profile.opening_for('en') == 'Hello! Welcome in.'
    assert profile.opening_for('zh') == '你好！欢迎光临。'
    # An unsupported language falls back to the profile default, never crashes.
    assert profile.opening_for('ms') == 'Hello! Welcome in.'


@pytest.mark.parametrize('missing', [
    'kind', 'role', 'opening', 'facts', 'topics_forbidden', 'deflect_to_human'])
def test_a_profile_missing_a_required_key_refuses_to_load(missing):
    doc = {'venue': dict(MINIMAL['venue'])}
    del doc['venue'][missing]
    with pytest.raises(VenueError) as exc:
        parse_venue(doc)
    assert missing in str(exc.value)


def test_a_profile_with_no_opening_for_the_default_language_refuses_to_load():
    doc = {'venue': dict(MINIMAL['venue'], opening={'zh': '你好'})}
    with pytest.raises(VenueError):
        parse_venue(doc)


def test_a_profile_with_an_unsupported_default_language_refuses_to_load():
    doc = {'venue': dict(MINIMAL['venue'], language_default='ms')}
    with pytest.raises(VenueError):
        parse_venue(doc)


def test_a_profile_with_no_facts_refuses_to_load():
    # An empty facts list is not a venue with nothing to say; it is a profile
    # someone forgot to fill in, and it would produce a robot that invents.
    doc = {'venue': dict(MINIMAL['venue'], facts=[])}
    with pytest.raises(VenueError):
        parse_venue(doc)


def test_the_prompt_carries_the_facts_and_the_forbidden_topics():
    prompt = parse_venue(MINIMAL).to_prompt()
    assert 'The fitting rooms are at the back on the left.' in prompt
    assert 'prices' in prompt
    assert 'stock levels' in prompt
    assert 'A staff member can help you with that.' in prompt


@pytest.mark.parametrize('name', ['clothing_store', 'mall_atrium'])
def test_the_shipped_venue_profiles_load(name):
    profile = load_venue(VENUE_DIR / f'{name}.yaml')
    assert profile.kind == name
    assert profile.facts
    assert profile.opening_for('en')
    assert profile.opening_for('zh')


def test_loading_a_missing_profile_raises_venue_error():
    with pytest.raises(VenueError):
        load_venue(VENUE_DIR / 'no_such_venue.yaml')
```

- [ ] **Step 6: Run it and watch it fail**

Run: `python -m pytest x2_greeter_ws/src/x2_greeter/test/core/test_venue.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'x2_greeter.core.venue'`

- [ ] **Step 7: Write `core/venue.py`**

```python
"""What kind of place the robot is standing in, and what it may say about it.

The camera answers "what is in front of me". It cannot answer "does this shop
take returns" -- so everything the robot asserts as fact about the venue comes
from a profile a human wrote, and everything else is deflected to a human.
The profile is the authority: where it and the frame disagree, the profile
wins.

A malformed profile raises rather than degrading, because the degraded form of
this module is a robot that answers venue questions from the language model's
imagination.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Mapping, Tuple

import yaml

from x2_greeter.core.language import ALLOWED_LANGUAGES, normalise_language

_REQUIRED = ('kind', 'role', 'opening', 'facts', 'topics_forbidden',
             'deflect_to_human')


class VenueError(Exception):
    """A venue profile is missing, unreadable, or incomplete."""


@dataclass(frozen=True)
class VenueProfile:
    kind: str
    role: str
    language_default: str
    opening: Dict[str, str]
    facts: Tuple[str, ...]
    topics_encouraged: Tuple[str, ...]
    topics_forbidden: Tuple[str, ...]
    deflect_to_human: str

    def opening_for(self, language: str) -> str:
        lang = normalise_language(language) or self.language_default
        return self.opening.get(lang) or self.opening[self.language_default]

    def to_prompt(self) -> str:
        """Render the profile as the venue block of the dialogue system prompt."""
        lines = [
            f'You are {self.role}.',
            '',
            'Facts you may state. These are the ONLY things you may assert about '
            'this place; anything else about the venue you do not know:',
        ]
        lines += [f'- {fact}' for fact in self.facts]
        if self.topics_encouraged:
            lines += ['', 'Good things to talk about:']
            lines += [f'- {topic}' for topic in self.topics_encouraged]
        lines += ['', 'Never discuss these, even if asked directly:']
        lines += [f'- {topic}' for topic in self.topics_forbidden]
        lines += ['',
                  'When asked something you cannot answer from the facts above, '
                  f'say: "{self.deflect_to_human}"']
        return '\n'.join(lines)


def parse_venue(doc: Mapping) -> VenueProfile:
    if not isinstance(doc, Mapping) or not isinstance(doc.get('venue'), Mapping):
        raise VenueError("venue profile must have a top-level 'venue' mapping")
    venue = doc['venue']

    missing = [key for key in _REQUIRED if key not in venue]
    if missing:
        raise VenueError(f'venue profile is missing required key(s): '
                         f'{", ".join(missing)}')

    language_default = normalise_language(venue.get('language_default', 'en'))
    if language_default is None:
        raise VenueError(
            f'venue.language_default must be one of {ALLOWED_LANGUAGES}, got '
            f'{venue.get("language_default")!r}')

    opening = venue['opening']
    if not isinstance(opening, Mapping) or not opening.get(language_default):
        raise VenueError(
            f'venue.opening must provide a line for the default language '
            f'{language_default!r}')

    facts = tuple(str(fact) for fact in venue['facts'])
    if not facts:
        raise VenueError('venue.facts must list at least one fact: an empty '
                         'list produces a robot that invents them')

    forbidden = tuple(str(topic) for topic in venue['topics_forbidden'])
    if not forbidden:
        raise VenueError('venue.topics_forbidden must list at least one topic')

    return VenueProfile(
        kind=str(venue['kind']),
        role=str(venue['role']),
        language_default=language_default,
        opening={str(k): str(v) for k, v in opening.items()},
        facts=facts,
        topics_encouraged=tuple(str(t) for t in venue.get('topics_encouraged', ())),
        topics_forbidden=forbidden,
        deflect_to_human=str(venue['deflect_to_human']),
    )


def load_venue(path) -> VenueProfile:
    path = Path(path)
    try:
        with open(path, 'r', encoding='utf-8') as handle:
            doc = yaml.safe_load(handle)
    except OSError as exc:
        raise VenueError(f'could not read venue profile {path}: {exc}') from exc
    except yaml.YAMLError as exc:
        raise VenueError(f'venue profile {path} is not valid YAML: {exc}') from exc
    return parse_venue(doc)
```

- [ ] **Step 8: Write the two shipped venue profiles**

Create `config/venues/clothing_store.yaml`:

```yaml
# The authority over what the robot may assert in a clothing store.
# Everything here is said by a human who works in the shop, not inferred from
# a camera frame. Edit this file, not the prompt, to change what the robot
# knows.
venue:
  kind: clothing_store
  role: >-
    a friendly robot greeter standing near the entrance of a clothing store,
    welcoming customers and pointing them in the right direction
  language_default: en
  opening:
    en: "Hi there! Welcome in — have a look around."
    zh: "你好！欢迎光临，随便看看。"
  facts:
    - The fitting rooms are at the back of the store, on the left.
    - Staff at the counter can check sizes and stock for you.
    - There is a seating area near the fitting rooms.
  topics_encouraged:
    - what the customer is shopping for today
    - colours and styles they like
    - whether they are shopping for themselves or for someone else
  topics_forbidden:
    - prices and discounts
    - stock levels and availability
    - returns, exchanges and refunds
    - anything about a specific person's body, size, or weight
  deflect_to_human: >-
    I'm not sure about that one — a staff member at the counter can help you.
```

Create `config/venues/mall_atrium.yaml`:

```yaml
# A public mall atrium: more children, more passers-by, fewer answerable
# questions. The forbidden list is longer than the clothing store's on
# purpose -- the robot is standing in public with no staff beside it.
venue:
  kind: mall_atrium
  role: >-
    a friendly robot standing in the open atrium of a shopping mall, saying
    hello to people passing by and chatting briefly with anyone who stops
  language_default: en
  opening:
    en: "Hello! Nice to see you."
    zh: "你好呀！很高兴见到你。"
  facts:
    - I am a robot called X2, and I am here to say hello to people.
    - The information desk can help with directions around the mall.
  topics_encouraged:
    - how the person's day is going
    - what brought them to the mall today
    - what the robot is and what it can do
  topics_forbidden:
    - directions to a specific shop, floor, or facility
    - opening hours, events and promotions
    - anything about a specific person's appearance, body, or age
    - where a child's parent or guardian is
  deflect_to_human: >-
    I don't know that one — the information desk will be able to tell you.
```

- [ ] **Step 9: Run the venue tests**

Run: `python -m pytest x2_greeter_ws/src/x2_greeter/test/core/test_venue.py -v`
Expected: PASS (16 tests)

- [ ] **Step 10: Run the layering guard**

Run: `python -m pytest x2_greeter_ws/src/x2_greeter/test/test_layering.py -v`
Expected: PASS — the two new `core/` modules import cleanly without ROS and depend on nothing outside `core/`.

- [ ] **Step 11: Commit**

```bash
git add x2_greeter_ws/src/x2_greeter/x2_greeter/core/language.py \
        x2_greeter_ws/src/x2_greeter/x2_greeter/core/venue.py \
        x2_greeter_ws/src/x2_greeter/config/venues/ \
        x2_greeter_ws/src/x2_greeter/test/core/test_language.py \
        x2_greeter_ws/src/x2_greeter/test/core/test_venue.py
git commit -m "feat(core): venue profiles and the explicit language policy"
```

---


## Task 2: Head gaze geometry (pure)

**Files:**
- Create: `x2_greeter_ws/src/x2_greeter/x2_greeter/core/gaze.py`
- Test: `x2_greeter_ws/src/x2_greeter/test/core/test_gaze.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `core.gaze.MAX_YAW_RAD: float == 0.262` (our clamp, ±15°)
  - `core.gaze.VENDOR_LIMIT_RAD: float == 0.349` (the vendor's ±20°, for the assertion that ours is inside it)
  - `core.gaze.HEAD_YAW_JOINT: str == 'head_yaw'`, `core.gaze.HEAD_PITCH_JOINT: str == 'head_pitch'`
  - `core.gaze.HEAD_HFOV_RAD: float == 1.6406` (94°, the head RGB-D colour horizontal FOV)
  - `core.gaze.clamp_yaw(yaw: float, max_yaw_rad: float = MAX_YAW_RAD) -> float`
  - `core.gaze.yaw_for(bbox_cx: float, image_width: int, hfov_rad: float = HEAD_HFOV_RAD, max_yaw_rad: float = MAX_YAW_RAD, yaw_sign: int = 1) -> float`
  - `core.gaze.SweepStep(NamedTuple)`: `yaw: float`, `t_s: float`, `capture: bool`
  - `core.gaze.sweep_waypoints(leg_s: float = 1.0, hold_s: float = 0.4, rate_hz: float = 20.0, max_yaw_rad: float = MAX_YAW_RAD) -> Tuple[SweepStep, ...]`
  - `core.gaze.group_drift(t_s: float, period_s: float = 12.0, max_yaw_rad: float = MAX_YAW_RAD) -> float`

Everything here is arithmetic. No ROS, no clock, no state — which is what makes the ±15° clamp testable at all. `ros/head.py` (Task 12) is the only caller and adds nothing but message plumbing.

**The sweep is not a way to see more.** 94° plus 40° of sweep is 134°, which is less than the 156° a single stereo frame already covers. The sweep exists so a person watching the robot can see it consider the room, and so the head camera can capture two off-axis close-range frames that the wide stereo lens renders too small to read. Do not widen it in the belief that it extends coverage.

- [ ] **Step 1: Write the failing test**

Create `test/core/test_gaze.py`:

```python
"""Head yaw geometry: the clamp is the safety property, the rest is framing.

The vendor documents +/-20 degrees of head yaw and says pitch is unavailable.
We clamp to +/-15 so that an arithmetic error, a bad FOV constant or a
malformed bbox cannot walk the joint into its own hard stop. Every test here
exists to keep that margin, or to keep the sweep from being mistaken for a
way to see a wider scene.
"""
import pytest

from x2_greeter.core.gaze import (
    HEAD_HFOV_RAD, HEAD_PITCH_JOINT, HEAD_YAW_JOINT, MAX_YAW_RAD,
    VENDOR_LIMIT_RAD, clamp_yaw, group_drift, sweep_waypoints, yaw_for)


def test_our_clamp_sits_inside_the_vendor_limit():
    assert MAX_YAW_RAD == pytest.approx(0.262, abs=1e-3)       # 15 degrees
    assert VENDOR_LIMIT_RAD == pytest.approx(0.349, abs=1e-3)  # 20 degrees
    assert MAX_YAW_RAD < VENDOR_LIMIT_RAD, (
        'the whole point of the clamp is the margin: if it ever reaches the '
        'vendor limit, a rounding error is enough to hit the hard stop')


def test_the_joint_names_are_the_vendor_names():
    assert HEAD_YAW_JOINT == 'head_yaw'
    assert HEAD_PITCH_JOINT == 'head_pitch'


@pytest.mark.parametrize('raw, expected', [
    (0.0, 0.0), (0.1, 0.1), (-0.1, -0.1),
    (10.0, MAX_YAW_RAD), (-10.0, -MAX_YAW_RAD),
    (MAX_YAW_RAD, MAX_YAW_RAD), (-MAX_YAW_RAD, -MAX_YAW_RAD),
])
def test_clamp_yaw_never_returns_more_than_the_limit(raw, expected):
    assert clamp_yaw(raw) == pytest.approx(expected)


@pytest.mark.parametrize('bad', [float('nan'), float('inf'), float('-inf')])
def test_clamp_yaw_turns_a_non_finite_value_into_centre(bad):
    # A NaN yaw sent to the controller is not a small command, it is an
    # undefined one. Centre is the only safe reading of "I do not know".
    assert clamp_yaw(bad) == 0.0


def test_a_centred_person_needs_no_yaw():
    assert yaw_for(320.0, 640) == pytest.approx(0.0)


def test_a_person_at_the_edge_of_frame_yaws_towards_half_the_fov():
    # Full deflection would be hfov/2 = 0.820 rad, which the clamp cuts to
    # MAX_YAW_RAD -- the clamp binds long before the frame edge does.
    assert yaw_for(640.0, 640) == pytest.approx(MAX_YAW_RAD)
    assert yaw_for(0.0, 640) == pytest.approx(-MAX_YAW_RAD)


def test_a_person_slightly_off_centre_yaws_proportionally():
    # 25% right of centre -> 0.25 * hfov/2 = 0.205 rad, inside the clamp.
    expected = 0.25 * HEAD_HFOV_RAD / 2.0
    assert expected < MAX_YAW_RAD
    assert yaw_for(400.0, 640) == pytest.approx(expected, abs=1e-4)


def test_the_yaw_sign_is_configurable_because_nobody_has_measured_it_yet():
    # Whether +yaw turns the head left or right is an open hardware question
    # (spec section 18). It is a config value with a default, never a guess
    # baked into the arithmetic.
    assert yaw_for(400.0, 640, yaw_sign=-1) == pytest.approx(
        -yaw_for(400.0, 640, yaw_sign=1))


@pytest.mark.parametrize('width', [0, -640])
def test_a_degenerate_image_width_yields_centre(width):
    assert yaw_for(100.0, width) == 0.0


def test_the_sweep_starts_and_ends_at_centre():
    steps = sweep_waypoints()
    assert steps[0].yaw == pytest.approx(0.0)
    assert steps[-1].yaw == pytest.approx(0.0)


def test_every_sweep_waypoint_is_inside_the_clamp():
    for step in sweep_waypoints():
        assert abs(step.yaw) <= MAX_YAW_RAD + 1e-9, step


def test_the_sweep_visits_both_extremes():
    yaws = [step.yaw for step in sweep_waypoints()]
    assert min(yaws) == pytest.approx(-MAX_YAW_RAD)
    assert max(yaws) == pytest.approx(MAX_YAW_RAD)


def test_the_sweep_captures_exactly_twice_and_only_while_held():
    captures = [step for step in sweep_waypoints() if step.capture]
    assert len(captures) == 2, (
        'two off-axis frames, one per side: a capture per waypoint would '
        'mean a dozen head-camera frames per sweep for no extra information')
    assert {round(step.yaw, 3) for step in captures} == {
        round(-MAX_YAW_RAD, 3), round(MAX_YAW_RAD, 3)}


def test_the_sweep_timestamps_increase_at_the_command_rate():
    steps = sweep_waypoints(rate_hz=20.0)
    deltas = [b.t_s - a.t_s for a, b in zip(steps, steps[1:])]
    assert all(delta == pytest.approx(0.05, abs=1e-6) for delta in deltas)


def test_a_faster_command_rate_produces_more_waypoints_over_the_same_span():
    slow = sweep_waypoints(rate_hz=10.0)
    fast = sweep_waypoints(rate_hz=20.0)
    assert len(fast) > len(slow)
    assert fast[-1].t_s == pytest.approx(slow[-1].t_s, abs=0.11)


def test_the_group_drift_is_a_slow_bounded_wander():
    for t_s in [0.0, 1.0, 3.0, 6.0, 9.0, 12.0, 100.0]:
        assert abs(group_drift(t_s)) <= MAX_YAW_RAD + 1e-9
    assert group_drift(0.0) == pytest.approx(0.0)
    assert group_drift(3.0, period_s=12.0) > 0.0
    assert group_drift(9.0, period_s=12.0) < 0.0


def test_the_group_drift_is_a_pure_function_of_time():
    assert group_drift(4.2) == group_drift(4.2)
```

- [ ] **Step 2: Run it and watch it fail**

Run: `python -m pytest x2_greeter_ws/src/x2_greeter/test/core/test_gaze.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'x2_greeter.core.gaze'`

- [ ] **Step 3: Write `core/gaze.py`**

```python
"""Where to point the head, expressed as arithmetic and nothing else.

The vendor exposes head yaw over /aima/hal/joint/head/command and documents a
+/-20 degree range; pitch is listed but unavailable. We clamp to +/-15 so the
margin absorbs a bad FOV constant, a malformed bounding box or a rounding
error before the joint reaches its hard stop.

Two behaviours live here. yaw_for() points the head at one person. The sweep
turns the head slowly left, holds, right, holds, and returns to centre. The
sweep is NOT a way to see more of the room: 94 degrees of head FOV plus 40
degrees of travel is 134 degrees, less than the 156 the fixed stereo camera
already covers in a single frame. It exists so a person can watch the robot
take the room in, and so the head camera can capture two off-axis close-range
frames that the wide lens renders too small to read.
"""
from __future__ import annotations

import math
from typing import NamedTuple, Tuple

MAX_YAW_RAD = 0.262        # 15 degrees, our clamp
VENDOR_LIMIT_RAD = 0.349   # 20 degrees, the vendor's documented range
HEAD_HFOV_RAD = 1.6406     # 94 degrees, head RGB-D colour horizontal FOV

HEAD_YAW_JOINT = 'head_yaw'
HEAD_PITCH_JOINT = 'head_pitch'


def clamp_yaw(yaw: float, max_yaw_rad: float = MAX_YAW_RAD) -> float:
    """Bound a yaw command, treating a non-finite value as "no idea": centre."""
    value = float(yaw)
    if not math.isfinite(value):
        return 0.0
    limit = abs(float(max_yaw_rad))
    return max(-limit, min(limit, value))


def yaw_for(bbox_cx: float, image_width: int,
            hfov_rad: float = HEAD_HFOV_RAD,
            max_yaw_rad: float = MAX_YAW_RAD,
            yaw_sign: int = 1) -> float:
    """Yaw that brings a bounding box centred at bbox_cx to the middle of frame.

    yaw_sign exists because nobody has measured whether a positive command
    turns the head left or right (spec section 18). It is a configuration
    value with a default, not an assumption compiled into the geometry.
    """
    width = int(image_width)
    if width <= 0:
        return 0.0
    half = width / 2.0
    offset = (float(bbox_cx) - half) / half    # -1 at left edge, +1 at right
    if not math.isfinite(offset):
        return 0.0
    return clamp_yaw(int(yaw_sign) * offset * (float(hfov_rad) / 2.0), max_yaw_rad)


class SweepStep(NamedTuple):
    """One 20 Hz command in the sweep. capture is True only at a hold point."""

    yaw: float
    t_s: float
    capture: bool


def sweep_waypoints(leg_s: float = 1.0, hold_s: float = 0.4,
                    rate_hz: float = 20.0,
                    max_yaw_rad: float = MAX_YAW_RAD) -> Tuple[SweepStep, ...]:
    """centre -> left -> hold -> right -> hold -> centre, sampled at rate_hz.

    Returned as a full command trajectory rather than four target angles: the
    joint interface takes positions, so somebody has to do the interpolation,
    and doing it here means the +/-15 clamp is proved over every intermediate
    sample, not only at the corners.
    """
    rate_hz = float(rate_hz)
    if rate_hz <= 0.0:
        raise ValueError('rate_hz must be positive')
    dt = 1.0 / rate_hz
    limit = abs(float(max_yaw_rad))

    steps = [SweepStep(yaw=0.0, t_s=0.0, capture=False)]

    def _ramp(start: float, end: float, seconds: float) -> None:
        count = max(1, int(round(float(seconds) * rate_hz)))
        for i in range(1, count + 1):
            yaw = start + (end - start) * (i / count)
            steps.append(SweepStep(yaw=clamp_yaw(yaw, limit),
                                   t_s=steps[-1].t_s + dt, capture=False))

    def _hold(yaw: float, seconds: float) -> None:
        count = max(1, int(round(float(seconds) * rate_hz)))
        for i in range(1, count + 1):
            # Capture on the last sample of the hold: by then the head has had
            # the whole hold to settle, so the frame is not motion-blurred.
            steps.append(SweepStep(yaw=clamp_yaw(yaw, limit),
                                   t_s=steps[-1].t_s + dt,
                                   capture=(i == count)))

    _ramp(0.0, -limit, leg_s)
    _hold(-limit, hold_s)
    _ramp(-limit, limit, 2.0 * leg_s)
    _hold(limit, hold_s)
    _ramp(limit, 0.0, leg_s)
    return tuple(steps)


def group_drift(t_s: float, period_s: float = 12.0,
                max_yaw_rad: float = MAX_YAW_RAD) -> float:
    """A slow sinusoidal wander for GROUP addressing, at 60% of the clamp.

    Locking onto one face while addressing a group reads as staring; holding
    dead centre reads as a screensaver. This is neither, and it is bounded by
    the same clamp as everything else.
    """
    period_s = float(period_s)
    if period_s <= 0.0:
        return 0.0
    amplitude = 0.6 * abs(float(max_yaw_rad))
    return clamp_yaw(amplitude * math.sin(2.0 * math.pi * float(t_s) / period_s),
                     max_yaw_rad)
```

- [ ] **Step 4: Run the tests**

Run: `python -m pytest x2_greeter_ws/src/x2_greeter/test/core/test_gaze.py -v`
Expected: PASS (27 tests)

- [ ] **Step 5: Prove the clamp tests are load-bearing (mutation check)**

Mutation A — edit `core/gaze.py` and change `MAX_YAW_RAD = 0.262` to `MAX_YAW_RAD = 0.5`.

Run: `python -m pytest x2_greeter_ws/src/x2_greeter/test/core/test_gaze.py -v`
Expected: FAIL — at minimum `test_our_clamp_sits_inside_the_vendor_limit` and `test_a_person_slightly_off_centre_yaws_proportionally`.

Mutation B — restore that, then change the body of `clamp_yaw` to `return float(yaw)`.

Run: `python -m pytest x2_greeter_ws/src/x2_greeter/test/core/test_gaze.py -v`
Expected: FAIL — `test_clamp_yaw_never_returns_more_than_the_limit`, `test_clamp_yaw_turns_a_non_finite_value_into_centre`, `test_every_sweep_waypoint_is_inside_the_clamp`.

Revert both mutations (`git checkout -- x2_greeter_ws/src/x2_greeter/x2_greeter/core/gaze.py`) and re-run to confirm green. If either mutation left the suite green, the test is decorative — fix the test before moving on.

- [ ] **Step 6: Commit**

```bash
git add x2_greeter_ws/src/x2_greeter/x2_greeter/core/gaze.py \
        x2_greeter_ws/src/x2_greeter/test/core/test_gaze.py
git commit -m "feat(core): head gaze geometry with a 15-degree clamp inside the vendor 20"
```

---

## Task 3: The two catalogues — gesture correction and the emoji set

**Files:**
- Modify: `x2_greeter_ws/src/x2_greeter/x2_greeter/core/gestures.py` (the `GestureSpec` dataclass, the `heart` entry, one new entry)
- Create: `x2_greeter_ws/src/x2_greeter/x2_greeter/core/faces.py`
- Test: `x2_greeter_ws/src/x2_greeter/test/core/test_faces.py`
- Test: `x2_greeter_ws/src/x2_greeter/test/ros/test_face_catalogue_ids.py`
- Modify: `x2_greeter_ws/src/x2_greeter/test/ros/test_gesture_catalogue_ids.py` (the pin widens; one test is replaced)
- Reference (read-only): `sdk/aimdk-aarch64-a424add7-artifacts/docs/cn/_build/html/en/dev/Interface/control_mod/preset_motion.html`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `core.gestures.GestureSpec` gains two fields: `source: str` (one of `'enum'`, `'doc_table'`) and `hw_verified: bool`
  - `core.gestures.VALID_SOURCES: frozenset == frozenset({'enum', 'doc_table'})`
  - `core.gestures.CATALOGUE['heart'] == GestureSpec('heart', 1007, (AREA_BOTH,), handed=False, source='doc_table', hw_verified=False)`
  - `core.gestures.CATALOGUE['heart_overhead']` — 3004, `(AREA_WHOLE_BODY,)`, `handed=False`, `source='enum'`, `hw_verified=False`, **not** in `DEFAULT_ENABLED`
  - `core.faces.EmojiSpec` frozen dataclass: `name: str`, `emotion_id: int`, `source: str`, `hw_verified: bool`
  - `core.faces.CATALOGUE: Dict[str, EmojiSpec]`, `core.faces.DEFAULT_ENABLED: Tuple[str, ...]`
  - `core.faces.THINKING = 'thinking'`, `core.faces.MODE_ONCE = 1`, `core.faces.MODE_LOOP = 2`
  - `core.faces.EmojiSelector(enabled: Sequence[str])` with `.enabled_names -> tuple`, `.select(requested: Optional[str]) -> Optional[EmojiSpec]`, `.thinking -> Optional[EmojiSpec]`

**Why these two ship together:** they are the same shape of work — a name-to-vendor-id table, an allowlist the model chooses from by name, and a test that pins every id against a vendor source. Reviewing them as one diff is what makes the shared rule visible.

**Two corrections this task lands, both worth understanding before you touch the file:**

1. **`heart` is 1007 with area 3, not 3004.** Both motions are real and they are different gestures: 3004 (`INTERACTION_SWEATHEART` in the enum) is the overhead heart made with the arms above the head; 1007 in the interface doc's preset-motion table is the two-handed heart made at the chest. Phase 1 shipped 1007 with `handed=True`, which — with the shipped `hand_preference: right` — resolved to **area 2**, one hand. The controller refused it, and the refusal was recorded as "1007 is not real". It is real; it was being sent a one-handed area for a two-handed motion. `heart` therefore becomes `handed=False` with `areas=(AREA_BOTH,)`, and 3004 gets its own name, `heart_overhead`.
2. **The pin widens.** "Every motion id must be a member of `McPresetMotion`" would now reject the corrected `heart`, because the enum omits 1007. The two vendor sources disagree and neither is a superset — the enum omits 1007/1010/1011/3017/3024/3025/3031, and the doc table omits 4001/4002 and the whole of area 4. So the rule becomes: **every `(motion_id, area_id)` pair must be traceable to a named vendor source**, checked against that source, and the `hw_verified` flag records what has actually been seen to move on a real robot. Today that is `wave` (1002, area 2) and nothing else.

- [ ] **Step 1: Transcribe the vendor doc table**

The widened pin needs the doc table's `(motion_id, area_id)` pairs as data. Extract them from the SDK docs rather than typing them from memory:

```bash
python - <<'PY'
import re, pathlib, html
p = pathlib.Path('sdk/aimdk-aarch64-a424add7-artifacts/docs/cn/_build/html/en/dev/'
                 'Interface/control_mod/preset_motion.html')
rows = re.findall(r'<tr>(.*?)</tr>', p.read_text(encoding='utf-8'), re.S)
for row in rows:
    cells = [html.unescape(re.sub(r'<[^>]+>', '', c)).strip()
             for c in re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>', row, re.S)]
    if cells:
        print(' | '.join(cells))
PY
```

Read the output. Every row that gives a motion id and the control areas it accepts becomes one or more `(motion_id, area_id)` pairs. Write them into the test file (Step 5) as a `DOC_TABLE_COMBINATIONS` frozenset, sorted, one line per motion id, with the vendor's Chinese/English name in a trailing comment so the next reader can find the row again. `(1007, 3)` — 双手比心 — is the row this whole task turns on; make sure it is there.

If a pair the catalogue uses is absent from both the doc table and the enum, that is a finding, not a formatting problem: the catalogue entry is wrong and must be corrected or removed, and the reason recorded in the commit message.

- [ ] **Step 2: Write the failing gesture test (the widened pin)**

Replace `test/ros/test_gesture_catalogue_ids.py` entirely:

```python
"""Every catalogued (motion id, area) pair must come from a named vendor source.

The narrower version of this rule -- "the motion id must be a member of
McPresetMotion" -- is not sufficient and never was. The vendor ships two
sources that disagree and neither is a superset of the other: the enum omits
1007, 1010, 1011, 3017, 3024, 3025 and 3031; the interface doc's table omits
4001, 4002 and the whole of area 4. A catalogue entry pinned against only one
of them is pinned against half the evidence.

So each spec names its source and is checked against that source. The area is
part of the check because the area is what went wrong last time: 1007 is a
two-handed motion, Phase 1 sent it area 2, the controller refused it, and the
refusal was written down as "1007 does not exist".

Lives in the ros suite because the enum half needs aimdk_msgs.
"""
import pytest

from x2_greeter.core.gestures import AREA_BOTH, CATALOGUE, DEFAULT_ENABLED, VALID_SOURCES

pytestmark = pytest.mark.ros

# Transcribed from the interface docs, preset_motion.html (Step 1 of the task).
# One line per motion id; the trailing comment is the vendor's own name for it.
DOC_TABLE_COMBINATIONS = frozenset({
    (1007, 3),   # 双手比心 / two-handed heart at the chest
    # ... the remaining rows go here, transcribed in Step 1 ...
})


def _enum_motion_ids():
    from aimdk_msgs.msg import McPresetMotion
    return {value for name, value in vars(McPresetMotion).items()
            if isinstance(value, int) and name.isupper()}


def test_every_spec_declares_a_source_we_recognise():
    bad = {name: spec.source for name, spec in CATALOGUE.items()
           if spec.source not in VALID_SOURCES}
    assert not bad, (
        f'gesture(s) with no usable provenance: {bad}. Every entry must say '
        f'where its id came from -- one of {sorted(VALID_SOURCES)} -- because '
        f'"I read it somewhere" is how three invented ids reached hardware.')


def test_every_enum_sourced_motion_id_is_in_the_vendor_enum():
    known = _enum_motion_ids()
    invented = {name: spec.motion_id for name, spec in CATALOGUE.items()
                if spec.source == 'enum' and spec.motion_id not in known}
    assert not invented, (
        f'gesture(s) claiming the enum as their source but absent from it: '
        f'{invented}')


def test_every_doc_sourced_pair_is_in_the_doc_table():
    missing = {
        name: (spec.motion_id, area)
        for name, spec in CATALOGUE.items() if spec.source == 'doc_table'
        for area in spec.areas
        if (spec.motion_id, area) not in DOC_TABLE_COMBINATIONS
    }
    assert not missing, (
        f'gesture(s) claiming the doc table as their source with a pair the '
        f'table does not list: {missing}')


def test_the_default_enabled_gestures_are_all_real():
    # Called out separately so a failure names the ones a live robot would
    # actually have tried to perform.
    enum_ids = _enum_motion_ids()
    broken = []
    for name in DEFAULT_ENABLED:
        spec = CATALOGUE[name]
        for area in spec.areas:
            ok = (spec.motion_id in enum_ids
                  if spec.source == 'enum'
                  else (spec.motion_id, area) in DOC_TABLE_COMBINATIONS)
            if not ok:
                broken.append((name, spec.motion_id, area, spec.source))
    assert not broken, f'shipped-enabled gestures with unsourced pairs: {broken}'


def test_the_chest_heart_is_1007_performed_with_both_hands():
    # The correction this file exists to hold. 1007 is the two-handed heart at
    # the chest and it takes area 3. Sending it area 2 -- which is what
    # handed=True plus the shipped right-hand preference did -- is a
    # one-handed area for a two-handed motion, and the controller refuses it
    # silently. That refusal was once mistaken for the id being invented.
    spec = CATALOGUE['heart']
    assert spec.motion_id == 1007
    assert spec.areas == (AREA_BOTH,)
    assert spec.handed is False, (
        'heart must not follow hand_preference: there is no one-handed '
        'variant of it, so honouring a preference can only produce an area '
        'the motion does not accept')
    assert spec.source == 'doc_table'


def test_the_overhead_heart_is_a_separate_disabled_gesture():
    from aimdk_msgs.msg import McPresetMotion

    spec = CATALOGUE['heart_overhead']
    assert spec.motion_id == McPresetMotion.INTERACTION_SWEATHEART == 3004
    assert spec.source == 'enum'
    assert spec.hw_verified is False
    assert 'heart_overhead' not in DEFAULT_ENABLED, (
        'nobody has watched this one run: the area is an inference from the '
        'enum, not a row in the doc table, so it stays off until the bench '
        'check')


def test_only_gestures_actually_seen_on_hardware_are_marked_verified():
    verified = {name for name, spec in CATALOGUE.items() if spec.hw_verified}
    assert verified == {'wave'}, (
        f'hw_verified means a human watched the robot perform it, not that we '
        f'believe the id is right. Currently that is wave (1002, area 2) and '
        f'nothing else; got {sorted(verified)}. Widen this set only after the '
        f'bench check, in the same commit that records what was observed.')
```

- [ ] **Step 3: Run it in the ROS container and watch it fail**

Run: `docker/run_tests.sh` (which runs `python3 -m pytest src/x2_greeter/test -m ros -v -p no:cacheprovider -p no:launch_testing -p no:launch_ros`)
Expected: FAIL — `ImportError: cannot import name 'VALID_SOURCES'`.

- [ ] **Step 4: Apply the gesture-catalogue correction**

In `core/gestures.py`, add the provenance fields to the dataclass and its helper:

```python
VALID_SOURCES = frozenset({'enum', 'doc_table'})


@dataclass(frozen=True)
class GestureSpec:
    """One preset motion.

    areas lists the control areas the docs permit, most-preferred first -- so
    areas[0] is the fallback when a hand preference cannot be honoured.
    handed is True when the gesture has left/right variants and should follow
    gestures.hand_preference.

    source names where the (motion_id, area) pair came from: 'enum' for the
    McPresetMotion message, 'doc_table' for the interface docs' preset-motion
    table. The vendor's two sources disagree and neither contains the other,
    so an entry that does not say which one it came from cannot be checked.

    hw_verified is True only when a human has watched a real robot perform
    this exact pair. It is not a confidence rating.
    """

    name: str
    motion_id: int
    areas: tuple
    handed: bool
    source: str
    hw_verified: bool = False


def _spec(name: str, motion_id: int, areas: tuple, handed: bool,
          source: str = 'enum', hw_verified: bool = False) -> GestureSpec:
    if source not in VALID_SOURCES:
        raise ValueError(f'gesture {name}: source must be one of '
                         f'{sorted(VALID_SOURCES)}, got {source!r}')
    return GestureSpec(name=name, motion_id=motion_id, areas=areas,
                       handed=handed, source=source, hw_verified=hw_verified)
```

Every existing entry keeps its id and areas and gains `source='enum'` by
staying on the default; only these three lines change:

```python
    'wave': _spec('wave', 1002, (AREA_RIGHT, AREA_LEFT), True, hw_verified=True),
    ...
    # 1007 is the two-handed chest heart. It is NOT in McPresetMotion -- the
    # enum's heart is 3004, the overhead one below -- so its source is the
    # doc table, and it takes area 3 only. Phase 1 shipped it handed=True,
    # which with hand_preference: right sent a two-handed motion a one-handed
    # area; the controller refused it and the refusal was mistaken for the id
    # being invented.
    'heart': _spec('heart', 1007, (AREA_BOTH,), False, source='doc_table'),
    # --- available but disabled by default (spec section 9) ---
    # The enum's INTERACTION_SWEATHEART: hands above the head. Area 11 is an
    # inference from it being a whole-body motion, not a row anyone has read,
    # which is exactly why this ships disabled.
    'heart_overhead': _spec('heart_overhead', 3004, (AREA_WHOLE_BODY,), False),
```

`DEFAULT_ENABLED` is unchanged: `heart` stays enabled, `heart_overhead` is not added to it.

- [ ] **Step 5: Fill in `DOC_TABLE_COMBINATIONS` and re-run**

Paste the pairs transcribed in Step 1 into the frozenset, replacing the `...` comment line.

Run: `docker/run_tests.sh`
Expected: PASS. If `test_every_doc_sourced_pair_is_in_the_doc_table` still fails, the transcription is incomplete — go back to Step 1 output, do not relax the test.

- [ ] **Step 6: Run the host suite to catch anything that pinned the old heart id**

Run: `python -m pytest`
Expected: PASS. `test/core/test_gestures.py` constructs `GestureSpec`s and asserts on the catalogue; if it names `3004` for `heart` or builds a `GestureSpec` positionally without `source`, fix it there — the correction is the point of this task, not a regression.

- [ ] **Step 7: Write the failing emoji tests**

The spec (section 9.3) records that `emotion_id` is "a bare `uint8` with no enum message anywhere in the SDK". **That is wrong.** `aimdk_msgs/srv/PlayEmoji.srv` declares the full emotion enum as service constants — `EMOTION_EYE_THINKING = 170`, `EMOTION_EYE_HAPPY = 90`, and 20-odd more, plus `EMOTION_MODE_ONCE = 1` and `EMOTION_MODE_LOOP = 2`. So emoji ids get pinned against a vendor message exactly the way gesture ids do, and the catalogue can ship a conservative enabled set on day one instead of the empty list the spec assumed.

Create `test/core/test_faces.py` (pure, host suite):

```python
"""The emoji catalogue and the allowlist the model picks from.

Same shape as core/gestures.py and for the same reason: the model supplies a
*name*, which is validated against the enabled allowlist before it ever
becomes a vendor id. An emoji is the cheapest thing the robot can do -- it is
the only thing that happens between the person finishing their sentence and
the reply arriving four to eight seconds later -- so the catalogue ships
enabled, unlike the head.
"""
import pytest

from x2_greeter.core.faces import (
    CATALOGUE, DEFAULT_ENABLED, MODE_LOOP, MODE_ONCE, THINKING, EmojiSelector)


def test_the_thinking_emoji_is_in_the_catalogue_and_enabled():
    # Spec section 13: this is the only thing standing between the person and
    # several seconds of a robot that looks switched off.
    assert THINKING in CATALOGUE
    assert THINKING in DEFAULT_ENABLED


def test_the_modes_are_the_vendor_modes():
    assert MODE_ONCE == 1
    assert MODE_LOOP == 2


def test_every_default_enabled_emoji_is_in_the_catalogue():
    unknown = [name for name in DEFAULT_ENABLED if name not in CATALOGUE]
    assert not unknown, unknown


def test_no_angry_emoji_is_catalogued_at_all():
    # The vendor offers ANGRY and EXTREMEANGRY. A greeter has no use for
    # either, and the cheapest way to guarantee the model never picks one is
    # for the name not to exist.
    assert not [name for name in CATALOGUE if 'angry' in name]


def test_nothing_is_marked_hardware_verified_yet():
    # Nobody has watched the face screen. The flag records observation.
    assert not [name for name, spec in CATALOGUE.items() if spec.hw_verified]


def test_the_selector_honours_a_name_on_the_allowlist():
    selector = EmojiSelector(['thinking', 'happy'])
    assert selector.select('happy').name == 'happy'
    assert selector.select('happy').emotion_id == CATALOGUE['happy'].emotion_id


def test_the_selector_refuses_a_name_that_is_not_enabled():
    # Not a fallback to something random: an emoji nobody asked for is worse
    # than no emoji, and a disabled name means somebody disabled it.
    selector = EmojiSelector(['thinking', 'happy'])
    assert selector.select('shock') is None
    assert selector.select('nonsense') is None
    assert selector.select(None) is None


def test_the_selector_exposes_the_thinking_emoji_when_it_is_enabled():
    assert EmojiSelector(['thinking', 'happy']).thinking.name == 'thinking'


def test_the_selector_reports_no_thinking_emoji_when_it_is_disabled():
    # A deployment may turn the whole face off. That must degrade to silence,
    # not to an exception on the latency path.
    assert EmojiSelector(['happy']).thinking is None


def test_an_empty_allowlist_disables_the_face_rather_than_failing():
    selector = EmojiSelector([])
    assert selector.enabled_names == ()
    assert selector.thinking is None
    assert selector.select('happy') is None


def test_an_unknown_name_in_the_allowlist_is_a_configuration_error():
    with pytest.raises(ValueError) as exc:
        EmojiSelector(['happy', 'grumpy'])
    assert 'grumpy' in str(exc.value)
```

Create `test/ros/test_face_catalogue_ids.py` (ROS suite):

```python
"""Every catalogued emotion id must be a constant on the vendor's PlayEmoji.

The design doc said emoji ids exist only in a documentation table. They do
not: aimdk_msgs/srv/PlayEmoji.srv declares the whole enum as service
constants, so the same pin that protects the gesture catalogue protects this
one. Checking against the message is always better than checking against a
description of the message -- that is the lesson this project keeps paying
for.
"""
import pytest

from x2_greeter.core.faces import CATALOGUE, MODE_LOOP, MODE_ONCE

pytestmark = pytest.mark.ros


def _vendor_emotion_ids():
    from aimdk_msgs.srv import PlayEmoji
    return {value: name for name, value in vars(PlayEmoji).items()
            if isinstance(value, int) and name.startswith('EMOTION_')}


def test_every_catalogued_emotion_id_is_a_vendor_constant():
    known = _vendor_emotion_ids()
    invented = {name: spec.emotion_id for name, spec in CATALOGUE.items()
                if spec.emotion_id not in known}
    assert not invented, (
        f'emoji with ids PlayEmoji does not define: {invented}. Take the id '
        f'from the .srv, not from a table describing it.')


def test_the_thinking_emoji_is_the_vendor_thinking_eye():
    from aimdk_msgs.srv import PlayEmoji

    assert CATALOGUE['thinking'].emotion_id == PlayEmoji.EMOTION_EYE_THINKING


def test_the_modes_match_the_vendor_constants():
    from aimdk_msgs.srv import PlayEmoji

    assert MODE_ONCE == PlayEmoji.EMOTION_MODE_ONCE
    assert MODE_LOOP == PlayEmoji.EMOTION_MODE_LOOP
```

- [ ] **Step 8: Run both and watch them fail**

Run: `python -m pytest x2_greeter_ws/src/x2_greeter/test/core/test_faces.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'x2_greeter.core.faces'`

- [ ] **Step 9: Write `core/faces.py`**

```python
"""The face-screen emoji catalogue and the policy for choosing one.

Emotion ids are constants on aimdk_msgs/srv/PlayEmoji; test_face_catalogue_ids
pins every one of them against that message. An LLM never supplies an id -- it
supplies a *name*, validated against the enabled allowlist before it becomes
one.

Unlike a gesture, an emoji moves nothing and can hurt nobody, so the
catalogue ships enabled and the hw_verified flag means only "somebody has
watched this appear on the real face screen" -- which, today, nobody has.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Sequence, Tuple

MODE_ONCE = 1   # PlayEmoji.EMOTION_MODE_ONCE
MODE_LOOP = 2   # PlayEmoji.EMOTION_MODE_LOOP

THINKING = 'thinking'


@dataclass(frozen=True)
class EmojiSpec:
    """One face expression. source is 'srv' -- the PlayEmoji service constants."""

    name: str
    emotion_id: int
    source: str = 'srv'
    hw_verified: bool = False


def _spec(name: str, emotion_id: int) -> EmojiSpec:
    return EmojiSpec(name=name, emotion_id=emotion_id)


CATALOGUE: Dict[str, EmojiSpec] = {
    # --- the latency cover ---
    'thinking': _spec('thinking', 170),      # EMOTION_EYE_THINKING
    # --- everyday conversational faces ---
    'calm': _spec('calm', 10),               # EMOTION_IDLE_CALM_1
    'blink': _spec('blink', 1),              # EMOTION_IDLE_BLINK
    'happy': _spec('happy', 90),             # EMOTION_EYE_HAPPY
    'very_happy': _spec('very_happy', 100),  # EMOTION_EYE_EXTREMEHAPPY_1
    'cute': _spec('cute', 30),               # EMOTION_IDLE_CUTE_1
    'adore': _spec('adore', 200),            # EMOTION_EYE_ADORE
    'confused': _spec('confused', 130),      # EMOTION_EYE_CONFUSE
    # --- catalogued but not enabled by default ---
    'shock': _spec('shock', 140),            # EMOTION_EYE_SHOCK
    'sad': _spec('sad', 110),                # EMOTION_EYE_SAD
    'sympathy': _spec('sympathy', 120),      # EMOTION_EYE_SYMPATHY
    'serious': _spec('serious', 160),        # EMOTION_EYE_SERIOUS
    # The vendor also offers EMOTION_EYE_ANGRY (180) and EXTREMEANGRY (190).
    # They are deliberately absent: a greeter has no use for either, and a
    # name that does not exist is a name the model cannot choose.
}

DEFAULT_ENABLED: Tuple[str, ...] = (
    'thinking', 'calm', 'blink', 'happy', 'very_happy', 'cute', 'adore',
    'confused',
)


class EmojiSelector:
    """Turns an optional emoji *name* into a vendor spec, or into nothing.

    Unlike GestureSelector this never falls back to a random choice. A gesture
    is part of a greeting and something is better than nothing; an expression
    nobody asked for is just a robot pulling a face mid-sentence.
    """

    def __init__(self, enabled: Sequence[str]) -> None:
        enabled = tuple(enabled)
        unknown = [name for name in enabled if name not in CATALOGUE]
        if unknown:
            raise ValueError(
                f'unknown emoji in face.enabled: {", ".join(unknown)}')
        self._enabled = enabled

    @property
    def enabled_names(self) -> Tuple[str, ...]:
        return self._enabled

    @property
    def thinking(self) -> Optional[EmojiSpec]:
        return CATALOGUE[THINKING] if THINKING in self._enabled else None

    def select(self, requested: Optional[str]) -> Optional[EmojiSpec]:
        if requested in self._enabled:
            return CATALOGUE[requested]
        return None
```

- [ ] **Step 10: Run everything**

Run: `python -m pytest`
Expected: PASS (host suite, including the new `test_faces.py` and the corrected gesture tests)

Run: `docker/run_tests.sh`
Expected: PASS (ROS suite, including both widened pins)

- [ ] **Step 11: Commit**

```bash
git add x2_greeter_ws/src/x2_greeter/x2_greeter/core/gestures.py \
        x2_greeter_ws/src/x2_greeter/x2_greeter/core/faces.py \
        x2_greeter_ws/src/x2_greeter/test/core/test_faces.py \
        x2_greeter_ws/src/x2_greeter/test/core/test_gestures.py \
        x2_greeter_ws/src/x2_greeter/test/ros/test_gesture_catalogue_ids.py \
        x2_greeter_ws/src/x2_greeter/test/ros/test_face_catalogue_ids.py
git commit -m "fix(core): heart is 1007 area 3; pin every gesture and emoji id to a named vendor source"
```

---

## Task 4: Reading the scene — who is there, how far, and who is being addressed

**Files:**
- Create: `x2_greeter_ws/src/x2_greeter/x2_greeter/core/scene.py`
- Test: `x2_greeter_ws/src/x2_greeter/test/core/test_scene.py`
- Reference (read-only, **do not modify**): `x2_greeter_ws/src/x2_greeter/x2_greeter/core/detection.py`

**Interfaces:**
- Consumes: `core.detection.RawDetection` and `core.detection.median_depth_m(depth, bbox, rgb_shape, depth_scale) -> Optional[float]` — both already exist and are unchanged.
- Produces:
  - `core.scene.AddressingMode` — `str` enum with members `INDIVIDUAL = 'individual'` and `GROUP = 'group'`
  - `core.scene.Person` frozen dataclass: `bbox: Tuple[int, int, int, int]`, `confidence: float`, `distance_m: float`, `center_offset: float`, `stature_m: Optional[float]`, `likely_child: bool`
  - `core.scene.SceneConfig` frozen dataclass: `confidence_min: float = 0.5`, `individual_max_m: float = 2.0`, `scene_max_m: float = 5.0`, `child_stature_max_m: float = 1.35`, `vertical_fov_rad: float = 1.1868`
  - `core.scene.SceneSnapshot` frozen dataclass: `people: Tuple[Person, ...]`, `subject: Optional[Person]`, `mode: AddressingMode`, `at_s: float`; properties `person_count: int`, `closest_m: Optional[float]`, `has_child: bool`
  - `core.scene.stature_m(bbox, image_height: int, distance_m: float, vertical_fov_rad: float = ...) -> Optional[float]`
  - `core.scene.choose_subject(people: Sequence[Person], individual_max_m: float) -> Tuple[Optional[Person], AddressingMode]`
  - `core.scene.child_mode(local_child: bool, model_age_band: Optional[str]) -> bool`
  - `core.scene.observe(raws, rgb_shape, depth, depth_scale, config, at_s) -> SceneSnapshot`

**Why this is not an edit to `detection.py`:** `gate_detections()` returns *the single most central passing detection*. That is exactly right for Phase 1 — one person, one greeting — and exactly wrong here, where the addressing rule needs to compare everyone in frame before it can decide whether it is talking to a person or to a group. `scene.py` composes the pure `median_depth_m()` helper and does its own gating. `detection.py` is untouched (spec section 2), and the Phase 1 greeter keeps working unchanged.

**The addressing rule, locked:** at session start, if anyone is closer than `individual_max_m` (2.0 m), the mode is INDIVIDUAL and the subject is the most central of *those*; if everyone is 2.0 m or further, the mode is GROUP. **The mode is decided once and held for the whole session** — a robot that switches from "you" to "everyone" halfway through a conversation because somebody stepped forward is worse than one that picks wrong and commits.

- [ ] **Step 1: Write the failing test**

Create `test/core/test_scene.py`:

```python
"""Who is in front of the robot, and which of them it is talking to.

The stature figures in these tests are geometry, not biometrics: the robot
never identifies anybody, it estimates how tall the thing in the box is and
uses that to decide whether to speak to a child the way you speak to a child.
"""
import math

import numpy as np
import pytest

from x2_greeter.core.detection import RawDetection
from x2_greeter.core.scene import (
    AddressingMode, Person, SceneConfig, SceneSnapshot, child_mode,
    choose_subject, observe, stature_m)

VFOV = 1.1868   # 68 degrees, head RGB-D colour vertical FOV


def _person(distance_m, center_offset=0.0, stature=1.7, child=False,
            bbox=(0, 0, 10, 10), confidence=0.9):
    return Person(bbox=bbox, confidence=confidence, distance_m=distance_m,
                  center_offset=center_offset, stature_m=stature,
                  likely_child=child)


def test_stature_scales_with_distance_for_the_same_box():
    # The same pixel height twice as far away is twice as tall in the world.
    near = stature_m((0, 0, 100, 240), image_height=480, distance_m=2.0,
                     vertical_fov_rad=VFOV)
    far = stature_m((0, 0, 100, 240), image_height=480, distance_m=4.0,
                    vertical_fov_rad=VFOV)
    assert far == pytest.approx(2.0 * near, rel=1e-6)


def test_stature_of_a_full_height_box_is_the_full_vertical_fov_arc():
    expected = 2.0 * 3.0 * math.tan(VFOV / 2.0)
    assert stature_m((0, 0, 100, 480), 480, 3.0, VFOV) == pytest.approx(expected)


@pytest.mark.parametrize('image_height, distance_m', [(0, 3.0), (480, 0.0), (480, -1.0)])
def test_stature_is_unknown_rather_than_wrong_when_the_inputs_are_degenerate(
        image_height, distance_m):
    assert stature_m((0, 0, 10, 100), image_height, distance_m, VFOV) is None


def test_a_person_within_two_metres_makes_it_an_individual_conversation():
    people = [_person(1.4, center_offset=0.3), _person(3.0, center_offset=0.0)]
    subject, mode = choose_subject(people, individual_max_m=2.0)
    assert mode is AddressingMode.INDIVIDUAL
    assert subject.distance_m == 1.4, (
        'the subject is the most central person *inside* the individual '
        'range -- not the most central person overall, or the one further '
        'away is addressed as "you" while somebody stands at the robot\'s '
        'elbow being ignored')


def test_the_most_central_of_the_near_people_is_the_subject():
    people = [_person(1.9, center_offset=0.4), _person(1.2, center_offset=0.05)]
    subject, mode = choose_subject(people, individual_max_m=2.0)
    assert mode is AddressingMode.INDIVIDUAL
    assert subject.center_offset == 0.05


def test_everyone_beyond_two_metres_makes_it_a_group():
    people = [_person(2.5, center_offset=0.1), _person(3.4, center_offset=0.2)]
    subject, mode = choose_subject(people, individual_max_m=2.0)
    assert mode is AddressingMode.GROUP
    assert subject is not None, (
        'GROUP still needs somebody to look at; it changes how the robot '
        'speaks, not whether it has eyes')
    assert subject.center_offset == 0.1


def test_exactly_two_metres_counts_as_a_group():
    # The boundary is stated as "closer than 2.0 m" -- pinned so a later
    # refactor cannot quietly turn < into <=.
    subject, mode = choose_subject([_person(2.0)], individual_max_m=2.0)
    assert mode is AddressingMode.GROUP


def test_an_empty_scene_has_no_subject():
    subject, mode = choose_subject([], individual_max_m=2.0)
    assert subject is None
    assert mode is AddressingMode.GROUP


def test_child_mode_engages_when_either_signal_says_child():
    # Child mode only ever *restricts* what the robot says, so the safe error
    # is to engage it when unsure. Both signals are weak on their own: the
    # stature heuristic mistakes a crouching adult for a child, and the model
    # mistakes a short adult for one.
    assert child_mode(local_child=True, model_age_band=None) is True
    assert child_mode(local_child=False, model_age_band='child') is True
    assert child_mode(local_child=True, model_age_band='adult') is True
    assert child_mode(local_child=False, model_age_band='adult') is False
    assert child_mode(local_child=False, model_age_band=None) is False


@pytest.mark.parametrize('band', ['CHILD', ' child ', 'Child'])
def test_the_model_age_band_is_read_case_and_space_insensitively(band):
    assert child_mode(False, band) is True


def test_an_unrecognised_age_band_does_not_engage_child_mode():
    assert child_mode(False, 'teenager-ish') is False


def _frame_with(boxes, distances_m, shape=(480, 640, 3), depth_scale=0.001):
    """Build a depth array where each box reads back its intended distance."""
    depth = np.zeros(shape[:2], dtype=np.uint16)
    for (x1, y1, x2, y2), metres in zip(boxes, distances_m):
        depth[y1:y2, x1:x2] = int(metres / depth_scale)
    return depth


def test_observe_builds_one_person_per_passing_detection():
    boxes = [(100, 100, 200, 400), (400, 150, 480, 380)]
    depth = _frame_with(boxes, [1.5, 2.8])
    raws = [RawDetection(bbox=box, confidence=0.9) for box in boxes]
    scene = observe(raws, (480, 640, 3), depth, 0.001, SceneConfig(), at_s=10.0)
    assert scene.person_count == 2
    assert scene.at_s == 10.0
    assert scene.closest_m == pytest.approx(1.5, abs=0.05)
    assert scene.mode is AddressingMode.INDIVIDUAL


def test_observe_drops_low_confidence_detections():
    boxes = [(100, 100, 200, 400)]
    depth = _frame_with(boxes, [1.5])
    raws = [RawDetection(bbox=boxes[0], confidence=0.3)]
    scene = observe(raws, (480, 640, 3), depth, 0.001,
                    SceneConfig(confidence_min=0.5), at_s=0.0)
    assert scene.person_count == 0
    assert scene.subject is None


def test_observe_drops_a_detection_with_no_usable_depth():
    # An all-zero depth patch is "no reading", not "zero metres away". A
    # person admitted with a guessed distance would be gated for gestures
    # against a number nobody measured.
    depth = np.zeros((480, 640), dtype=np.uint16)
    raws = [RawDetection(bbox=(100, 100, 200, 400), confidence=0.9)]
    scene = observe(raws, (480, 640, 3), depth, 0.001, SceneConfig(), at_s=0.0)
    assert scene.person_count == 0


def test_observe_drops_people_beyond_the_scene_horizon():
    boxes = [(100, 100, 200, 400)]
    depth = _frame_with(boxes, [8.0])
    raws = [RawDetection(bbox=boxes[0], confidence=0.9)]
    scene = observe(raws, (480, 640, 3), depth, 0.001,
                    SceneConfig(scene_max_m=5.0), at_s=0.0)
    assert scene.person_count == 0


def test_observe_flags_a_short_person_as_likely_child():
    # A 150 px box at 2 m in a 480 px frame is about 0.85 m tall.
    boxes = [(300, 200, 360, 350)]
    depth = _frame_with(boxes, [2.0])
    raws = [RawDetection(bbox=boxes[0], confidence=0.9)]
    scene = observe(raws, (480, 640, 3), depth, 0.001,
                    SceneConfig(child_stature_max_m=1.35), at_s=0.0)
    assert scene.person_count == 1
    assert scene.people[0].stature_m < 1.35
    assert scene.people[0].likely_child is True
    assert scene.has_child is True


def test_observe_does_not_flag_a_full_height_person_as_a_child():
    boxes = [(300, 40, 380, 440)]
    depth = _frame_with(boxes, [2.5])
    raws = [RawDetection(bbox=boxes[0], confidence=0.9)]
    scene = observe(raws, (480, 640, 3), depth, 0.001, SceneConfig(), at_s=0.0)
    assert scene.people[0].likely_child is False
    assert scene.has_child is False


def test_the_snapshot_is_frozen():
    scene = SceneSnapshot(people=(), subject=None, mode=AddressingMode.GROUP,
                          at_s=0.0)
    with pytest.raises(Exception):
        scene.at_s = 1.0
```

- [ ] **Step 2: Run it and watch it fail**

Run: `python -m pytest x2_greeter_ws/src/x2_greeter/test/core/test_scene.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'x2_greeter.core.scene'`

- [ ] **Step 3: Write `core/scene.py`**

```python
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
        x1, _, x2, _ = raw.bbox
        width = int(rgb_shape[1])
        centre = (int(x1) + int(x2)) / 2.0
        offset = ((centre - width / 2.0) / (width / 2.0)) if width > 0 else 0.0
        height_m = stature_m(raw.bbox, image_height, distance,
                             config.vertical_fov_rad)
        people.append(Person(
            bbox=tuple(int(v) for v in raw.bbox),
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
```

- [ ] **Step 4: Run the tests**

Run: `python -m pytest x2_greeter_ws/src/x2_greeter/test/core/test_scene.py -v`
Expected: PASS (23 tests)

If `RawDetection` does not take `bbox=`/`confidence=` as keyword arguments, read `core/detection.py` and match its real constructor in the test — do not change `detection.py`.

- [ ] **Step 5: Confirm the Phase 1 greeter is untouched**

Run: `python -m pytest`
Expected: PASS, with `core/detection.py` unmodified (`git diff --stat` must not list it).

- [ ] **Step 6: Commit**

```bash
git add x2_greeter_ws/src/x2_greeter/x2_greeter/core/scene.py \
        x2_greeter_ws/src/x2_greeter/test/core/test_scene.py
git commit -m "feat(core): multi-person scene reading and the locked addressing rule"
```

---

## Task 5: The transcriber port and its faster-whisper adapter

**Files:**
- Create: `x2_greeter_ws/src/x2_greeter/x2_greeter/cognition/transcriber.py`
- Create: `x2_greeter_ws/src/x2_greeter/x2_greeter/sim/fakes.py`
- Test: `x2_greeter_ws/src/x2_greeter/test/cognition/test_transcriber.py`

**Interfaces:**
- Consumes: `core.language.normalise_language`.
- Produces:
  - `cognition.transcriber.Utterance(NamedTuple)`: `text: str`, `language: Optional[str]`, `confidence: float`; property `is_empty: bool`
  - `cognition.transcriber.TranscriptionUnavailable(Exception)`
  - `cognition.transcriber.Transcriber` — `typing.Protocol` with `transcribe(pcm: bytes, sample_rate: int) -> Utterance`
  - `cognition.transcriber.FasterWhisperTranscriber(model_size: str = 'small', device: str = 'auto', compute_type: str = 'int8', model=None, logger=None)` implementing that protocol, with `.name == 'faster_whisper'`
  - `cognition.transcriber.SAMPLE_RATE_HZ: int == 16000`
  - `sim.fakes.ScriptedTranscriber(utterances: Sequence[Utterance])` — pops one per call, raises `TranscriptionUnavailable` when exhausted if constructed with `raise_when_exhausted=True`

**Audio format, from the vendor:** 16 kHz, 16-bit, mono, PCM S16LE. `ProcessedAudioOutput.audio_data` is a `uint8[]` carrying exactly that.

**`sim/fakes.py` starts here and grows.** Later tasks add fakes for the other ports to the same file. It must stay pure Python — it is imported by host-suite tests, so a single `rclpy` import in it would break every one of them. `sim/fake_robot.py` is the ROS-side simulator and stays separate.

**No test in this repo loads a Whisper model.** The adapter takes an already-constructed `model` object for exactly this reason; tests pass a stub. Loading `small` costs ~500 MB of download and tens of seconds, and a test suite that needs a model download is a test suite that stops being run.

- [ ] **Step 1: Write the failing test**

Create `test/cognition/test_transcriber.py`:

```python
"""Speech to text, locally on PC2, with the model injected.

No test here loads a real Whisper model: the adapter takes an
already-constructed model object, so the tests can stub it. A suite that
needs a 500 MB download is a suite people stop running.
"""
import pytest

from x2_greeter.cognition.transcriber import (
    SAMPLE_RATE_HZ, FasterWhisperTranscriber, TranscriptionUnavailable,
    Utterance)
from x2_greeter.sim.fakes import ScriptedTranscriber


class _Segment:
    def __init__(self, text, avg_logprob=-0.1, no_speech_prob=0.05):
        self.text = text
        self.avg_logprob = avg_logprob
        self.no_speech_prob = no_speech_prob


class _Info:
    def __init__(self, language='en', language_probability=0.98):
        self.language = language
        self.language_probability = language_probability


class _StubModel:
    """Stands in for faster_whisper.WhisperModel."""

    def __init__(self, segments, info=None, raises=None):
        self._segments = segments
        self._info = info or _Info()
        self._raises = raises
        self.calls = []

    def transcribe(self, audio, **kwargs):
        self.calls.append((audio, kwargs))
        if self._raises is not None:
            raise self._raises
        return iter(self._segments), self._info


def _pcm(samples=16000):
    # One second of silence is fine: the stub decides what comes back.
    return b'\x00\x00' * samples


def test_the_sample_rate_is_the_vendor_sample_rate():
    assert SAMPLE_RATE_HZ == 16000


def test_an_utterance_knows_when_it_is_empty():
    assert Utterance('', 'en', 0.9).is_empty is True
    assert Utterance('   ', 'en', 0.9).is_empty is True
    assert Utterance('hello', 'en', 0.9).is_empty is False


def test_transcribing_joins_the_segments_and_trims():
    model = _StubModel([_Segment(' Hello there.'), _Segment(' How are you?')])
    result = FasterWhisperTranscriber(model=model).transcribe(_pcm(), SAMPLE_RATE_HZ)
    assert result.text == 'Hello there. How are you?'
    assert result.language == 'en'
    assert result.confidence == pytest.approx(0.98)


def test_a_chinese_result_keeps_its_language():
    model = _StubModel([_Segment('你好')], _Info('zh', 0.91))
    result = FasterWhisperTranscriber(model=model).transcribe(_pcm(), SAMPLE_RATE_HZ)
    assert result.text == '你好'
    assert result.language == 'zh'


def test_an_unsupported_language_is_reported_as_none_not_as_itself():
    # Malay is deferred. The transcriber may well detect it; the rest of the
    # system must see "not a language we speak", not a tag it will later try
    # to switch to.
    model = _StubModel([_Segment('apa khabar')], _Info('ms', 0.88))
    result = FasterWhisperTranscriber(model=model).transcribe(_pcm(), SAMPLE_RATE_HZ)
    assert result.language is None
    assert result.text == 'apa khabar'


def test_a_regional_tag_is_normalised():
    model = _StubModel([_Segment('hi')], _Info('en-US', 0.9))
    assert FasterWhisperTranscriber(model=model).transcribe(
        _pcm(), SAMPLE_RATE_HZ).language == 'en'


def test_no_segments_produces_an_empty_utterance_rather_than_an_error():
    # Silence, a cough, or the tail of the robot's own voice. Not a failure.
    result = FasterWhisperTranscriber(model=_StubModel([])).transcribe(
        _pcm(), SAMPLE_RATE_HZ)
    assert result.is_empty


def test_empty_audio_never_reaches_the_model():
    model = _StubModel([_Segment('should not happen')])
    result = FasterWhisperTranscriber(model=model).transcribe(b'', SAMPLE_RATE_HZ)
    assert result.is_empty
    assert model.calls == []


def test_the_pcm_is_converted_to_normalised_float_mono():
    import numpy as np

    model = _StubModel([_Segment('x')])
    # +32767 and -32768 as little-endian int16.
    FasterWhisperTranscriber(model=model).transcribe(
        b'\xff\x7f\x00\x80', SAMPLE_RATE_HZ)
    audio, _ = model.calls[0]
    assert isinstance(audio, np.ndarray)
    assert audio.dtype == np.float32
    assert audio.shape == (2,)
    assert audio[0] == pytest.approx(1.0, abs=1e-4)
    assert audio[1] == pytest.approx(-1.0, abs=1e-4)


def test_an_odd_length_buffer_drops_the_trailing_byte_instead_of_crashing():
    model = _StubModel([_Segment('x')])
    FasterWhisperTranscriber(model=model).transcribe(b'\x01\x02\x03', SAMPLE_RATE_HZ)
    audio, _ = model.calls[0]
    assert audio.shape == (1,)


def test_a_sample_rate_the_model_was_not_built_for_is_refused():
    model = _StubModel([_Segment('x')])
    with pytest.raises(TranscriptionUnavailable):
        FasterWhisperTranscriber(model=model).transcribe(_pcm(), 44100)
    assert model.calls == []


def test_a_model_failure_becomes_transcription_unavailable():
    # Every failure has one name upstream, so conversation.py has one branch.
    model = _StubModel([], raises=RuntimeError('CUDA out of memory'))
    with pytest.raises(TranscriptionUnavailable) as exc:
        FasterWhisperTranscriber(model=model).transcribe(_pcm(), SAMPLE_RATE_HZ)
    assert 'CUDA out of memory' in str(exc.value)


def test_no_audio_bytes_appear_in_any_log_line(caplog):
    import logging

    model = _StubModel([], raises=RuntimeError('boom'))
    caplog.set_level(logging.DEBUG)
    with pytest.raises(TranscriptionUnavailable):
        FasterWhisperTranscriber(model=model, logger=logging.getLogger('x2')
                                 ).transcribe(b'\xde\xad\xbe\xef' * 100,
                                              SAMPLE_RATE_HZ)
    joined = ' '.join(record.getMessage() for record in caplog.records)
    assert 'dead' not in joined.lower()
    assert '\\xde' not in joined


def test_the_scripted_fake_pops_one_utterance_per_call():
    fake = ScriptedTranscriber([Utterance('one', 'en', 0.9),
                                Utterance('two', 'en', 0.9)])
    assert fake.transcribe(b'', SAMPLE_RATE_HZ).text == 'one'
    assert fake.transcribe(b'', SAMPLE_RATE_HZ).text == 'two'
    assert fake.transcribe(b'', SAMPLE_RATE_HZ).is_empty


def test_the_scripted_fake_can_be_asked_to_fail_when_it_runs_out():
    fake = ScriptedTranscriber([], raise_when_exhausted=True)
    with pytest.raises(TranscriptionUnavailable):
        fake.transcribe(b'', SAMPLE_RATE_HZ)
```

- [ ] **Step 2: Run it and watch it fail**

Run: `python -m pytest x2_greeter_ws/src/x2_greeter/test/cognition/test_transcriber.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'x2_greeter.cognition.transcriber'`

- [ ] **Step 3: Write `cognition/transcriber.py`**

```python
"""Speech to text, on PC2, with no network involved.

faster-whisper `small` on the Jetson's GPU. The model is injected rather than
constructed here so that no test ever has to download or load one -- a suite
that needs half a gigabyte of weights is a suite that quietly stops being
run.

Privacy rule, same as the camera: the PCM lives in memory for one utterance
and is dropped. Nothing is written to disk, and no audio byte ever reaches a
log line at any level -- which is why the failure path logs the exception's
message and never the buffer.
"""
from __future__ import annotations

from typing import NamedTuple, Optional, Protocol, Sequence

import numpy as np

from x2_greeter.core.language import normalise_language

SAMPLE_RATE_HZ = 16000     # the vendor publishes 16 kHz, 16-bit, mono, S16LE
_INT16_FULL_SCALE = 32768.0


class TranscriptionUnavailable(Exception):
    """The transcriber could not produce a result. One name for every cause."""


class Utterance(NamedTuple):
    text: str
    language: Optional[str]
    confidence: float

    @property
    def is_empty(self) -> bool:
        return not self.text.strip()


class Transcriber(Protocol):
    def transcribe(self, pcm: bytes, sample_rate: int) -> Utterance:
        ...


def _to_float_mono(pcm: bytes) -> np.ndarray:
    """S16LE bytes -> float32 in [-1, 1). An odd trailing byte is dropped."""
    usable = len(pcm) - (len(pcm) % 2)
    samples = np.frombuffer(pcm[:usable], dtype='<i2')
    return (samples.astype(np.float32) / _INT16_FULL_SCALE)


class FasterWhisperTranscriber:
    name = 'faster_whisper'

    def __init__(self, model_size: str = 'small', device: str = 'auto',
                 compute_type: str = 'int8', model=None, logger=None) -> None:
        self._logger = logger
        if model is None:
            # Imported lazily so importing this module -- which the layering
            # test does for every cognition module -- never pulls in the
            # runtime or its weights.
            from faster_whisper import WhisperModel
            model = WhisperModel(model_size, device=device,
                                 compute_type=compute_type)
        self._model = model

    def transcribe(self, pcm: bytes, sample_rate: int) -> Utterance:
        if int(sample_rate) != SAMPLE_RATE_HZ:
            raise TranscriptionUnavailable(
                f'expected {SAMPLE_RATE_HZ} Hz audio, got {sample_rate} Hz')
        audio = _to_float_mono(pcm)
        if audio.size == 0:
            return Utterance(text='', language=None, confidence=0.0)

        try:
            segments, info = self._model.transcribe(
                audio, beam_size=1, vad_filter=False,
                condition_on_previous_text=False)
            text = ' '.join(segment.text.strip() for segment in segments).strip()
        except Exception as exc:                  # noqa: BLE001 - one name upstream
            if self._logger is not None:
                # The message only. Never the buffer, never its length in a
                # form that could be mistaken for content.
                self._logger.warning(f'transcription failed: {exc}')
            raise TranscriptionUnavailable(str(exc)) from exc

        return Utterance(
            text=text,
            language=normalise_language(getattr(info, 'language', None)),
            confidence=float(getattr(info, 'language_probability', 0.0) or 0.0),
        )
```

- [ ] **Step 4: Write `sim/fakes.py`**

```python
"""Pure-Python stand-ins for the ports, for tests and for the simulator.

Deliberately free of rclpy: the host test suite imports this module, and a
single ROS import here would break every host test on a machine without a ROS
installation. The ROS-side simulator is sim/fake_robot.py and stays separate.

Later tasks add fakes for the remaining ports to this file.
"""
from __future__ import annotations

from typing import List, Optional, Sequence

from x2_greeter.cognition.transcriber import TranscriptionUnavailable, Utterance


class ScriptedTranscriber:
    """Returns a prepared utterance per call, then empties (or fails)."""

    name = 'scripted'

    def __init__(self, utterances: Sequence[Utterance],
                 raise_when_exhausted: bool = False) -> None:
        self._queue: List[Utterance] = list(utterances)
        self._raise_when_exhausted = bool(raise_when_exhausted)
        self.calls = 0

    def transcribe(self, pcm: bytes, sample_rate: int) -> Utterance:
        self.calls += 1
        if self._queue:
            return self._queue.pop(0)
        if self._raise_when_exhausted:
            raise TranscriptionUnavailable('scripted transcriber exhausted')
        return Utterance(text='', language=None, confidence=0.0)
```

- [ ] **Step 5: Run the tests**

Run: `python -m pytest x2_greeter_ws/src/x2_greeter/test/cognition/test_transcriber.py -v`
Expected: PASS (17 tests)

- [ ] **Step 6: Run the layering and privacy guards**

Run: `python -m pytest x2_greeter_ws/src/x2_greeter/test/test_layering.py x2_greeter_ws/src/x2_greeter/test/test_privacy.py -v`
Expected: PASS. `cognition/transcriber.py` must import without `faster_whisper` installed — that is what the lazy import inside `__init__` is for, and the layering test's "importable without ROS" parametrisation will catch it if it moves to module scope.

- [ ] **Step 7: Commit**

```bash
git add x2_greeter_ws/src/x2_greeter/x2_greeter/cognition/transcriber.py \
        x2_greeter_ws/src/x2_greeter/x2_greeter/sim/fakes.py \
        x2_greeter_ws/src/x2_greeter/test/cognition/test_transcriber.py
git commit -m "feat(cognition): local faster-whisper transcriber behind an injectable port"
```

---

## Task 6: The turn contract — what a reply is and what makes one valid

**Files:**
- Create: `x2_greeter_ws/src/x2_greeter/x2_greeter/cognition/dialogue.py`
- Modify: `x2_greeter_ws/src/x2_greeter/x2_greeter/sim/fakes.py` (add `ScriptedDialogueBackend`)
- Test: `x2_greeter_ws/src/x2_greeter/test/cognition/test_dialogue.py`

**Interfaces:**
- Consumes: `core.language.normalise_language`, `core.language.DEFAULT_LANGUAGE`; the existing Phase 1 `BackendUnavailable` exception and `Frame` type. **Find them first** — `grep -rn "class BackendUnavailable\|class Frame" x2_greeter_ws/src/x2_greeter/x2_greeter` — and import from where they already live. Do not define a second copy of either.
- Produces:
  - `cognition.dialogue.Turn` frozen dataclass: `reply: str`, `language: str`, `gesture: Optional[str]`, `emoji: Optional[str]`, `end: bool`
  - `cognition.dialogue.Exchange(NamedTuple)`: `speaker: str` (`'person'` or `'robot'`), `text: str`
  - `cognition.dialogue.TurnLimits` frozen dataclass: `reply_max_chars: int = 200`
  - `cognition.dialogue.TurnRejected(Exception)`
  - `cognition.dialogue.truncate_at_sentence(text: str, max_chars: int) -> str`
  - `cognition.dialogue.validate_turn(doc, enabled_gestures: Sequence[str], enabled_emoji: Sequence[str], limits: TurnLimits, fallback_language: str) -> Turn`
  - `cognition.dialogue.build_turn_schema(enabled_gestures: Sequence[str], enabled_emoji: Sequence[str]) -> dict`
  - `cognition.dialogue.DialogueBackend` — `typing.Protocol` with
    `respond(base_frame, frame, scene, venue, history: Sequence[Exchange], utterance, language: str, child: bool) -> Turn`
  - `sim.fakes.ScriptedDialogueBackend(turns: Sequence[Turn], fail_after: Optional[int] = None)` with `.calls` and `.last_call`

**The reason validation lives here and not in the Claude adapter:** every backend gets the same contract. A model that returns a gesture nobody enabled, a language this phase does not speak, or a 900-character monologue is not a special case of Claude — it is what language models do, and the robot must behave identically whichever one is behind the port.

**Truncation is at a sentence boundary, not at character 200.** Cutting mid-word produces speech that sounds like the robot was interrupted; cutting at the last full stop before the limit produces speech that sounds brief.

- [ ] **Step 1: Write the failing test**

Create `test/cognition/test_dialogue.py`:

```python
"""The turn contract: what comes back from a dialogue backend, and what
survives validation.

Every rule here exists because a language model will eventually break it. The
model is asked for a gesture name from an allowlist; it will invent one. It
is asked for 'en' or 'zh'; it will answer 'English'. It is told to keep the
reply short; it will write a paragraph. None of those are errors worth
aborting a conversation over -- they are fields to drop or repair -- but a
reply that is missing or empty is, because a robot that gestures at somebody
in silence is worse than one that says nothing at all.
"""
import pytest

from x2_greeter.cognition.dialogue import (
    Exchange, Turn, TurnLimits, TurnRejected, build_turn_schema,
    truncate_at_sentence, validate_turn)
from x2_greeter.sim.fakes import ScriptedDialogueBackend

GESTURES = ('wave', 'heart', 'bow')
EMOJI = ('thinking', 'happy', 'confused')
LIMITS = TurnLimits(reply_max_chars=200)


def _valid(**overrides):
    doc = {'reply': 'Hello there!', 'language': 'en', 'gesture': 'wave',
           'emoji': 'happy', 'end': False}
    doc.update(overrides)
    return doc


def test_a_well_formed_turn_passes_through_unchanged():
    turn = validate_turn(_valid(), GESTURES, EMOJI, LIMITS, 'en')
    assert turn == Turn(reply='Hello there!', language='en', gesture='wave',
                        emoji='happy', end=False)


@pytest.mark.parametrize('doc', [
    {}, {'reply': ''}, {'reply': '   '}, {'reply': None}, {'reply': 42},
    'not a dict', None, [],
])
def test_a_turn_with_no_usable_reply_is_rejected(doc):
    with pytest.raises(TurnRejected):
        validate_turn(doc, GESTURES, EMOJI, LIMITS, 'en')


def test_a_gesture_that_is_not_enabled_is_dropped_not_rejected():
    turn = validate_turn(_valid(gesture='backflip'), GESTURES, EMOJI, LIMITS, 'en')
    assert turn.gesture is None
    assert turn.reply == 'Hello there!', 'the words survive a bad gesture'


def test_an_emoji_that_is_not_enabled_is_dropped():
    assert validate_turn(_valid(emoji='rage'), GESTURES, EMOJI, LIMITS,
                         'en').emoji is None


def test_a_null_gesture_and_emoji_are_perfectly_valid():
    turn = validate_turn(_valid(gesture=None, emoji=None), GESTURES, EMOJI,
                         LIMITS, 'en')
    assert turn.gesture is None and turn.emoji is None


def test_a_language_the_phase_does_not_speak_falls_back():
    # 'ms' is deferred; 'English' is the model answering in prose. Neither is
    # a reason to drop the reply.
    for bad in ['ms', 'English', '', None, 'fr']:
        turn = validate_turn(_valid(language=bad), GESTURES, EMOJI, LIMITS, 'zh')
        assert turn.language == 'zh'


def test_a_regional_language_tag_is_normalised():
    assert validate_turn(_valid(language='zh-CN'), GESTURES, EMOJI, LIMITS,
                         'en').language == 'zh'


@pytest.mark.parametrize('raw, expected', [
    (True, True), (False, False), ('yes', False), (1, False), (None, False)])
def test_end_is_only_true_for_a_real_boolean(raw, expected):
    # Ending the conversation is a decision; a truthy string is not one.
    assert validate_turn(_valid(end=raw), GESTURES, EMOJI, LIMITS,
                         'en').end is expected


def test_a_long_reply_is_truncated_at_the_last_sentence_that_fits():
    long_reply = ('That is a great question. ' * 20).strip()
    turn = validate_turn(_valid(reply=long_reply), GESTURES, EMOJI, LIMITS, 'en')
    assert len(turn.reply) <= 200
    assert turn.reply.endswith('.')


def test_truncation_prefers_a_sentence_boundary():
    text = 'One. Two. Three is a much longer sentence that will not fit.'
    assert truncate_at_sentence(text, 20) == 'One. Two.'


@pytest.mark.parametrize('terminator', ['.', '!', '?', '。', '！', '？'])
def test_truncation_understands_both_scripts(terminator):
    text = f'First{terminator} Second sentence goes well past the limit here.'
    assert truncate_at_sentence(text, 12) == f'First{terminator}'


def test_truncation_falls_back_to_a_word_boundary_when_there_is_no_sentence():
    text = 'a rambling reply with no punctuation at all anywhere in it'
    result = truncate_at_sentence(text, 20)
    assert len(result) <= 20
    assert not result.endswith(' ')
    assert 'rambl' in result


def test_truncation_leaves_a_short_reply_alone():
    assert truncate_at_sentence('Hi there.', 200) == 'Hi there.'


def test_the_schema_offers_only_the_enabled_names():
    schema = build_turn_schema(GESTURES, EMOJI)
    props = schema['properties']
    assert set(props) == {'reply', 'language', 'gesture', 'emoji', 'end'}
    assert schema['required'] == ['reply', 'language', 'gesture', 'emoji', 'end']
    assert schema['additionalProperties'] is False
    assert set(props['gesture']['enum']) == set(GESTURES) | {None}
    assert set(props['emoji']['enum']) == set(EMOJI) | {None}
    assert props['language']['enum'] == ['en', 'zh']
    assert props['end']['type'] == 'boolean'


def test_the_schema_still_permits_no_gesture_when_none_are_enabled():
    # A deployment can disable every gesture. The schema must stay valid.
    schema = build_turn_schema((), ())
    assert schema['properties']['gesture']['enum'] == [None]
    assert schema['properties']['emoji']['enum'] == [None]


def test_an_exchange_records_who_said_it():
    assert Exchange('person', 'hello').speaker == 'person'
    assert Exchange('robot', 'hi').text == 'hi'


def test_the_scripted_backend_returns_prepared_turns_and_records_its_input():
    turns = [Turn('one', 'en', None, None, False),
             Turn('two', 'en', 'wave', 'happy', True)]
    backend = ScriptedDialogueBackend(turns)
    first = backend.respond(base_frame=None, frame=None, scene=None,
                            venue=None, history=(), utterance=None,
                            language='en', child=False)
    assert first.reply == 'one'
    assert backend.calls == 1
    assert backend.last_call['language'] == 'en'
    assert backend.respond(None, None, None, None, (), None, 'en', False).end is True


def test_the_scripted_backend_can_be_made_to_fail_on_a_chosen_turn():
    from x2_greeter.cognition.dialogue import BackendUnavailable

    backend = ScriptedDialogueBackend([Turn('one', 'en', None, None, False)],
                                      fail_after=1)
    backend.respond(None, None, None, None, (), None, 'en', False)
    with pytest.raises(BackendUnavailable):
        backend.respond(None, None, None, None, (), None, 'en', False)
```

- [ ] **Step 2: Run it and watch it fail**

Run: `python -m pytest x2_greeter_ws/src/x2_greeter/test/cognition/test_dialogue.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'x2_greeter.cognition.dialogue'`

- [ ] **Step 3: Write `cognition/dialogue.py`**

Adjust the `BackendUnavailable` import to wherever Phase 1 already defines it; re-export it here so callers have one place to catch from.

```python
"""One turn of conversation: the contract, and the validation that enforces it.

A dialogue backend returns five fields. Four of them are advisory -- a
gesture name, an emoji name, a language tag, a stop flag -- and one, the
reply, is the whole point. So a malformed advisory field is dropped and the
turn continues, while a missing reply raises: a robot that waves at somebody
in silence is worse than one that does nothing.

This lives beside the port rather than inside the Claude adapter because
every backend gets the same treatment. Models invent gesture names, answer
'English' when asked for 'en', and write paragraphs when asked for a
sentence. That is not a property of one vendor.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping, NamedTuple, Optional, Protocol, Sequence

from x2_greeter.cognition.backend import BackendUnavailable   # re-exported
from x2_greeter.core.language import ALLOWED_LANGUAGES, normalise_language

__all__ = ['BackendUnavailable', 'DialogueBackend', 'Exchange', 'Turn',
           'TurnLimits', 'TurnRejected', 'build_turn_schema',
           'truncate_at_sentence', 'validate_turn']

# Both scripts' sentence terminators. Chinese full stops are not '.' and a
# reply that ends on one must not be cut mid-clause for want of an ASCII dot.
_SENTENCE_END = re.compile(r'[.!?。！？]')


class TurnRejected(Exception):
    """The backend returned something that is not a usable turn."""


@dataclass(frozen=True)
class Turn:
    reply: str
    language: str
    gesture: Optional[str]
    emoji: Optional[str]
    end: bool


class Exchange(NamedTuple):
    speaker: str        # 'person' or 'robot'
    text: str


@dataclass(frozen=True)
class TurnLimits:
    reply_max_chars: int = 200


class DialogueBackend(Protocol):
    def respond(self, base_frame, frame, scene, venue,
                history: Sequence[Exchange], utterance, language: str,
                child: bool) -> Turn:
        ...


def truncate_at_sentence(text: str, max_chars: int) -> str:
    """Cut to at most max_chars, preferring the last sentence that fits.

    Falling back to a word boundary, and only then to a hard cut. Mid-word
    truncation sounds like the robot was interrupted; a clean sentence just
    sounds brief.
    """
    text = str(text).strip()
    limit = int(max_chars)
    if limit <= 0 or len(text) <= limit:
        return text

    window = text[:limit]
    ends = [m.end() for m in _SENTENCE_END.finditer(window)]
    if ends:
        return window[:ends[-1]].strip()
    space = window.rfind(' ')
    if space > 0:
        return window[:space].strip()
    return window.strip()


def validate_turn(doc: Any, enabled_gestures: Sequence[str],
                  enabled_emoji: Sequence[str], limits: TurnLimits,
                  fallback_language: str) -> Turn:
    if not isinstance(doc, Mapping):
        raise TurnRejected(f'expected a JSON object, got {type(doc).__name__}')

    raw_reply = doc.get('reply')
    if not isinstance(raw_reply, str) or not raw_reply.strip():
        raise TurnRejected('turn has no usable reply text')

    language = (normalise_language(doc.get('language'))
                or normalise_language(fallback_language)
                or ALLOWED_LANGUAGES[0])

    gesture = doc.get('gesture')
    if gesture not in tuple(enabled_gestures):
        gesture = None
    emoji = doc.get('emoji')
    if emoji not in tuple(enabled_emoji):
        emoji = None

    end = doc.get('end')
    return Turn(
        reply=truncate_at_sentence(raw_reply, limits.reply_max_chars),
        language=language,
        gesture=gesture,
        emoji=emoji,
        end=end if isinstance(end, bool) else False,
    )


def build_turn_schema(enabled_gestures: Sequence[str],
                      enabled_emoji: Sequence[str]) -> dict:
    """The JSON schema handed to the model.

    The allowlists appear as enums so the model is steered towards a valid
    name, but validate_turn still checks -- a schema is a request, and the
    only thing that has ever stopped an invented gesture reaching the
    controller is the check on our side.
    """
    return {
        'type': 'object',
        'additionalProperties': False,
        'required': ['reply', 'language', 'gesture', 'emoji', 'end'],
        'properties': {
            'reply': {'type': 'string'},
            'language': {'type': 'string', 'enum': list(ALLOWED_LANGUAGES)},
            'gesture': {'enum': list(enabled_gestures) + [None]},
            'emoji': {'enum': list(enabled_emoji) + [None]},
            'end': {'type': 'boolean'},
        },
    }
```

- [ ] **Step 4: Add `ScriptedDialogueBackend` to `sim/fakes.py`**

```python
class ScriptedDialogueBackend:
    """Returns prepared turns and remembers what it was asked.

    fail_after=N raises BackendUnavailable from call N+1 onwards, which is how
    the conversation tests exercise the cloud-failure path without a network.
    """

    name = 'scripted'

    def __init__(self, turns, fail_after=None) -> None:
        self._turns = list(turns)
        self._fail_after = fail_after
        self.calls = 0
        self.last_call = None

    def respond(self, base_frame, frame, scene, venue, history, utterance,
                language, child):
        self.calls += 1
        self.last_call = {
            'base_frame': base_frame, 'frame': frame, 'scene': scene,
            'venue': venue, 'history': tuple(history), 'utterance': utterance,
            'language': language, 'child': child,
        }
        if self._fail_after is not None and self.calls > self._fail_after:
            raise BackendUnavailable('scripted failure')
        if not self._turns:
            raise BackendUnavailable('scripted backend exhausted')
        return self._turns.pop(0)
```

Add the import at the top of `sim/fakes.py`:

```python
from x2_greeter.cognition.dialogue import BackendUnavailable
```

- [ ] **Step 5: Run the tests**

Run: `python -m pytest x2_greeter_ws/src/x2_greeter/test/cognition/test_dialogue.py -v`
Expected: PASS (30 tests)

- [ ] **Step 6: Commit**

```bash
git add x2_greeter_ws/src/x2_greeter/x2_greeter/cognition/dialogue.py \
        x2_greeter_ws/src/x2_greeter/x2_greeter/sim/fakes.py \
        x2_greeter_ws/src/x2_greeter/test/cognition/test_dialogue.py
git commit -m "feat(cognition): the turn contract, its validation, and a scripted backend"
```

---

## Task 7: The Claude dialogue backend

**Files:**
- Modify: `x2_greeter_ws/src/x2_greeter/x2_greeter/cognition/claude.py` (add a second class; the Phase 1 `ClaudeBackend` is untouched)
- Test: `x2_greeter_ws/src/x2_greeter/test/cognition/test_claude_dialogue.py`

**Interfaces:**
- Consumes: `cognition.dialogue.{Turn, TurnLimits, TurnRejected, Exchange, build_turn_schema, validate_turn, BackendUnavailable}`, `core.scene.SceneSnapshot`, `core.venue.VenueProfile`, `cognition.transcriber.Utterance`.
- Produces:
  - `cognition.claude.DIALOGUE_SYSTEM_PROMPT: str`
  - `cognition.claude.ClaudeDialogueBackend(enabled_gestures, enabled_emoji, model='claude-opus-5', effort='low', timeout_s=6.0, limits=TurnLimits(), client=None, logger=None)` with `name = 'claude_dialogue'` and `.respond(...)` matching the `DialogueBackend` protocol.

**Two images per turn, one call.** The base frame is the wide 156° stereo view of the venue, captured once at session start; the per-turn frame is a fresh 94° head view. Sending both in one message is what lets the model say "the shelf behind you" without the robot having to remember anything between turns.

**Copy the Phase 1 call shape exactly.** `output_config={'effort': ..., 'format': {'type': 'json_schema', 'schema': ...}}`, `thinking={'type': 'adaptive'}`, **no `budget_tokens`** — Opus 5 returns a 400 for it. Read `_extract_json` in the same file and reuse its approach: adaptive thinking can emit a thinking block before the text block, so the first `type == 'text'` block is the one to parse.

**The exception ladder is not optional and its order matters** — most specific first, every branch ending in `BackendUnavailable`, and **no image or audio bytes in any message**: `APITimeoutError`, `NotFoundError`, `RateLimitError`, `AuthenticationError`, `APIStatusError`, `APIConnectionError`, bare `Exception`.

- [ ] **Step 1: Write the failing test**

Create `test/cognition/test_claude_dialogue.py`:

```python
"""The Claude dialogue backend. No network, ever.

The client is injected and every test here uses a stub. What is being tested
is the request we build and the way failures are named -- not Claude.
"""
import json

import pytest

from x2_greeter.cognition.claude import DIALOGUE_SYSTEM_PROMPT, ClaudeDialogueBackend
from x2_greeter.cognition.dialogue import BackendUnavailable, Exchange, TurnLimits
from x2_greeter.cognition.transcriber import Utterance
from x2_greeter.core.scene import AddressingMode, Person, SceneSnapshot
from x2_greeter.core.venue import parse_venue

GESTURES = ('wave', 'heart')
EMOJI = ('thinking', 'happy')

VENUE = parse_venue({'venue': {
    'kind': 'clothing_store',
    'role': 'a greeter in a clothing store',
    'language_default': 'en',
    'opening': {'en': 'Welcome in.', 'zh': '欢迎光临。'},
    'facts': ['The fitting rooms are at the back.'],
    'topics_encouraged': ['what they are shopping for'],
    'topics_forbidden': ['prices'],
    'deflect_to_human': 'A staff member can help with that.',
}})


class _Frame:
    def __init__(self, data=b'\x89PNG-fake', media_type='image/jpeg'):
        self.data = data
        self.media_type = media_type


class _Block:
    def __init__(self, type_, text=None):
        self.type = type_
        self.text = text


class _Response:
    def __init__(self, blocks):
        self.content = blocks


class _StubClient:
    def __init__(self, response=None, raises=None):
        self._response = response
        self._raises = raises
        self.options = None
        self.request = None
        self.messages = self

    def with_options(self, **kwargs):
        self.options = kwargs
        return self

    def create(self, **kwargs):
        self.request = kwargs
        if self._raises is not None:
            raise self._raises
        return self._response


def _ok_response(**overrides):
    doc = {'reply': 'Nice to meet you!', 'language': 'en', 'gesture': 'wave',
           'emoji': 'happy', 'end': False}
    doc.update(overrides)
    return _Response([_Block('thinking'), _Block('text', json.dumps(doc))])


def _scene(distance=1.4, child=False, count=1):
    person = Person(bbox=(0, 0, 10, 10), confidence=0.9, distance_m=distance,
                    center_offset=0.0, stature_m=1.0 if child else 1.7,
                    likely_child=child)
    people = tuple([person] * count)
    return SceneSnapshot(people=people, subject=person,
                         mode=AddressingMode.INDIVIDUAL, at_s=0.0)


def _backend(client, **kwargs):
    return ClaudeDialogueBackend(enabled_gestures=GESTURES, enabled_emoji=EMOJI,
                                 client=client, **kwargs)


def _respond(backend, **overrides):
    call = dict(base_frame=_Frame(b'base'), frame=_Frame(b'head'),
                scene=_scene(), venue=VENUE, history=(),
                utterance=Utterance('hi there', 'en', 0.95), language='en',
                child=False)
    call.update(overrides)
    return backend.respond(**call)


def test_a_good_response_becomes_a_validated_turn():
    client = _StubClient(_ok_response())
    turn = _respond(_backend(client))
    assert turn.reply == 'Nice to meet you!'
    assert turn.gesture == 'wave'
    assert turn.emoji == 'happy'
    assert turn.end is False


def test_both_frames_are_sent_in_one_call():
    client = _StubClient(_ok_response())
    _respond(_backend(client))
    blocks = client.request['messages'][0]['content']
    images = [b for b in blocks if b['type'] == 'image']
    assert len(images) == 2, (
        'the wide base frame and the fresh head frame go together: that pair '
        'is what lets the model say "the shelf behind you" without the robot '
        'remembering anything between turns')


def test_the_request_uses_the_opus_5_shape():
    client = _StubClient(_ok_response())
    _respond(_backend(client, model='claude-opus-5', effort='low', timeout_s=6.0))
    request = client.request
    assert request['model'] == 'claude-opus-5'
    assert request['thinking'] == {'type': 'adaptive'}
    assert 'budget_tokens' not in json.dumps(request), (
        'Opus 5 rejects budget_tokens with a 400')
    assert request['output_config']['effort'] == 'low'
    assert request['output_config']['format']['type'] == 'json_schema'
    assert client.options == {'timeout': 6.0, 'max_retries': 0}


def test_the_schema_sent_carries_only_the_enabled_names():
    client = _StubClient(_ok_response())
    _respond(_backend(client))
    schema = client.request['output_config']['format']['schema']
    assert set(schema['properties']['gesture']['enum']) == set(GESTURES) | {None}
    assert set(schema['properties']['emoji']['enum']) == set(EMOJI) | {None}


def test_the_venue_facts_and_forbidden_topics_reach_the_prompt():
    client = _StubClient(_ok_response())
    _respond(_backend(client))
    text = ' '.join(b.get('text', '') for b in client.request['messages'][0]['content'])
    assert 'The fitting rooms are at the back.' in text
    assert 'prices' in text


def test_the_conversation_history_reaches_the_prompt_in_order():
    client = _StubClient(_ok_response())
    _respond(_backend(client), history=(Exchange('person', 'do you have hats'),
                                        Exchange('robot', 'we do')))
    text = ' '.join(b.get('text', '') for b in client.request['messages'][0]['content'])
    assert text.index('do you have hats') < text.index('we do')


def test_child_mode_puts_the_child_rules_in_the_prompt():
    client = _StubClient(_ok_response())
    _respond(_backend(client), child=True)
    text = ' '.join(b.get('text', '') for b in client.request['messages'][0]['content'])
    lowered = text.lower()
    assert 'closer' in lowered, (
        'the never-invite-them-closer rule must be stated in the prompt, not '
        'only relied on downstream: the 1.0 m interlock silently cancels the '
        'gesture the robot just invited a child to come and see')
    assert 'promise' in lowered


def test_group_mode_is_stated_so_the_model_stops_saying_you():
    client = _StubClient(_ok_response())
    scene = _scene(distance=3.0, count=4)
    object.__setattr__(scene, 'mode', AddressingMode.GROUP)
    _respond(_backend(client), scene=scene)
    text = ' '.join(b.get('text', '') for b in client.request['messages'][0]['content'])
    assert 'group' in text.lower()


def test_a_response_with_no_text_block_is_unavailable_not_a_crash():
    client = _StubClient(_Response([_Block('thinking')]))
    with pytest.raises(BackendUnavailable):
        _respond(_backend(client))


def test_a_non_json_text_block_is_unavailable():
    client = _StubClient(_Response([_Block('text', 'sorry, I cannot do that')]))
    with pytest.raises(BackendUnavailable):
        _respond(_backend(client))


def test_a_json_object_with_no_reply_is_unavailable():
    client = _StubClient(_Response([_Block('text', json.dumps({'end': True}))]))
    with pytest.raises(BackendUnavailable):
        _respond(_backend(client))


def test_every_sdk_failure_arrives_as_backend_unavailable():
    # The names are checked against the installed SDK so a rename cannot
    # quietly turn a handled failure into an unhandled one.
    import anthropic

    for exc in [
        anthropic.APITimeoutError(request=None),
        anthropic.APIConnectionError(request=None),
        RuntimeError('something else entirely'),
    ]:
        with pytest.raises(BackendUnavailable):
            _respond(_backend(_StubClient(raises=exc)))


def test_no_image_bytes_appear_in_the_exception_or_the_log(caplog):
    import logging

    caplog.set_level(logging.DEBUG)
    client = _StubClient(raises=RuntimeError('boom'))
    backend = _backend(client, logger=logging.getLogger('x2'))
    with pytest.raises(BackendUnavailable) as exc:
        _respond(backend, frame=_Frame(b'\xca\xfe\xba\xbe' * 64))
    joined = str(exc.value) + ' '.join(r.getMessage() for r in caplog.records)
    assert 'cafe' not in joined.lower()
    assert 'yv6' not in joined  # a fragment of the base64 of that payload


def test_a_long_reply_comes_back_truncated():
    client = _StubClient(_ok_response(reply='A sentence. ' * 40))
    turn = _respond(_backend(client, limits=TurnLimits(reply_max_chars=200)))
    assert len(turn.reply) <= 200
```

- [ ] **Step 2: Run it and watch it fail**

Run: `python -m pytest x2_greeter_ws/src/x2_greeter/test/cognition/test_claude_dialogue.py -v`
Expected: FAIL — `ImportError: cannot import name 'DIALOGUE_SYSTEM_PROMPT'`

- [ ] **Step 3: Add the backend to `cognition/claude.py`**

Append to the existing file. Do not modify `SYSTEM_PROMPT`, `build_schema` or `ClaudeBackend` — Phase 1 still uses them.

```python
DIALOGUE_SYSTEM_PROMPT = """You are the voice of a humanoid robot standing in a \
public place, talking with someone who has stopped in front of you.

You will be given two photographs: a wide view of the place you are standing \
in, taken when this conversation began, and a fresh close view of the person \
you are talking to right now. You will also be given what they just said.

How to reply:
- One or two sentences. You are speaking out loud, not writing.
- Reply in the language named in the request, and in that language only.
- React to what you can actually see. Describe what people are doing, not who \
they are. Never guess at anyone's name, job, nationality, or age, and never \
say anything about a specific person's body or appearance.
- The venue facts you are given are the only things you may state as fact \
about this place. If asked anything else about it, use the deflection line.
- Never discuss the forbidden topics, even if asked directly.
- Ask a question back roughly every other turn. This is a conversation.
- Set end to true when the person is clearly finished -- they have said \
goodbye, thanked you and turned away, or stopped answering.

You may also choose one gesture and one facial expression from the lists in \
the schema, or null for either. Choose only names from those lists.

You cannot walk, and you must never say that you will. You cannot fetch \
anything, hold anything, or take anyone anywhere."""

CHILD_RULES = """You are talking with a child. Additional rules, all of them \
absolute:
- Keep it short, warm and simple.
- Never ask for any personal information: no name, no age, no school, no \
address, nothing about their family or where their parent is.
- Never promise anything, including that you will still be here later.
- Never tell them to come closer, to follow you, to reach out, or to touch \
you. This is the important one: you stop gesturing when anyone is within \
arm's reach, so inviting a child closer means inviting them to a robot that \
then goes still."""


class ClaudeDialogueBackend:
    name = 'claude_dialogue'

    def __init__(self, enabled_gestures, enabled_emoji,
                 model: str = 'claude-opus-5', effort: str = 'low',
                 timeout_s: float = 6.0, limits=None, client=None,
                 logger=None) -> None:
        self._gestures = tuple(enabled_gestures)
        self._emoji = tuple(enabled_emoji)
        self._model = model
        self._effort = effort
        self._timeout_s = float(timeout_s)
        self._limits = limits if limits is not None else TurnLimits()
        self._logger = logger
        self._schema = build_turn_schema(self._gestures, self._emoji)
        self._client = client if client is not None else anthropic.Anthropic()

    def respond(self, base_frame, frame, scene, venue, history, utterance,
                language, child) -> Turn:
        prompt = self._build_prompt(scene, venue, history, utterance, language,
                                    child)
        content = []
        for image in (base_frame, frame):
            if image is None:
                continue
            content.append({'type': 'image', 'source': {
                'type': 'base64', 'media_type': image.media_type,
                'data': base64.standard_b64encode(image.data).decode('ascii')}})
        content.append({'type': 'text', 'text': prompt})

        try:
            response = self._client.with_options(
                timeout=self._timeout_s, max_retries=0
            ).messages.create(
                model=self._model,
                max_tokens=4096,
                system=DIALOGUE_SYSTEM_PROMPT,
                thinking={'type': 'adaptive'},
                output_config={
                    'effort': self._effort,
                    'format': {'type': 'json_schema', 'schema': self._schema},
                },
                messages=[{'role': 'user', 'content': content}],
            )
        except anthropic.APITimeoutError as exc:
            raise self._unavailable('timed out', exc)
        except anthropic.NotFoundError as exc:
            raise self._unavailable('model not found', exc)
        except anthropic.RateLimitError as exc:
            raise self._unavailable('rate limited', exc)
        except anthropic.AuthenticationError as exc:
            raise self._unavailable('authentication failed', exc)
        except anthropic.APIStatusError as exc:
            raise self._unavailable(f'api error {exc.status_code}', exc)
        except anthropic.APIConnectionError as exc:
            raise self._unavailable('connection failed', exc)
        except Exception as exc:                  # noqa: BLE001 - one name upstream
            raise self._unavailable('unexpected failure', exc)

        try:
            doc = json.loads(_extract_json(response))
        except Exception as exc:                  # noqa: BLE001
            raise self._unavailable('response was not usable JSON', exc)

        try:
            return validate_turn(doc, self._gestures, self._emoji,
                                 self._limits, language)
        except TurnRejected as exc:
            raise self._unavailable('turn failed validation', exc)

    def _unavailable(self, what: str, exc: Exception) -> BackendUnavailable:
        # The exception type and our own words. Never the request, never the
        # frame, never anything derived from either.
        message = f'{what}: {type(exc).__name__}'
        if self._logger is not None:
            self._logger.warning(f'dialogue backend unavailable, {message}')
        return BackendUnavailable(message)

    def _build_prompt(self, scene, venue, history, utterance, language,
                      child) -> str:
        parts = [venue.to_prompt(), '']
        if scene is not None:
            crowd = ('You are talking to one person.'
                     if scene.mode is AddressingMode.INDIVIDUAL
                     else f'You are addressing a group of about '
                          f'{scene.person_count} people. Speak to all of them, '
                          f'not to any one of them.')
            parts.append(crowd)
        if child:
            parts += ['', CHILD_RULES]
        if history:
            parts += ['', 'The conversation so far:']
            parts += [f'{"Them" if e.speaker == "person" else "You"}: {e.text}'
                      for e in history]
        said = getattr(utterance, 'text', '') or ''
        parts += ['', f'They just said: "{said}"',
                  '', f'Reply in {language}.']
        return '\n'.join(parts)
```

Add to the imports at the top of the file:

```python
from x2_greeter.cognition.dialogue import (
    BackendUnavailable, Turn, TurnLimits, TurnRejected, build_turn_schema,
    validate_turn)
from x2_greeter.core.scene import AddressingMode
```

- [ ] **Step 4: Run the tests**

Run: `python -m pytest x2_greeter_ws/src/x2_greeter/test/cognition/test_claude_dialogue.py -v`
Expected: PASS (14 tests)

- [ ] **Step 5: Confirm Phase 1 still works and nothing calls the network**

Run: `python -m pytest`
Expected: PASS, including the Phase 1 `ClaudeBackend` tests. If any test is slow enough to suggest a real HTTP call, that is a bug — every client in this suite is injected.

- [ ] **Step 6: Commit**

```bash
git add x2_greeter_ws/src/x2_greeter/x2_greeter/cognition/claude.py \
        x2_greeter_ws/src/x2_greeter/test/cognition/test_claude_dialogue.py
git commit -m "feat(cognition): Claude dialogue backend with the base and per-turn frames"
```

---

## Task 8: The conversation state machine

**Files:**
- Create: `x2_greeter_ws/src/x2_greeter/x2_greeter/core/conversation.py`
- Test: `x2_greeter_ws/src/x2_greeter/test/core/test_conversation.py`

**Interfaces:**
- Consumes: `core.language.{LanguagePolicy, DEFAULT_LANGUAGE}`, `core.scene.{SceneSnapshot, AddressingMode}`, `core.venue.VenueProfile`, `cognition.dialogue.{Turn, Exchange}`, `cognition.transcriber.Utterance`.
- Produces:
  - `core.conversation.SessionState(str, enum.Enum)` — `IDLE`, `GREETING`, `LISTENING`, `THINKING`, `SPEAKING`, `CLOSING`, `COOLDOWN`
  - `core.conversation.CloseReason(str, enum.Enum)` — `MODEL_ENDED`, `MAX_TURNS`, `SILENCE`, `SESSION_TIMEOUT`, `BACKEND_FAILED`, `PERSON_GONE`, `ABORTED`
  - `core.conversation.ConversationLimits` frozen dataclass: `max_turns=8`, `silence_timeout_s=8.0`, `session_max_s=180.0`, `cooldown_s=20.0`, `backend_failures_max=2`, `history_max=12`
  - `core.conversation.Conversation(venue, limits=ConversationLimits(), language_policy=LanguagePolicy(), clock_epoch_s=0.0)` with methods `start(scene, at_s) -> str`, `greeted(at_s)`, `heard(utterance, at_s)`, `replied(turn, at_s)`, `finished_speaking(at_s)`, `observed(scene, at_s)`, `backend_failed(at_s) -> bool`, `tick(at_s)`, `close(reason, at_s)`, `cooldown_active(at_s) -> bool`, `reset()`; and read-only properties `state`, `language`, `turns_taken`, `mode`, `subject`, `history`, `close_reason`, `active`.

**This is the object that owns every rule about when the robot talks and when it stops.** The node below it is a wiring harness; the backend above it produces sentences. Every timeout, turn cap, failure budget and cooldown lives here, in a class with no ROS import, no clock of its own, and no I/O — which is the only reason the whole of §12's behaviour can be tested in milliseconds.

**Time is a parameter, never `time.time()`.** Every method that cares about time takes `at_s` from the caller. The node passes the ROS clock; the tests pass integers. A module that reads its own clock cannot be tested for a three-minute session timeout without waiting three minutes, and so in practice never gets tested for it at all.

**Addressing is locked at `start()` and never revisited** — the user's decision, verbatim: 只要交互一开始就不需要改变策略. A robot that switches from "you" to "everyone" mid-conversation because a fourth person wandered past reads as broken, not as attentive.

- [ ] **Step 1: Write the failing test**

Create `test/core/test_conversation.py`:

```python
"""The conversation state machine: when the robot talks, and when it stops.

Every number in ConversationLimits is a decision about how a machine
behaves towards a person standing in front of it, so each one gets a test
that would fail if somebody widened it. The session cap is not a resource
limit -- it is the promise that the robot lets go.
"""
import pytest

from x2_greeter.cognition.dialogue import Exchange, Turn
from x2_greeter.cognition.transcriber import Utterance
from x2_greeter.core.conversation import (
    CloseReason, Conversation, ConversationLimits, SessionState)
from x2_greeter.core.scene import AddressingMode, Person, SceneSnapshot
from x2_greeter.core.venue import parse_venue

VENUE = parse_venue({'venue': {
    'kind': 'clothing_store',
    'role': 'a greeter in a clothing store',
    'language_default': 'en',
    'opening': {'en': 'Welcome in.', 'zh': '欢迎光临。'},
    'facts': ['The fitting rooms are at the back.'],
    'topics_encouraged': ['what they are shopping for'],
    'topics_forbidden': ['prices'],
    'deflect_to_human': 'A staff member can help with that.',
}})


def _person(distance=1.4, offset=0.0, child=False):
    return Person(bbox=(0, 0, 10, 10), confidence=0.9, distance_m=distance,
                  center_offset=offset, stature_m=1.0 if child else 1.7,
                  likely_child=child)


def _scene(*people, mode=AddressingMode.INDIVIDUAL, at_s=0.0):
    people = people or (_person(),)
    subject = people[0] if mode is AddressingMode.INDIVIDUAL else None
    return SceneSnapshot(people=people, subject=subject, mode=mode, at_s=at_s)


def _empty_scene(at_s=0.0):
    return SceneSnapshot(people=(), subject=None,
                         mode=AddressingMode.GROUP, at_s=at_s)


def _turn(reply='Sure.', language='en', end=False):
    return Turn(reply=reply, language=language, gesture=None, emoji=None,
                end=end)


def _started(limits=None, at_s=0.0, scene=None):
    convo = Conversation(venue=VENUE, limits=limits or ConversationLimits())
    convo.start(scene if scene is not None else _scene(), at_s=at_s)
    return convo


def _through_one_turn(convo, t0=1.0, turn=None):
    """greeted -> heard -> replied -> finished_speaking, in order."""
    convo.greeted(at_s=t0)
    convo.heard(Utterance('hello', 'en', 0.9), at_s=t0 + 1)
    convo.replied(turn or _turn(), at_s=t0 + 2)
    convo.finished_speaking(at_s=t0 + 3)
    return convo


# --- opening --------------------------------------------------------------

def test_a_fresh_conversation_is_idle():
    convo = Conversation(venue=VENUE)
    assert convo.state is SessionState.IDLE
    assert convo.active is False
    assert convo.turns_taken == 0


def test_start_returns_the_venue_opening_and_enters_greeting():
    convo = Conversation(venue=VENUE)
    assert convo.start(_scene(), at_s=0.0) == 'Welcome in.'
    assert convo.state is SessionState.GREETING


def test_the_opening_is_in_the_venue_default_language():
    convo = Conversation(venue=VENUE)
    convo.start(_scene(), at_s=0.0)
    assert convo.language == 'en'


def test_starting_twice_is_refused():
    convo = _started()
    with pytest.raises(RuntimeError):
        convo.start(_scene(), at_s=1.0)


# --- addressing is locked -------------------------------------------------

def test_addressing_is_decided_once_at_the_start():
    convo = _started(scene=_scene(_person(1.2), _person(1.6)))
    assert convo.mode is AddressingMode.INDIVIDUAL
    assert convo.subject is not None


def test_a_crowd_arriving_mid_conversation_does_not_change_the_mode():
    # 只要交互一开始就不需要改变策略. Switching from 'you' to 'everyone' halfway
    # through reads as broken, not as attentive.
    convo = _through_one_turn(_started(scene=_scene(_person(1.2))))
    convo.observed(_scene(*[_person(3.5)] * 6, mode=AddressingMode.GROUP),
                   at_s=10.0)
    assert convo.mode is AddressingMode.INDIVIDUAL


def test_group_mode_locks_just_as_hard():
    convo = _started(scene=_scene(*[_person(3.2)] * 4,
                                  mode=AddressingMode.GROUP))
    assert convo.mode is AddressingMode.GROUP
    convo.observed(_scene(_person(1.0)), at_s=5.0)
    assert convo.mode is AddressingMode.GROUP


# --- the turn cycle -------------------------------------------------------

def test_the_states_walk_greeting_listening_thinking_speaking_listening():
    convo = _started()
    convo.greeted(at_s=1.0)
    assert convo.state is SessionState.LISTENING
    convo.heard(Utterance('hello', 'en', 0.9), at_s=2.0)
    assert convo.state is SessionState.THINKING
    convo.replied(_turn(), at_s=3.0)
    assert convo.state is SessionState.SPEAKING
    convo.finished_speaking(at_s=4.0)
    assert convo.state is SessionState.LISTENING


def test_a_completed_turn_is_counted_once():
    convo = _through_one_turn(_started())
    assert convo.turns_taken == 1


def test_history_records_both_sides_in_order():
    convo = _through_one_turn(_started())
    assert convo.history == (Exchange('person', 'hello'),
                             Exchange('robot', 'Sure.'))


def test_history_is_trimmed_to_the_limit_keeping_the_most_recent():
    convo = _started(limits=ConversationLimits(history_max=4, max_turns=99))
    for i in range(6):
        convo.greeted(at_s=0.0) if i == 0 else None
        convo.heard(Utterance(f'q{i}', 'en', 0.9), at_s=10.0 + i)
        convo.replied(_turn(reply=f'a{i}'), at_s=10.5 + i)
        convo.finished_speaking(at_s=11.0 + i)
    assert len(convo.history) == 4
    assert convo.history[-1].text == 'a5'


def test_hearing_out_of_turn_is_ignored_not_a_crash():
    # A late transcription can land while the robot is already speaking.
    convo = _started()
    convo.greeted(at_s=1.0)
    convo.heard(Utterance('hello', 'en', 0.9), at_s=2.0)
    convo.replied(_turn(), at_s=3.0)
    convo.heard(Utterance('and another thing', 'en', 0.9), at_s=3.5)
    assert convo.state is SessionState.SPEAKING
    assert [e.text for e in convo.history] == ['hello', 'Sure.']


def test_an_empty_utterance_does_not_start_a_turn():
    convo = _started()
    convo.greeted(at_s=1.0)
    convo.heard(Utterance('   ', 'en', 0.0), at_s=2.0)
    assert convo.state is SessionState.LISTENING
    assert convo.history == ()


# --- language -------------------------------------------------------------

def test_a_confident_language_switch_is_followed():
    convo = _started()
    convo.greeted(at_s=1.0)
    convo.heard(Utterance('你好', 'zh', 0.95), at_s=2.0)
    assert convo.language == 'zh'


def test_an_unconfident_detection_does_not_flip_the_language():
    convo = _started()
    convo.greeted(at_s=1.0)
    convo.heard(Utterance('hello 你好', 'zh', 0.3), at_s=2.0)
    assert convo.language == 'en'


def test_the_language_the_model_replied_in_becomes_the_current_language():
    convo = _started()
    convo.greeted(at_s=1.0)
    convo.heard(Utterance('你好', 'zh', 0.95), at_s=2.0)
    convo.replied(_turn(reply='你好！', language='zh'), at_s=3.0)
    assert convo.language == 'zh'


# --- closing --------------------------------------------------------------

def test_the_model_ending_the_turn_closes_after_the_speech_finishes():
    convo = _started()
    convo.greeted(at_s=1.0)
    convo.heard(Utterance('bye', 'en', 0.9), at_s=2.0)
    convo.replied(_turn(reply='See you!', end=True), at_s=3.0)
    assert convo.state is SessionState.SPEAKING, (
        'the goodbye still has to be spoken before the session ends')
    convo.finished_speaking(at_s=4.0)
    assert convo.state is SessionState.COOLDOWN
    assert convo.close_reason is CloseReason.MODEL_ENDED


def test_the_turn_cap_closes_the_session():
    convo = _started(limits=ConversationLimits(max_turns=2))
    convo.greeted(at_s=1.0)
    for i in range(2):
        convo.heard(Utterance(f'q{i}', 'en', 0.9), at_s=10.0 + i)
        convo.replied(_turn(), at_s=10.5 + i)
        convo.finished_speaking(at_s=11.0 + i)
    assert convo.state is SessionState.COOLDOWN
    assert convo.close_reason is CloseReason.MAX_TURNS


def test_silence_while_listening_closes_the_session():
    convo = _through_one_turn(_started(limits=ConversationLimits(
        silence_timeout_s=8.0)))
    convo.tick(at_s=4.0 + 7.9)
    assert convo.state is SessionState.LISTENING
    convo.tick(at_s=4.0 + 8.1)
    assert convo.close_reason is CloseReason.SILENCE


def test_silence_is_measured_from_the_last_thing_that_happened():
    convo = _started(limits=ConversationLimits(silence_timeout_s=8.0))
    convo.greeted(at_s=1.0)
    convo.tick(at_s=8.5)
    assert convo.state is SessionState.LISTENING, 'the clock restarts on each event'
    convo.heard(Utterance('hi', 'en', 0.9), at_s=8.6)
    convo.replied(_turn(), at_s=8.7)
    convo.finished_speaking(at_s=8.8)
    convo.tick(at_s=16.0)
    assert convo.state is SessionState.LISTENING
    convo.tick(at_s=17.0)
    assert convo.close_reason is CloseReason.SILENCE


def test_the_thinking_state_is_not_cut_short_by_the_silence_timeout():
    # A slow backend is not the person going quiet.
    convo = _started(limits=ConversationLimits(silence_timeout_s=1.0))
    convo.greeted(at_s=1.0)
    convo.heard(Utterance('hi', 'en', 0.9), at_s=2.0)
    convo.tick(at_s=30.0)
    assert convo.state is SessionState.THINKING


def test_the_session_cap_closes_even_a_lively_conversation():
    limits = ConversationLimits(session_max_s=180.0, max_turns=99,
                                silence_timeout_s=999.0)
    convo = _started(limits=limits, at_s=100.0)
    convo.greeted(at_s=101.0)
    convo.heard(Utterance('hi', 'en', 0.9), at_s=270.0)
    convo.tick(at_s=279.9)
    assert convo.state is SessionState.THINKING
    convo.tick(at_s=280.1)
    assert convo.close_reason is CloseReason.SESSION_TIMEOUT, (
        'the cap is a promise that the robot lets go, not a resource limit')


def test_the_person_walking_away_closes_the_session():
    convo = _through_one_turn(_started())
    convo.observed(_empty_scene(at_s=10.0), at_s=10.0)
    assert convo.close_reason is CloseReason.PERSON_GONE


def test_an_empty_scene_while_speaking_does_not_cut_the_sentence():
    convo = _started()
    convo.greeted(at_s=1.0)
    convo.heard(Utterance('hi', 'en', 0.9), at_s=2.0)
    convo.replied(_turn(), at_s=3.0)
    convo.observed(_empty_scene(at_s=3.5), at_s=3.5)
    assert convo.state is SessionState.SPEAKING
    convo.finished_speaking(at_s=4.0)
    assert convo.close_reason is CloseReason.PERSON_GONE


def test_close_can_be_called_directly_and_is_idempotent():
    convo = _through_one_turn(_started())
    convo.close(CloseReason.ABORTED, at_s=10.0)
    convo.close(CloseReason.SILENCE, at_s=11.0)
    assert convo.close_reason is CloseReason.ABORTED, 'the first reason wins'


# --- backend failures -----------------------------------------------------

def test_one_backend_failure_returns_to_listening():
    convo = _started(limits=ConversationLimits(backend_failures_max=2))
    convo.greeted(at_s=1.0)
    convo.heard(Utterance('hi', 'en', 0.9), at_s=2.0)
    assert convo.backend_failed(at_s=3.0) is False
    assert convo.state is SessionState.LISTENING


def test_the_failure_budget_closes_the_session_when_it_runs_out():
    convo = _started(limits=ConversationLimits(backend_failures_max=2))
    convo.greeted(at_s=1.0)
    for i in range(2):
        convo.heard(Utterance(f'q{i}', 'en', 0.9), at_s=10.0 + i)
        fatal = convo.backend_failed(at_s=10.5 + i)
    assert fatal is True
    assert convo.close_reason is CloseReason.BACKEND_FAILED


def test_a_successful_turn_forgives_the_earlier_failures():
    convo = _started(limits=ConversationLimits(backend_failures_max=2))
    convo.greeted(at_s=1.0)
    convo.heard(Utterance('q', 'en', 0.9), at_s=2.0)
    convo.backend_failed(at_s=3.0)
    convo.heard(Utterance('q again', 'en', 0.9), at_s=4.0)
    convo.replied(_turn(), at_s=5.0)
    convo.finished_speaking(at_s=6.0)
    convo.heard(Utterance('q3', 'en', 0.9), at_s=7.0)
    assert convo.backend_failed(at_s=8.0) is False, (
        'a working turn means the network came back; the budget resets')


# --- cooldown -------------------------------------------------------------

def test_cooldown_runs_from_the_close_and_then_expires():
    convo = _through_one_turn(_started(limits=ConversationLimits(
        cooldown_s=20.0)))
    convo.close(CloseReason.SILENCE, at_s=10.0)
    assert convo.cooldown_active(at_s=29.9) is True
    assert convo.cooldown_active(at_s=30.1) is False


def test_a_conversation_that_never_started_is_not_in_cooldown():
    assert Conversation(venue=VENUE).cooldown_active(at_s=0.0) is False


def test_reset_returns_to_idle_and_clears_the_session():
    convo = _through_one_turn(_started())
    convo.close(CloseReason.SILENCE, at_s=10.0)
    convo.reset()
    assert convo.state is SessionState.IDLE
    assert convo.turns_taken == 0
    assert convo.history == ()
    assert convo.close_reason is None
    assert convo.subject is None
```

- [ ] **Step 2: Run it and watch it fail**

Run: `python -m pytest x2_greeter_ws/src/x2_greeter/test/core/test_conversation.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'x2_greeter.core.conversation'`

- [ ] **Step 3: Write `core/conversation.py`**

```python
"""One conversation, from the opening line to the cooldown.

Everything about when the robot talks and when it stops lives here: the
turn cap, the silence timeout, the session cap, the failure budget, the
cooldown. Nothing here imports ROS, reads a clock, or performs I/O, which
is why a three-minute session timeout can be tested in microseconds.

Time arrives as a parameter on every method that cares about it. A module
that calls time.time() itself cannot have its timeouts tested without
waiting for them, and so in practice never has them tested at all.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Optional, Sequence, Tuple

from x2_greeter.cognition.dialogue import Exchange, Turn
from x2_greeter.core.language import DEFAULT_LANGUAGE, LanguagePolicy
from x2_greeter.core.scene import AddressingMode, SceneSnapshot
from x2_greeter.core.venue import VenueProfile


class SessionState(str, enum.Enum):
    IDLE = 'idle'
    GREETING = 'greeting'        # the opening line is being spoken
    LISTENING = 'listening'      # waiting for the person to say something
    THINKING = 'thinking'        # the backend has the turn
    SPEAKING = 'speaking'        # the reply is being spoken
    CLOSING = 'closing'
    COOLDOWN = 'cooldown'        # closed; will not re-greet yet


class CloseReason(str, enum.Enum):
    MODEL_ENDED = 'model_ended'
    MAX_TURNS = 'max_turns'
    SILENCE = 'silence'
    SESSION_TIMEOUT = 'session_timeout'
    BACKEND_FAILED = 'backend_failed'
    PERSON_GONE = 'person_gone'
    ABORTED = 'aborted'


@dataclass(frozen=True)
class ConversationLimits:
    max_turns: int = 8
    silence_timeout_s: float = 8.0
    session_max_s: float = 180.0
    cooldown_s: float = 20.0
    backend_failures_max: int = 2
    history_max: int = 12


# States in which nothing is being spoken and nobody is being waited on, so
# a close can take effect immediately.
_INTERRUPTIBLE = (SessionState.GREETING, SessionState.LISTENING,
                  SessionState.THINKING)


class Conversation:
    def __init__(self, venue: VenueProfile,
                 limits: ConversationLimits = ConversationLimits(),
                 language_policy: LanguagePolicy = LanguagePolicy()) -> None:
        self._venue = venue
        self._limits = limits
        self._policy = language_policy
        self.reset()

    # -- lifecycle --------------------------------------------------------

    def reset(self) -> None:
        self._state = SessionState.IDLE
        self._language = DEFAULT_LANGUAGE
        self._mode: Optional[AddressingMode] = None
        self._subject = None
        self._history: list = []
        self._turns = 0
        self._failures = 0
        self._started_at = 0.0
        self._last_event_at = 0.0
        self._closed_at = 0.0
        self._close_reason: Optional[CloseReason] = None
        self._pending_close: Optional[CloseReason] = None

    def start(self, scene: SceneSnapshot, at_s: float) -> str:
        if self._state is not SessionState.IDLE:
            raise RuntimeError(f'cannot start from {self._state.value}')
        # Addressing is decided once, here, and never revisited.
        self._mode = scene.mode
        self._subject = scene.subject
        self._language = self._venue.language_default or self._policy.default
        self._started_at = float(at_s)
        self._last_event_at = float(at_s)
        self._state = SessionState.GREETING
        return self._venue.opening_for(self._language)

    def greeted(self, at_s: float) -> None:
        """The opening line has finished playing."""
        if self._state is not SessionState.GREETING:
            return
        self._last_event_at = float(at_s)
        self._to_listening_or_close(at_s)

    def heard(self, utterance, at_s: float) -> None:
        if self._state is not SessionState.LISTENING:
            return                      # a late transcription; not a turn
        text = (getattr(utterance, 'text', '') or '').strip()
        if not text:
            self._last_event_at = float(at_s)
            return
        self._language = self._policy.next_language(
            self._language, getattr(utterance, 'language', None),
            getattr(utterance, 'confidence', 0.0))
        self._append('person', text)
        self._last_event_at = float(at_s)
        self._state = SessionState.THINKING

    def replied(self, turn: Turn, at_s: float) -> None:
        if self._state is not SessionState.THINKING:
            return
        self._language = turn.language or self._language
        self._append('robot', turn.reply)
        self._turns += 1
        self._failures = 0              # the backend answered; budget resets
        self._last_event_at = float(at_s)
        self._state = SessionState.SPEAKING
        if turn.end:
            self._pending_close = CloseReason.MODEL_ENDED
        elif self._turns >= self._limits.max_turns:
            self._pending_close = CloseReason.MAX_TURNS

    def finished_speaking(self, at_s: float) -> None:
        if self._state is not SessionState.SPEAKING:
            return
        self._last_event_at = float(at_s)
        self._to_listening_or_close(at_s)

    def backend_failed(self, at_s: float) -> bool:
        """Returns True when this failure ended the session."""
        if self._state is not SessionState.THINKING:
            return False
        self._failures += 1
        self._last_event_at = float(at_s)
        if self._failures >= self._limits.backend_failures_max:
            self.close(CloseReason.BACKEND_FAILED, at_s)
            return True
        self._state = SessionState.LISTENING
        return False

    def observed(self, scene: SceneSnapshot, at_s: float) -> None:
        """A fresh look at the room. Only presence matters -- the addressing
        decision was made at start() and stays made."""
        if not self.active:
            return
        if scene.person_count == 0:
            self.close(CloseReason.PERSON_GONE, at_s)

    def tick(self, at_s: float) -> None:
        if not self.active:
            return
        if float(at_s) - self._started_at > self._limits.session_max_s:
            self.close(CloseReason.SESSION_TIMEOUT, at_s)
            return
        if (self._state is SessionState.LISTENING
                and float(at_s) - self._last_event_at
                > self._limits.silence_timeout_s):
            self.close(CloseReason.SILENCE, at_s)

    def close(self, reason: CloseReason, at_s: float) -> None:
        if self._close_reason is not None:
            return                      # the first reason wins
        if self._state in (SessionState.SPEAKING,):
            # Never cut a sentence in half. The close lands when the words
            # have finished.
            self._pending_close = self._pending_close or reason
            return
        self._commit_close(reason, at_s)

    def cooldown_active(self, at_s: float) -> bool:
        if self._close_reason is None:
            return False
        return float(at_s) - self._closed_at < self._limits.cooldown_s

    # -- properties -------------------------------------------------------

    @property
    def state(self) -> SessionState:
        return self._state

    @property
    def active(self) -> bool:
        return self._state in _INTERRUPTIBLE + (SessionState.SPEAKING,)

    @property
    def language(self) -> str:
        return self._language

    @property
    def turns_taken(self) -> int:
        return self._turns

    @property
    def mode(self) -> Optional[AddressingMode]:
        return self._mode

    @property
    def subject(self):
        return self._subject

    @property
    def history(self) -> Tuple[Exchange, ...]:
        return tuple(self._history)

    @property
    def close_reason(self) -> Optional[CloseReason]:
        return self._close_reason

    # -- internals --------------------------------------------------------

    def _append(self, speaker: str, text: str) -> None:
        self._history.append(Exchange(speaker, text))
        excess = len(self._history) - self._limits.history_max
        if excess > 0:
            del self._history[:excess]

    def _to_listening_or_close(self, at_s: float) -> None:
        if self._pending_close is not None:
            self._commit_close(self._pending_close, at_s)
        else:
            self._state = SessionState.LISTENING

    def _commit_close(self, reason: CloseReason, at_s: float) -> None:
        self._close_reason = reason
        self._closed_at = float(at_s)
        self._state = SessionState.COOLDOWN
```

- [ ] **Step 4: Run the tests**

Run: `python -m pytest x2_greeter_ws/src/x2_greeter/test/core/test_conversation.py -v`
Expected: PASS (28 tests)

- [ ] **Step 5: Prove the timeout tests are load-bearing**

The whole point of this module is that its limits are testable. Confirm they actually are, with two mutations, reverting each immediately:

1. In `tick`, change `> self._limits.silence_timeout_s` to `> self._limits.silence_timeout_s * 10`.
   Expected: `test_silence_while_listening_closes_the_session` and
   `test_silence_is_measured_from_the_last_thing_that_happened` FAIL.
2. In `close`, delete the `SessionState.SPEAKING` guard so a close always commits immediately.
   Expected: `test_an_empty_scene_while_speaking_does_not_cut_the_sentence` and
   `test_the_model_ending_the_turn_closes_after_the_speech_finishes` FAIL.

If either mutation leaves the suite green, the test is decorative — fix the test before moving on.

- [ ] **Step 6: Run the whole host suite**

Run: `python -m pytest`
Expected: PASS. `test_layering.py` must still pass — `core/conversation.py` imports only from `core/` and `cognition/`, never `ros/`.

- [ ] **Step 7: Commit**

```bash
git add x2_greeter_ws/src/x2_greeter/x2_greeter/core/conversation.py \
        x2_greeter_ws/src/x2_greeter/test/core/test_conversation.py
git commit -m "feat(core): conversation state machine with turn, silence and session limits"
```

---

## Task 9: Hearing — the audio source adapter and the audio privacy tripwire

**Files:**
- Create: `x2_greeter_ws/src/x2_greeter/x2_greeter/ros/audio_source.py`
- Modify: `x2_greeter_ws/src/x2_greeter/x2_greeter/cognition/transcriber.py` (add the `AudioSource` port)
- Modify: `x2_greeter_ws/src/x2_greeter/test/test_privacy.py` (extend the tripwire to audio)
- Test: `x2_greeter_ws/src/x2_greeter/test/ros/test_audio_source.py`

**Interfaces:**
- Consumes: `aimdk_msgs.msg.ProcessedAudioOutput`, `aimdk_msgs.msg.AudioVadStateType`, `rclpy`.
- Produces:
  - `cognition.transcriber.AudioSource` — `typing.Protocol` with `start()`, `stop()`, and the constructor-injected `on_utterance(pcm: bytes, at_s: float) -> None` callback contract.
  - `ros.audio_source.VendorAudioSource(node, on_utterance, topic='/aima/interaction/audio/processed', max_utterance_s=15.0, qos_depth=20, callback_group=None)` with `.utterances_seen`, `.bytes_buffered`, `.start()`, `.stop()`, `.destroy()`.

**The port lives beside `Transcriber` because they are two halves of one boundary** — bytes in, text out. This phase's implementation consumes the vendor's already-segmented `ProcessedAudioOutput` stream, which is what `only_voice` mode plus the vendor wake word produces. The later phase that subscribes to raw audio and runs our own VAD, AEC and denoising swaps the implementation behind this same port and changes nothing above it. That was the user's decision: 先跑 only_voice 和唤醒，成熟了再来做原始音频的订阅和 VAD，包括降噪这些.

**Segmentation belongs to the vendor here, not to us.** `AudioVadStateType` gives `BEGIN`(0x01) → `PROCESSING`(0x02) → `END`(0x03). One utterance is everything between a BEGIN and its END on the same `stream_id`. A `PROCESSING` chunk with no BEGIN before it is a session we joined mid-utterance: drop it rather than transcribe half a sentence.

**Audio is under the same rule as images.** PCM lives in memory for the length of one utterance and is then dropped. No `.wav` file, no cache directory, no PCM in a log line at any level, no transcript written to disk. The tripwire in `test/test_privacy.py` is extended to cover it — and, as with the image tripwire, it tests itself: a test that scans for a pattern and finds nothing proves nothing unless it can be shown to catch a real violation.

- [ ] **Step 1: Write the failing test**

Create `test/ros/test_audio_source.py`:

```python
"""The vendor audio source: VAD segments in, one utterance out.

Marked `ros` -- it needs aimdk_msgs and runs in the container.
"""
import pytest

pytestmark = pytest.mark.ros

from aimdk_msgs.msg import AudioVadStateType, ProcessedAudioOutput  # noqa: E402

from x2_greeter.ros.audio_source import VendorAudioSource  # noqa: E402


class _FakeSub:
    def __init__(self):
        self.destroyed = False


class _FakeNode:
    """Just enough node to hold a subscription and a clock."""

    def __init__(self):
        self.callback = None
        self.subscription = None
        self.logs = []

    def create_subscription(self, msg_type, topic, callback, qos, **kwargs):
        self.callback = callback
        self.topic = topic
        self.msg_type = msg_type
        self.subscription = _FakeSub()
        return self.subscription

    def destroy_subscription(self, sub):
        sub.destroyed = True

    def get_logger(self):
        node = self

        class _Logger:
            def info(self, msg): node.logs.append(msg)
            def warn(self, msg): node.logs.append(msg)
            def warning(self, msg): node.logs.append(msg)
            def error(self, msg): node.logs.append(msg)
            def debug(self, msg): node.logs.append(msg)
        return _Logger()


def _chunk(state, data=b'', stream_id=1):
    msg = ProcessedAudioOutput()
    msg.stream_id = stream_id
    msg.vad_state.state = state
    msg.audio_data = list(data)
    return msg


@pytest.fixture
def wired():
    node = _FakeNode()
    got = []
    source = VendorAudioSource(node=node, on_utterance=lambda pcm, at_s:
                               got.append((pcm, at_s)))
    source.start()
    return node, source, got


def test_it_subscribes_to_the_processed_audio_topic(wired):
    node, source, _ = wired
    assert node.topic == '/aima/interaction/audio/processed'
    assert node.msg_type is ProcessedAudioOutput


def test_a_complete_utterance_is_delivered_once_as_joined_pcm(wired):
    node, source, got = wired
    node.callback(_chunk(AudioVadStateType.BEGIN, b'ab'))
    node.callback(_chunk(AudioVadStateType.PROCESSING, b'cd'))
    node.callback(_chunk(AudioVadStateType.END, b'ef'))
    assert [pcm for pcm, _ in got] == [b'abcdef']
    assert source.utterances_seen == 1


def test_the_buffer_is_empty_again_after_delivery(wired):
    node, source, _ = wired
    node.callback(_chunk(AudioVadStateType.BEGIN, b'ab'))
    node.callback(_chunk(AudioVadStateType.END, b'cd'))
    assert source.bytes_buffered == 0, (
        'audio lives for one utterance and is then gone')


def test_a_second_utterance_does_not_carry_the_first_one_forward(wired):
    node, source, got = wired
    for payload in (b'one', b'two'):
        node.callback(_chunk(AudioVadStateType.BEGIN, payload))
        node.callback(_chunk(AudioVadStateType.END, b''))
    assert [pcm for pcm, _ in got] == [b'one', b'two']


def test_audio_arriving_before_any_begin_is_dropped(wired):
    node, source, got = wired
    node.callback(_chunk(AudioVadStateType.PROCESSING, b'half a sentence'))
    node.callback(_chunk(AudioVadStateType.END, b''))
    assert got == [], 'we joined mid-utterance; half a sentence is worse than none'


def test_an_empty_utterance_is_not_delivered(wired):
    node, source, got = wired
    node.callback(_chunk(AudioVadStateType.BEGIN, b''))
    node.callback(_chunk(AudioVadStateType.END, b''))
    assert got == []


def test_a_new_begin_abandons_an_unfinished_utterance(wired):
    node, source, got = wired
    node.callback(_chunk(AudioVadStateType.BEGIN, b'abandoned'))
    node.callback(_chunk(AudioVadStateType.BEGIN, b'kept'))
    node.callback(_chunk(AudioVadStateType.END, b''))
    assert [pcm for pcm, _ in got] == [b'kept']


def test_a_second_stream_id_replaces_the_first(wired):
    # Two overlapping streams means the vendor moved on; follow it rather
    # than interleaving two people's speech into one buffer.
    node, source, got = wired
    node.callback(_chunk(AudioVadStateType.BEGIN, b'aaa', stream_id=1))
    node.callback(_chunk(AudioVadStateType.BEGIN, b'bbb', stream_id=2))
    node.callback(_chunk(AudioVadStateType.END, b'', stream_id=2))
    assert [pcm for pcm, _ in got] == [b'bbb']


def test_an_over_long_utterance_is_abandoned_rather_than_grown_forever():
    node = _FakeNode()
    got = []
    # 16 kHz * 2 bytes * 0.5 s = 16000 bytes
    source = VendorAudioSource(node=node, on_utterance=lambda p, t: got.append(p),
                               max_utterance_s=0.5)
    source.start()
    node.callback(_chunk(AudioVadStateType.BEGIN, b'\x00' * 20000))
    node.callback(_chunk(AudioVadStateType.END, b''))
    assert got == []
    assert source.bytes_buffered == 0


def test_stop_drops_whatever_is_buffered(wired):
    node, source, got = wired
    node.callback(_chunk(AudioVadStateType.BEGIN, b'partial'))
    source.stop()
    assert source.bytes_buffered == 0
    node.callback(_chunk(AudioVadStateType.END, b''))
    assert got == [], 'a stopped source delivers nothing'


def test_start_after_stop_works_again(wired):
    node, source, got = wired
    source.stop()
    source.start()
    node.callback(_chunk(AudioVadStateType.BEGIN, b'hi'))
    node.callback(_chunk(AudioVadStateType.END, b''))
    assert [pcm for pcm, _ in got] == [b'hi']


def test_destroy_releases_the_subscription(wired):
    node, source, _ = wired
    sub = node.subscription
    source.destroy()
    assert sub.destroyed is True


def test_no_audio_bytes_reach_the_log(wired):
    node, source, _ = wired
    node.callback(_chunk(AudioVadStateType.BEGIN, b'\xde\xad\xbe\xef' * 100))
    node.callback(_chunk(AudioVadStateType.END, b''))
    joined = ' '.join(node.logs)
    assert 'dead' not in joined.lower()
    assert '\xde' not in joined
    assert 'deadbeef' not in joined.lower()


def test_a_callback_that_raises_does_not_kill_the_subscription(wired):
    node, source, _ = wired

    def _boom(pcm, at_s):
        raise RuntimeError('downstream exploded')

    source._on_utterance = _boom
    node.callback(_chunk(AudioVadStateType.BEGIN, b'hi'))
    node.callback(_chunk(AudioVadStateType.END, b''))
    # Still alive, still segmenting.
    source._on_utterance = lambda pcm, at_s: None
    node.callback(_chunk(AudioVadStateType.BEGIN, b'again'))
    node.callback(_chunk(AudioVadStateType.END, b''))
    assert source.utterances_seen == 2
```

- [ ] **Step 2: Run it in the container and watch it fail**

Run: `docker/run_tests.sh` (which runs `python3 -m pytest src/x2_greeter/test -m ros -v -p no:cacheprovider -p no:launch_testing -p no:launch_ros`)
Expected: FAIL — `ModuleNotFoundError: No module named 'x2_greeter.ros.audio_source'`

- [ ] **Step 3: Add the `AudioSource` port to `cognition/transcriber.py`**

Append to the existing file:

```python
class AudioSource(Protocol):
    """Segmented speech, one utterance at a time.

    The implementation shipping in this phase reads the vendor's already
    segmented stream, which is what only_voice mode plus the vendor wake
    word produces. A later phase subscribes to raw audio and runs its own
    VAD, AEC and denoising behind this same port, and nothing above it
    changes.

    Implementations take their on_utterance(pcm: bytes, at_s: float)
    callback at construction time.
    """

    def start(self) -> None:
        ...

    def stop(self) -> None:
        ...
```

- [ ] **Step 4: Write `ros/audio_source.py`**

```python
"""The vendor audio source: VAD-segmented speech off a ROS topic.

One utterance is everything between a BEGIN and its matching END. Audio
lives in memory for exactly that long and is then dropped -- never written
to disk, never logged, not at any level. That rule is the same one the
images live under, and it is enforced by test/test_privacy.py.
"""
from __future__ import annotations

from typing import Callable, Optional

from aimdk_msgs.msg import AudioVadStateType, ProcessedAudioOutput
from rclpy.qos import QoSProfile, ReliabilityPolicy

from x2_greeter.cognition.transcriber import SAMPLE_RATE_HZ

BYTES_PER_SAMPLE = 2                    # 16-bit mono PCM, S16LE
DEFAULT_TOPIC = '/aima/interaction/audio/processed'

UtteranceCallback = Callable[[bytes, float], None]


class VendorAudioSource:
    def __init__(self, node, on_utterance: UtteranceCallback,
                 topic: str = DEFAULT_TOPIC, max_utterance_s: float = 15.0,
                 qos_depth: int = 20, callback_group=None) -> None:
        self._node = node
        self._on_utterance = on_utterance
        self._max_bytes = int(max_utterance_s * SAMPLE_RATE_HZ
                              * BYTES_PER_SAMPLE)
        self._buffer = bytearray()
        self._stream_id: Optional[int] = None
        self._running = False
        self.utterances_seen = 0

        qos = QoSProfile(depth=qos_depth)
        qos.reliability = ReliabilityPolicy.RELIABLE
        kwargs = {}
        if callback_group is not None:
            kwargs['callback_group'] = callback_group
        self._sub = node.create_subscription(
            ProcessedAudioOutput, topic, self._on_chunk, qos, **kwargs)

    # -- lifecycle --------------------------------------------------------

    def start(self) -> None:
        self._discard()
        self._running = True

    def stop(self) -> None:
        self._running = False
        self._discard()

    def destroy(self) -> None:
        self.stop()
        self._node.destroy_subscription(self._sub)

    @property
    def bytes_buffered(self) -> int:
        return len(self._buffer)

    # -- internals --------------------------------------------------------

    def _discard(self) -> None:
        self._buffer = bytearray()
        self._stream_id = None

    def _on_chunk(self, msg: ProcessedAudioOutput) -> None:
        if not self._running:
            return
        state = msg.vad_state.state
        payload = bytes(bytearray(msg.audio_data))

        if state == AudioVadStateType.BEGIN:
            # A new BEGIN supersedes anything unfinished: either the vendor
            # gave up on the last utterance or a different stream took over.
            self._discard()
            self._stream_id = msg.stream_id
            self._append(payload)
            return

        if self._stream_id is None:
            return                      # joined mid-utterance; drop it
        if msg.stream_id != self._stream_id:
            return                      # a stream we are not following

        self._append(payload)
        if state == AudioVadStateType.END:
            self._emit()

    def _append(self, payload: bytes) -> None:
        if len(self._buffer) + len(payload) > self._max_bytes:
            # Somebody is monologuing, or END never came. Neither is a turn.
            self._node.get_logger().warn(
                'utterance exceeded the length limit, discarding')
            self._discard()
            return
        self._buffer.extend(payload)

    def _emit(self) -> None:
        pcm = bytes(self._buffer)
        self._discard()
        if not pcm:
            return
        self.utterances_seen += 1
        try:
            self._on_utterance(pcm, self._now())
        except Exception as exc:        # noqa: BLE001
            # A downstream failure must not take the subscription with it.
            # The exception type only -- never the audio.
            self._node.get_logger().error(
                f'utterance handler failed: {type(exc).__name__}')

    def _now(self) -> float:
        clock = getattr(self._node, 'get_clock', None)
        if clock is None:
            return 0.0
        return clock().now().nanoseconds / 1e9
```

- [ ] **Step 5: Extend the privacy tripwire to audio**

Read `test/test_privacy.py` first — it already scans the package source for image-writing calls and proves itself by injecting a violation into a temporary copy. Extend the same machinery; do not write a second, parallel scanner.

Add to the existing forbidden-call patterns:

```python
AUDIO_WRITE_PATTERNS = (
    r'\bwave\.open\b',
    r'\bsoundfile\.write\b',
    r'\bsf\.write\b',
    r'\bscipy\.io\.wavfile\.write\b',
    r'\.wav[\'"]',            # any literal .wav path
    r'\bsd\.rec\b',
)
```

and add these tests:

```python
def test_no_module_writes_audio_to_disk():
    offenders = _scan(PACKAGE_ROOT, AUDIO_WRITE_PATTERNS)
    assert offenders == [], (
        f'audio is under the same rule as images: {offenders}')


def test_the_audio_scanner_catches_a_real_violation(tmp_path):
    # The scanner is worthless unless it can be shown to fire. Plant one.
    planted = tmp_path / 'leak.py'
    planted.write_text("import wave\nwave.open('/tmp/utterance.wav', 'wb')\n")
    assert _scan(tmp_path, AUDIO_WRITE_PATTERNS) != []


def test_no_module_logs_raw_pcm():
    # Formatting a bytes buffer into a log line is how audio escapes without
    # anybody deciding to save it.
    offenders = _scan(PACKAGE_ROOT, (
        r'logger\(\)\.[a-z]+\([^)]*\bpcm\b',
        r'logger\(\)\.[a-z]+\([^)]*audio_data',
        r'logger\(\)\.[a-z]+\([^)]*\baudio\b[^)]*\{',
    ))
    assert offenders == [], f'raw audio must never reach a log line: {offenders}'


def test_no_module_persists_a_transcript():
    offenders = _scan(PACKAGE_ROOT, (
        r'open\([^)]*transcript',
        r'transcript[^\n]*\.write\(',
    ))
    assert offenders == [], f'transcripts are not written down: {offenders}'
```

If `_scan` in the existing file has a different name or signature, use the one that is there. The point is one scanner, two subject areas.

- [ ] **Step 6: Run both suites**

Run: `python -m pytest` (host — the privacy tests are host tests)
Expected: PASS

Run: `docker/run_tests.sh`
Expected: PASS, including 14 new audio-source tests.

- [ ] **Step 7: Commit**

```bash
git add x2_greeter_ws/src/x2_greeter/x2_greeter/ros/audio_source.py \
        x2_greeter_ws/src/x2_greeter/x2_greeter/cognition/transcriber.py \
        x2_greeter_ws/src/x2_greeter/test/ros/test_audio_source.py \
        x2_greeter_ws/src/x2_greeter/test/test_privacy.py
git commit -m "feat(ros): VAD-segmented audio source, and the privacy tripwire extended to audio"
```

---

## Task 10: Agent mode and the environment camera

**Files:**
- Create: `x2_greeter_ws/src/x2_greeter/x2_greeter/ros/agent_mode.py`
- Create: `x2_greeter_ws/src/x2_greeter/x2_greeter/ros/env_camera.py`
- Test: `x2_greeter_ws/src/x2_greeter/test/ros/test_agent_mode.py`
- Test: `x2_greeter_ws/src/x2_greeter/test/ros/test_env_camera.py`

**Interfaces:**
- Consumes: `aimdk_msgs.srv.SetAgentPropertiesRequest`, `sensor_msgs.msg.Image`, `rclpy`, `ros.frame_source.rotate_180`.
- Produces:
  - `ros.agent_mode.AgentMode(node, service='/aima/interaction/agent/set_properties', timeout_s=2.0, attempts=8, retry_s=0.25)` with `.set_only_voice() -> bool` and `.available -> bool`
  - `ros.env_camera.EnvCamera(node, topic, on_frame, rotate_180=False, callback_group=None)` with `.capture_next()`, `.frames_seen`, `.destroy()`

**Two small subscribers batched into one task** because neither carries enough logic to deserve its own review gate, and they are both "one vendor interface, one adapter, one test file".

### `only_voice` cannot be read back

The interaction service directory contains exactly four services:

```
GetMicSourceRequest.srv  PlayTts.srv  SetAgentPropertiesRequest.srv  SetMicSourceRequest.srv
```

**There is no `GetAgentProperties`.** So the node can set `only_voice` and check the response status, but can never confirm the mode is still in force later. That is why §12 treats it as a deployment step verified indirectly: if the vendor agent were still running its own dialogue, you would hear it answer over us — that is the check, and it happens with a human standing there, not in software.

The consequence for this adapter: `set_only_voice()` returns a bool and the node logs it loudly at startup. It does not retry forever, and it does not pretend a timeout is a success.

### Confirm the request field before writing the adapter

- [ ] **Step 1: Read the service definition**

Run, in the container:

```bash
cat /opt/aimdk/share/aimdk_msgs/srv/SetAgentPropertiesRequest.srv
ros2 interface show aimdk_msgs/srv/SetAgentPropertiesRequest
```

Write down the exact request field names and the response field name. **The vendor misspells the response field as `reponse` in at least four services** (`PlayAudioFile`, `RequestAudioFocus`, `AbandonAudioFocus`, `GetAllJointState`) — check which spelling this one uses and use that one. Do not assume; the whole recurring lesson of this project is that a vendor claim is grounded in the source, not in a description of it.

Use the names you just read in Steps 2 and 3 below. The code shape does not change — only the identifiers do.

- [ ] **Step 2: Write the failing tests**

Create `test/ros/test_agent_mode.py`:

```python
"""Setting only_voice, and being honest about whether it worked."""
import pytest

pytestmark = pytest.mark.ros

from aimdk_msgs.srv import SetAgentPropertiesRequest  # noqa: E402

from x2_greeter.ros.agent_mode import AgentMode  # noqa: E402


class _FakeFuture:
    def __init__(self, result=None):
        self._result = result

    def result(self):
        return self._result


class _FakeClient:
    def __init__(self, ready_after=0, results=None):
        self.ready_after = ready_after
        self.wait_calls = 0
        self.sent = []
        self._results = list(results or [])

    def wait_for_service(self, timeout_sec=None):
        self.wait_calls += 1
        return self.wait_calls > self.ready_after

    def call_async(self, request):
        self.sent.append(request)
        return _FakeFuture(self._results.pop(0) if self._results else None)


class _FakeNode:
    def __init__(self, client):
        self.client = client
        self.logs = []

    def create_client(self, srv_type, name, **kwargs):
        self.srv_type = srv_type
        self.service_name = name
        return self.client

    def get_logger(self):
        node = self

        class _L:
            def info(self, m): node.logs.append(m)
            def warn(self, m): node.logs.append(m)
            def warning(self, m): node.logs.append(m)
            def error(self, m): node.logs.append(m)
        return _L()


def _ok_response():
    response = SetAgentPropertiesRequest.Response()
    # Use the response field name confirmed in Step 1. If it is the
    # misspelled 'reponse', use that.
    response.header.code = 0
    return response


def _spin_ok(node, future, timeout_sec=None):
    return None


def test_it_targets_the_documented_service():
    client = _FakeClient(results=[_ok_response()])
    node = _FakeNode(client)
    AgentMode(node=node, spin_until=_spin_ok)
    assert node.service_name == '/aima/interaction/agent/set_properties'
    assert node.srv_type is SetAgentPropertiesRequest


def test_a_successful_call_reports_true():
    client = _FakeClient(results=[_ok_response()])
    mode = AgentMode(node=_FakeNode(client), spin_until=_spin_ok)
    assert mode.set_only_voice() is True
    assert len(client.sent) == 1


def test_a_service_that_never_appears_reports_false_and_does_not_hang():
    client = _FakeClient(ready_after=999)
    mode = AgentMode(node=_FakeNode(client), attempts=3, retry_s=0.0,
                     spin_until=_spin_ok)
    assert mode.set_only_voice() is False
    assert client.wait_calls == 3, 'bounded retries, matching the vendor examples'


def test_a_timeout_is_not_reported_as_success():
    client = _FakeClient(results=[None])
    mode = AgentMode(node=_FakeNode(client), spin_until=_spin_ok)
    assert mode.set_only_voice() is False


def test_a_nonzero_header_code_is_a_failure():
    response = _ok_response()
    response.header.code = 7
    mode = AgentMode(node=_FakeNode(_FakeClient(results=[response])),
                     spin_until=_spin_ok)
    assert mode.set_only_voice() is False


def test_a_failure_is_logged_loudly_because_nothing_can_check_it_later():
    node = _FakeNode(_FakeClient(ready_after=999))
    AgentMode(node=node, attempts=1, retry_s=0.0, spin_until=_spin_ok
              ).set_only_voice()
    joined = ' '.join(node.logs).lower()
    assert 'only_voice' in joined
    assert 'vendor' in joined or 'agent' in joined
```

Create `test/ros/test_env_camera.py`:

```python
"""The wide base frame, captured once per session on request."""
import numpy as np
import pytest

pytestmark = pytest.mark.ros

from sensor_msgs.msg import Image  # noqa: E402

from x2_greeter.ros.env_camera import EnvCamera  # noqa: E402


def _image(value=7, height=4, width=6):
    msg = Image()
    msg.height = height
    msg.width = width
    msg.encoding = 'bgr8'
    msg.step = width * 3
    msg.data = (np.full((height, width, 3), value, dtype=np.uint8)
                .tobytes())
    return msg


class _FakeNode:
    def __init__(self):
        self.callback = None
        self.logs = []

    def create_subscription(self, msg_type, topic, callback, qos, **kwargs):
        self.callback = callback
        self.topic = topic
        return object()

    def destroy_subscription(self, sub):
        self.destroyed = True

    def get_clock(self):
        class _C:
            def now(self):
                class _T:
                    nanoseconds = 0
                return _T()
        return _C()

    def get_logger(self):
        node = self

        class _L:
            def info(self, m): node.logs.append(m)
            def warn(self, m): node.logs.append(m)
            def warning(self, m): node.logs.append(m)
            def error(self, m): node.logs.append(m)
        return _L()


def test_nothing_is_delivered_until_a_capture_is_requested():
    node = _FakeNode()
    got = []
    EnvCamera(node=node, topic='/env', on_frame=lambda img, t: got.append(img))
    node.callback(_image())
    assert got == [], (
        'the base frame is captured once per session, not every frame -- '
        'holding every wide frame would cost memory for nothing')


def test_capture_next_takes_exactly_one_frame():
    node = _FakeNode()
    got = []
    camera = EnvCamera(node=node, topic='/env',
                       on_frame=lambda img, t: got.append(img))
    camera.capture_next()
    node.callback(_image(value=3))
    node.callback(_image(value=9))
    assert len(got) == 1
    assert int(got[0][0, 0, 0]) == 3


def test_the_delivered_frame_is_a_real_bgr_array():
    node = _FakeNode()
    got = []
    camera = EnvCamera(node=node, topic='/env',
                       on_frame=lambda img, t: got.append(img))
    camera.capture_next()
    node.callback(_image(height=4, width=6))
    assert got[0].shape == (4, 6, 3)
    assert got[0].dtype == np.uint8


def test_rotation_is_applied_when_the_camera_is_mounted_upside_down():
    node = _FakeNode()
    got = []
    camera = EnvCamera(node=node, topic='/env', rotate_180=True,
                       on_frame=lambda img, t: got.append(img))
    camera.capture_next()
    msg = _image(height=2, width=2)
    msg.data = np.array([[[1, 1, 1], [2, 2, 2]],
                         [[3, 3, 3], [4, 4, 4]]], dtype=np.uint8).tobytes()
    node.callback(msg)
    assert int(got[0][0, 0, 0]) == 4


def test_frames_seen_counts_everything_including_the_ones_dropped():
    node = _FakeNode()
    camera = EnvCamera(node=node, topic='/env', on_frame=lambda img, t: None)
    for _ in range(3):
        node.callback(_image())
    assert camera.frames_seen == 3


def test_a_malformed_frame_is_dropped_without_taking_the_node_down():
    node = _FakeNode()
    got = []
    camera = EnvCamera(node=node, topic='/env',
                       on_frame=lambda img, t: got.append(img))
    camera.capture_next()
    bad = _image()
    bad.data = b'\x00\x00'          # far too short for its declared shape
    node.callback(bad)
    assert got == []
    assert camera.pending is True, 'still waiting for a usable frame'
```

- [ ] **Step 3: Run them and watch them fail**

Run: `docker/run_tests.sh`
Expected: FAIL — no module named `x2_greeter.ros.agent_mode`

- [ ] **Step 4: Write `ros/agent_mode.py`**

Substitute the field names confirmed in Step 1 where marked.

```python
"""Putting the vendor agent into only_voice mode.

There is no GetAgentProperties service -- the interaction srv directory
holds exactly GetMicSourceRequest, PlayTts, SetAgentPropertiesRequest and
SetMicSourceRequest. So this can be set and its response checked, and then
never confirmed again. If the vendor agent is still running its own
dialogue you will hear it answer over us; that is the real check, and it
happens with a human standing there.
"""
from __future__ import annotations

import time
from typing import Callable, Optional

import rclpy
from aimdk_msgs.srv import SetAgentPropertiesRequest

SERVICE = '/aima/interaction/agent/set_properties'


def _default_spin(node, future, timeout_sec=None):
    rclpy.spin_until_future_complete(node, future, timeout_sec=timeout_sec)


class AgentMode:
    def __init__(self, node, service: str = SERVICE, timeout_s: float = 2.0,
                 attempts: int = 8, retry_s: float = 0.25,
                 spin_until: Optional[Callable] = None,
                 callback_group=None) -> None:
        self._node = node
        self._timeout_s = float(timeout_s)
        self._attempts = int(attempts)
        self._retry_s = float(retry_s)
        self._spin = spin_until or _default_spin
        self.available = False
        kwargs = {}
        if callback_group is not None:
            kwargs['callback_group'] = callback_group
        self._client = node.create_client(SetAgentPropertiesRequest, service,
                                          **kwargs)

    def set_only_voice(self) -> bool:
        log = self._node.get_logger()
        # Cross-host services are unreliable here; the vendor's own examples
        # retry 8 times at 0.25 s, so we do the same.
        for attempt in range(self._attempts):
            if self._client.wait_for_service(timeout_sec=self._retry_s):
                break
            if self._retry_s:
                time.sleep(self._retry_s)
        else:
            log.error(
                'could not reach the vendor agent service; only_voice was '
                'NOT set. The vendor agent may answer over us.')
            return False

        request = SetAgentPropertiesRequest.Request()
        # Field name confirmed in Step 1 from the .srv itself.
        request.only_voice = True

        future = self._client.call_async(request)
        self._spin(self._node, future, timeout_sec=self._timeout_s)
        response = future.result()
        if response is None:
            log.error('setting only_voice timed out; the vendor agent mode '
                      'is unknown and cannot be read back')
            return False

        # Response field name (and its spelling) confirmed in Step 1.
        code = getattr(response, 'header', None)
        code = getattr(code, 'code', 0) if code is not None else 0
        if code != 0:
            log.error(f'the vendor agent refused only_voice, code {code}')
            return False

        log.info('vendor agent set to only_voice')
        self.available = True
        return True
```

- [ ] **Step 5: Write `ros/env_camera.py`**

```python
"""The wide base frame: one photograph of the room, per session.

Held only until it is handed to the caller. Like every other image in this
package it is never written to disk and never logged.
"""
from __future__ import annotations

from typing import Callable, Optional

import numpy as np
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import Image

from x2_greeter.ros.frame_source import rotate_180 as _rotate_180

FrameCallback = Callable[[np.ndarray, float], None]


class EnvCamera:
    def __init__(self, node, topic: str, on_frame: FrameCallback,
                 rotate_180: bool = False, callback_group=None) -> None:
        self._node = node
        self._on_frame = on_frame
        self._rotate = bool(rotate_180)
        self._pending = False
        self.frames_seen = 0
        kwargs = {}
        if callback_group is not None:
            kwargs['callback_group'] = callback_group
        self._sub = node.create_subscription(
            Image, topic, self._on_image, qos_profile_sensor_data, **kwargs)

    @property
    def pending(self) -> bool:
        return self._pending

    def capture_next(self) -> None:
        """Take the next usable frame that arrives, and only that one."""
        self._pending = True

    def destroy(self) -> None:
        self._node.destroy_subscription(self._sub)

    def _on_image(self, msg: Image) -> None:
        self.frames_seen += 1
        if not self._pending:
            return
        image = self._decode(msg)
        if image is None:
            return                      # stay pending; try the next one
        self._pending = False
        if self._rotate:
            image = _rotate_180(image)
        try:
            self._on_frame(image, self._now())
        except Exception as exc:        # noqa: BLE001
            self._node.get_logger().error(
                f'base-frame handler failed: {type(exc).__name__}')

    def _decode(self, msg: Image) -> Optional[np.ndarray]:
        expected = msg.height * msg.width * 3
        buffer = np.frombuffer(bytes(msg.data), dtype=np.uint8)
        if buffer.size != expected:
            # Never log the buffer -- shapes only.
            self._node.get_logger().warn(
                f'dropping a {buffer.size}-byte frame that declares '
                f'{msg.height}x{msg.width}x3')
            return None
        return buffer.reshape((msg.height, msg.width, 3))
```

- [ ] **Step 6: Run the container suite**

Run: `docker/run_tests.sh`
Expected: PASS (12 new tests)

- [ ] **Step 7: Commit**

```bash
git add x2_greeter_ws/src/x2_greeter/x2_greeter/ros/agent_mode.py \
        x2_greeter_ws/src/x2_greeter/x2_greeter/ros/env_camera.py \
        x2_greeter_ws/src/x2_greeter/test/ros/test_agent_mode.py \
        x2_greeter_ws/src/x2_greeter/test/ros/test_env_camera.py
git commit -m "feat(ros): only_voice agent mode and the wide base-frame camera"
```

---

## Task 11: The face

**Files:**
- Create: `x2_greeter_ws/src/x2_greeter/x2_greeter/ros/face.py`
- Test: `x2_greeter_ws/src/x2_greeter/test/ros/test_face.py`

**Interfaces:**
- Consumes: `aimdk_msgs.srv.PlayEmoji`, `core.faces.{CATALOGUE, EmojiSpec, MODE_ONCE, MODE_LOOP, THINKING}`, `rclpy`.
- Produces: `ros.face.Face(node, service='/aima/interaction/emoji/play', enabled=False, timeout_s=1.0, attempts=4, retry_s=0.25, spin_until=None, callback_group=None)` with `.show(name, mode=MODE_ONCE) -> bool`, `.show_thinking() -> bool`, `.clear() -> bool`, `.available -> bool`, `.last_shown`.

**This ships with `enabled: false`.** Nothing about the emoji path has run on hardware. The flag is flipped on site, which is the arrangement the user asked for: 在现场再做调整.

**The face is fire-and-forget and never blocks a reply.** `show()` returns a bool that the caller is free to ignore. A failed expression is not a failed turn — the words are the turn. Every failure path returns `False` and logs once; none of them raises.

**Why the thinking face matters more than the others.** §13's only latency mitigation is showing `EMOTION_EYE_THINKING` at t≈0.05 s, long before the reply arrives at t≈1.5–3 s. Without it the robot stands motionless for two seconds after somebody speaks, which reads as broken rather than as thoughtful. `show_thinking()` exists as its own method so that path is one call with no name lookup and no branching.

- [ ] **Step 1: Read the service definition**

Run, in the container:

```bash
cat /opt/aimdk/share/aimdk_msgs/srv/PlayEmoji.srv
ros2 interface show aimdk_msgs/srv/PlayEmoji
ros2 service list | grep -i emoji
```

Note three things: the exact request field names, the response field name **and its spelling** (the vendor writes `reponse` in at least four other services), and the real service path — the default below is the documented one, and if the running system advertises something different, that is the value that goes in the config in Task 13.

The emotion constants are declared in this same `.srv` as service constants — `EMOTION_EYE_THINKING = 170` and the rest — which Task 3 already pins `core/faces.py` against. Use the names you read here in Steps 3 and 4 below; the code shape does not change, only the identifiers.

- [ ] **Step 2: Write the failing test**

Create `test/ros/test_face.py`:

```python
"""The face adapter. A failed expression is never a failed turn."""
import pytest

pytestmark = pytest.mark.ros

from aimdk_msgs.srv import PlayEmoji  # noqa: E402

from x2_greeter.core.faces import CATALOGUE, MODE_LOOP, MODE_ONCE  # noqa: E402
from x2_greeter.ros.face import Face  # noqa: E402


class _FakeFuture:
    def __init__(self, result):
        self._result = result

    def result(self):
        return self._result


class _FakeClient:
    def __init__(self, ready=True, results=None, raises=None):
        self.ready = ready
        self.sent = []
        self.raises = raises
        self._results = list(results or [])

    def wait_for_service(self, timeout_sec=None):
        return self.ready

    def call_async(self, request):
        if self.raises is not None:
            raise self.raises
        self.sent.append(request)
        return _FakeFuture(self._results.pop(0) if self._results else None)


class _FakeNode:
    def __init__(self, client):
        self.client = client
        self.logs = []

    def create_client(self, srv_type, name, **kwargs):
        self.srv_type = srv_type
        self.service_name = name
        return self.client

    def get_logger(self):
        node = self

        class _L:
            def info(self, m): node.logs.append(m)
            def warn(self, m): node.logs.append(m)
            def warning(self, m): node.logs.append(m)
            def error(self, m): node.logs.append(m)
            def debug(self, m): node.logs.append(m)
        return _L()


def _ok():
    response = PlayEmoji.Response()
    response.header.code = 0
    return response


def _spin(node, future, timeout_sec=None):
    return None


def _face(client, **kwargs):
    kwargs.setdefault('enabled', True)
    return Face(node=_FakeNode(client), spin_until=_spin, **kwargs)


def test_a_disabled_face_sends_nothing_and_says_so():
    client = _FakeClient(results=[_ok()])
    face = Face(node=_FakeNode(client), enabled=False, spin_until=_spin)
    assert face.show('happy') is False
    assert client.sent == [], 'disabled means no service call at all'


def test_showing_an_expression_sends_the_catalogued_id():
    client = _FakeClient(results=[_ok()])
    assert _face(client).show('happy') is True
    assert client.sent[0].emotion_id == CATALOGUE['happy'].emotion_id


def test_the_default_mode_is_once_not_loop():
    client = _FakeClient(results=[_ok()])
    _face(client).show('happy')
    assert client.sent[0].mode == MODE_ONCE, (
        'a looping expression outlives the sentence that prompted it')


def test_loop_mode_is_available_when_asked_for():
    client = _FakeClient(results=[_ok()])
    _face(client).show('calm', mode=MODE_LOOP)
    assert client.sent[0].mode == MODE_LOOP


def test_the_thinking_face_has_its_own_one_call_path():
    client = _FakeClient(results=[_ok()])
    face = _face(client)
    assert face.show_thinking() is True
    assert client.sent[0].emotion_id == CATALOGUE['thinking'].emotion_id


def test_an_unknown_expression_name_is_refused_without_a_call():
    client = _FakeClient(results=[_ok()])
    assert _face(client).show('smouldering') is False
    assert client.sent == []


def test_the_last_shown_expression_is_remembered():
    client = _FakeClient(results=[_ok(), _ok()])
    face = _face(client)
    face.show('happy')
    face.show('confused')
    assert face.last_shown == 'confused'


def test_a_service_that_is_not_there_returns_false_and_does_not_raise():
    face = _face(_FakeClient(ready=False), attempts=2, retry_s=0.0)
    assert face.show('happy') is False


def test_a_timeout_returns_false():
    assert _face(_FakeClient(results=[None])).show('happy') is False


def test_a_nonzero_code_returns_false():
    bad = _ok()
    bad.header.code = 3
    assert _face(_FakeClient(results=[bad])).show('happy') is False


def test_an_exception_from_the_client_is_swallowed():
    # The words are the turn. Nothing the face does may interrupt them.
    face = _face(_FakeClient(raises=RuntimeError('transport died')))
    assert face.show('happy') is False


def test_clear_returns_the_face_to_its_idle_expression():
    client = _FakeClient(results=[_ok()])
    face = _face(client)
    assert face.clear() is True
    assert client.sent[0].emotion_id == CATALOGUE['blink'].emotion_id


def test_availability_is_false_until_a_call_has_actually_worked():
    client = _FakeClient(results=[_ok()])
    face = _face(client)
    assert face.available is False
    face.show('happy')
    assert face.available is True
```

- [ ] **Step 3: Run it and watch it fail**

Run: `docker/run_tests.sh`
Expected: FAIL — `ModuleNotFoundError: No module named 'x2_greeter.ros.face'`

- [ ] **Step 4: Write `ros/face.py`**

Substitute the field names confirmed in Step 1 where marked.

```python
"""The robot's face.

Fire-and-forget: every method returns a bool the caller may ignore, and
nothing here raises. A failed expression is not a failed turn -- the words
are the turn.

Ships disabled. Nothing on this path has run on hardware yet; the flag gets
flipped on site.
"""
from __future__ import annotations

import time
from typing import Callable, Optional

import rclpy
from aimdk_msgs.srv import PlayEmoji

from x2_greeter.core.faces import CATALOGUE, MODE_ONCE, THINKING

SERVICE = '/aima/interaction/emoji/play'
IDLE_EXPRESSION = 'blink'


def _default_spin(node, future, timeout_sec=None):
    rclpy.spin_until_future_complete(node, future, timeout_sec=timeout_sec)


class Face:
    def __init__(self, node, service: str = SERVICE, enabled: bool = False,
                 timeout_s: float = 1.0, attempts: int = 4,
                 retry_s: float = 0.25, spin_until: Optional[Callable] = None,
                 callback_group=None) -> None:
        self._node = node
        self._enabled = bool(enabled)
        self._timeout_s = float(timeout_s)
        self._attempts = int(attempts)
        self._retry_s = float(retry_s)
        self._spin = spin_until or _default_spin
        self.available = False
        self.last_shown: Optional[str] = None
        kwargs = {}
        if callback_group is not None:
            kwargs['callback_group'] = callback_group
        self._client = node.create_client(PlayEmoji, service, **kwargs)

    def show_thinking(self) -> bool:
        """Shown at t=0.05 s, long before the reply exists.

        This is the whole of the latency mitigation: without it the robot
        stands motionless for two seconds after somebody speaks, which
        reads as broken rather than as thoughtful.
        """
        return self.show(THINKING)

    def clear(self) -> bool:
        return self.show(IDLE_EXPRESSION)

    def show(self, name: str, mode: int = MODE_ONCE) -> bool:
        if not self._enabled:
            return False
        spec = CATALOGUE.get(name)
        if spec is None:
            self._node.get_logger().warn(f'no such expression: {name}')
            return False
        if not self._wait():
            return False

        request = PlayEmoji.Request()
        # Field names confirmed in Step 1 from the .srv itself.
        request.emotion_id = spec.emotion_id
        request.mode = int(mode)

        try:
            future = self._client.call_async(request)
            self._spin(self._node, future, timeout_sec=self._timeout_s)
            response = future.result()
        except Exception as exc:        # noqa: BLE001
            self._node.get_logger().warn(
                f'expression {name} failed: {type(exc).__name__}')
            return False

        if response is None:
            self._node.get_logger().warn(f'expression {name} timed out')
            return False
        code = getattr(getattr(response, 'header', None), 'code', 0)
        if code != 0:
            self._node.get_logger().warn(
                f'expression {name} refused, code {code}')
            return False

        self.available = True
        self.last_shown = name
        return True

    def _wait(self) -> bool:
        for _ in range(self._attempts):
            if self._client.wait_for_service(timeout_sec=self._retry_s):
                return True
            if self._retry_s:
                time.sleep(self._retry_s)
        self._node.get_logger().warn('the emoji service is not available')
        return False
```

- [ ] **Step 5: Run the container suite**

Run: `docker/run_tests.sh`
Expected: PASS (13 new tests)

- [ ] **Step 6: Commit**

```bash
git add x2_greeter_ws/src/x2_greeter/x2_greeter/ros/face.py \
        x2_greeter_ws/src/x2_greeter/test/ros/test_face.py
git commit -m "feat(ros): face adapter, disabled by default"
```

---

## Task 12: The head

**Files:**
- Create: `x2_greeter_ws/src/x2_greeter/x2_greeter/ros/head.py`
- Test: `x2_greeter_ws/src/x2_greeter/test/ros/test_head.py`

**Interfaces:**
- Consumes: `aimdk_msgs.msg.JointCommandArray`, `core.gaze.{MAX_YAW_RAD, HEAD_YAW_JOINT, HEAD_PITCH_JOINT, clamp_yaw, sweep_waypoints, yaw_for, group_drift}`, `rclpy`.
- Produces: `ros.head.Head(node, command_topic='/aima/hal/joint/head/command', state_topic='/aima/hal/joint/head/state', enabled=False, rate_hz=20.0, callback_group=None)` with `.look_at(yaw_rad) -> bool`, `.centre() -> bool`, `.sweep(on_capture=None, sleep=None) -> bool`, `.current_yaw`, `.commands_sent`, `.destroy()`.

**This is the most dangerous adapter in the plan, so it ships disabled and clamped twice.**

- The vendor limit is ±20° = **0.349 rad**. Our own limit is **0.262 rad** (15°), and `clamp_yaw` in `core/gaze.py` enforces it. This adapter clamps again on the way out. Two clamps is not redundancy theatre: `core/gaze.py` is pure and testable, this class is what actually reaches a motor, and the day somebody calls `look_at` with a number that did not come through `gaze.py` is the day the second clamp is the only one that runs.
- **Every exit path returns the head to 0.0.** `sweep()` ends centred whether it completed, was interrupted, or raised. `destroy()` centres. `centre()` is idempotent and cheap.
- **`enabled: false` ships.** Head motion has never run on this robot under our code.

**The sweep is not a way to see more.** 94° of head camera plus 30° of sweep is 134°, and a single front stereo frame already covers 156°. The sweep exists because a robot that glances around before answering reads as attentive; the wide frame is where the extra field of view actually comes from. If the sweep ever has to be cut for latency, nothing is lost from the scene understanding.

- [ ] **Step 1: Read the message definition**

Run, in the container:

```bash
ros2 interface show aimdk_msgs/msg/JointCommandArray
ros2 topic info /aima/hal/joint/head/command -v
ros2 topic echo /aima/hal/joint/head/state --once
```

Confirm: the array field's name, the per-entry field names for joint name and target position, and that the state topic really is `TRANSIENT_LOCAL` (the `-v` output prints the durability). Use those names in Steps 3 and 4; the code shape does not change.

Note the two entries the vendor expects: `[head_yaw, head_pitch]`. Pitch is always commanded to its current value — this phase moves yaw only.

- [ ] **Step 2: Write the failing test**

Create `test/ros/test_head.py`:

```python
"""The head adapter: clamped twice, centred on every exit, off by default."""
import math

import pytest

pytestmark = pytest.mark.ros

from aimdk_msgs.msg import JointCommandArray  # noqa: E402

from x2_greeter.core.gaze import (  # noqa: E402
    HEAD_PITCH_JOINT, HEAD_YAW_JOINT, MAX_YAW_RAD)
from x2_greeter.ros.head import Head  # noqa: E402


class _FakePub:
    def __init__(self):
        self.messages = []
        self.destroyed = False

    def publish(self, msg):
        self.messages.append(msg)


class _FakeNode:
    def __init__(self):
        self.publisher = _FakePub()
        self.state_callback = None
        self.logs = []

    def create_publisher(self, msg_type, topic, qos, **kwargs):
        self.command_topic = topic
        self.command_type = msg_type
        return self.publisher

    def create_subscription(self, msg_type, topic, callback, qos, **kwargs):
        self.state_topic = topic
        self.state_callback = callback
        return object()

    def destroy_publisher(self, pub):
        pub.destroyed = True

    def destroy_subscription(self, sub):
        pass

    def get_logger(self):
        node = self

        class _L:
            def info(self, m): node.logs.append(m)
            def warn(self, m): node.logs.append(m)
            def warning(self, m): node.logs.append(m)
            def error(self, m): node.logs.append(m)
        return _L()


def _yaw_of(msg):
    for entry in msg.joints:
        if entry.name == HEAD_YAW_JOINT:
            return entry.position
    raise AssertionError('no yaw entry in the command')


def _head(node=None, **kwargs):
    kwargs.setdefault('enabled', True)
    return Head(node=node or _FakeNode(), **kwargs)


def test_a_disabled_head_publishes_nothing():
    node = _FakeNode()
    head = Head(node=node, enabled=False)
    assert head.look_at(0.2) is False
    assert head.sweep() is False
    assert node.publisher.messages == [], (
        'head motion has never run on this robot under our code')


def test_it_publishes_to_the_documented_topics():
    node = _FakeNode()
    _head(node)
    assert node.command_topic == '/aima/hal/joint/head/command'
    assert node.state_topic == '/aima/hal/joint/head/state'
    assert node.command_type is JointCommandArray


def test_a_command_carries_both_joints_in_the_vendor_order():
    node = _FakeNode()
    _head(node).look_at(0.1)
    names = [e.name for e in node.publisher.messages[0].joints]
    assert names == [HEAD_YAW_JOINT, HEAD_PITCH_JOINT]


def test_pitch_is_never_moved_in_this_phase():
    node = _FakeNode()
    _head(node).look_at(0.2)
    pitch = [e for e in node.publisher.messages[0].joints
             if e.name == HEAD_PITCH_JOINT][0]
    assert pitch.position == 0.0


@pytest.mark.parametrize('requested', [1.5, -1.5, 0.349, -0.349, 10.0])
def test_anything_beyond_our_limit_is_clamped_here_too(requested):
    node = _FakeNode()
    _head(node).look_at(requested)
    assert abs(_yaw_of(node.publisher.messages[0])) <= MAX_YAW_RAD + 1e-9


def test_the_second_clamp_is_not_theatre():
    # core.gaze clamps, and so does this. The day somebody calls look_at
    # with a number that did not pass through gaze.py, this is the only
    # clamp that runs.
    node = _FakeNode()
    _head(node).look_at(0.348)          # inside the vendor limit, past ours
    assert abs(_yaw_of(node.publisher.messages[0])) == pytest.approx(MAX_YAW_RAD)


@pytest.mark.parametrize('bad', [float('nan'), float('inf'), float('-inf')])
def test_a_non_finite_yaw_becomes_centre_not_a_motor_command(bad):
    node = _FakeNode()
    _head(node).look_at(bad)
    assert _yaw_of(node.publisher.messages[0]) == 0.0


def test_current_yaw_tracks_what_was_commanded():
    head = _head()
    head.look_at(0.1)
    assert head.current_yaw == pytest.approx(0.1)


def test_centre_returns_to_zero():
    node = _FakeNode()
    head = _head(node)
    head.look_at(0.2)
    head.centre()
    assert _yaw_of(node.publisher.messages[-1]) == 0.0
    assert head.current_yaw == 0.0


def test_a_sweep_starts_and_ends_at_centre():
    node = _FakeNode()
    _head(node).sweep(sleep=lambda s: None)
    yaws = [_yaw_of(m) for m in node.publisher.messages]
    assert yaws[0] == 0.0
    assert yaws[-1] == 0.0


def test_a_sweep_visits_both_extremes_and_never_exceeds_the_limit():
    node = _FakeNode()
    _head(node).sweep(sleep=lambda s: None)
    yaws = [_yaw_of(m) for m in node.publisher.messages]
    assert min(yaws) == pytest.approx(-MAX_YAW_RAD)
    assert max(yaws) == pytest.approx(MAX_YAW_RAD)
    assert all(abs(y) <= MAX_YAW_RAD + 1e-9 for y in yaws)


def test_the_sweep_asks_for_a_capture_at_each_hold():
    captures = []
    _head().sweep(on_capture=lambda yaw: captures.append(yaw),
                  sleep=lambda s: None)
    assert len(captures) == 2, 'one capture at each end, not one per sample'
    assert captures[0] == pytest.approx(-MAX_YAW_RAD)
    assert captures[1] == pytest.approx(MAX_YAW_RAD)


def test_a_capture_callback_that_raises_still_leaves_the_head_centred():
    node = _FakeNode()
    head = _head(node)

    def _boom(yaw):
        raise RuntimeError('the camera exploded')

    with pytest.raises(RuntimeError):
        head.sweep(on_capture=_boom, sleep=lambda s: None)
    assert _yaw_of(node.publisher.messages[-1]) == 0.0, (
        'a head left turned 15 degrees off-centre is how the next session '
        'starts looking at a wall')
    assert head.current_yaw == 0.0


def test_destroy_centres_before_releasing_the_publisher():
    node = _FakeNode()
    head = _head(node)
    head.look_at(0.25)
    head.destroy()
    assert _yaw_of(node.publisher.messages[-1]) == 0.0
    assert node.publisher.destroyed is True


def test_state_feedback_is_recorded_when_it_arrives():
    node = _FakeNode()
    head = _head(node)
    assert node.state_callback is not None, (
        'the state topic is TRANSIENT_LOCAL, so the last value is there '
        'the moment we subscribe')
    msg = JointCommandArray()
    entry = type(msg.joints[0]) if msg.joints else None
    # Build a one-entry state message using the same entry type the
    # command array uses; see Step 1 for the exact field names.
    head._on_state(_state_message(0.12))
    assert head.measured_yaw == pytest.approx(0.12)


def _state_message(yaw):
    from aimdk_msgs.msg import JointCommandArray as _Arr
    msg = _Arr()
    entry = _Arr().joints.__class__() if False else None
    # Constructed via the message's own entry type; substitute the entry
    # class name confirmed in Step 1.
    from aimdk_msgs.msg import JointCommand
    yaw_entry = JointCommand()
    yaw_entry.name = HEAD_YAW_JOINT
    yaw_entry.position = yaw
    msg.joints = [yaw_entry]
    return msg
```

If Step 1 shows the entry type is not `JointCommand`, use the real name in `_state_message` and delete the two dead lines above it — they are there only to make the substitution point obvious, and should not survive into the committed test.

- [ ] **Step 3: Run it and watch it fail**

Run: `docker/run_tests.sh`
Expected: FAIL — `ModuleNotFoundError: No module named 'x2_greeter.ros.head'`

- [ ] **Step 4: Write `ros/head.py`**

```python
"""Head yaw. Clamped twice, centred on every exit, disabled by default.

The vendor limit is +/-20 degrees (0.349 rad). Ours is 0.262 rad, enforced
in core.gaze and again here. Two clamps because core.gaze is pure and this
class is what reaches a motor: the day somebody calls look_at with a number
that did not come through gaze.py, this is the only clamp that runs.

The sweep is not a way to see more -- 94 degrees of head camera plus 30 of
sweep is 134, and one front stereo frame already covers 156. It exists
because a robot that glances around before answering reads as attentive.
"""
from __future__ import annotations

import time
from typing import Callable, Optional

from aimdk_msgs.msg import JointCommand, JointCommandArray
from rclpy.qos import (DurabilityPolicy, QoSProfile, ReliabilityPolicy,
                       qos_profile_sensor_data)

from x2_greeter.core.gaze import (
    HEAD_PITCH_JOINT, HEAD_YAW_JOINT, MAX_YAW_RAD, clamp_yaw, sweep_waypoints)

COMMAND_TOPIC = '/aima/hal/joint/head/command'
STATE_TOPIC = '/aima/hal/joint/head/state'


class Head:
    def __init__(self, node, command_topic: str = COMMAND_TOPIC,
                 state_topic: str = STATE_TOPIC, enabled: bool = False,
                 rate_hz: float = 20.0, callback_group=None) -> None:
        self._node = node
        self._enabled = bool(enabled)
        self._rate_hz = float(rate_hz)
        self.current_yaw = 0.0
        self.measured_yaw: Optional[float] = None
        self.commands_sent = 0

        kwargs = {}
        if callback_group is not None:
            kwargs['callback_group'] = callback_group
        self._pub = node.create_publisher(
            JointCommandArray, command_topic, 10, **kwargs)

        state_qos = QoSProfile(depth=1)
        state_qos.reliability = ReliabilityPolicy.RELIABLE
        state_qos.durability = DurabilityPolicy.TRANSIENT_LOCAL
        self._state_sub = node.create_subscription(
            JointCommandArray, state_topic, self._on_state, state_qos,
            **kwargs)

    # -- commands ---------------------------------------------------------

    def look_at(self, yaw_rad: float) -> bool:
        if not self._enabled:
            return False
        yaw = clamp_yaw(yaw_rad)
        # And again, here, because this is the call that reaches a motor.
        yaw = max(-MAX_YAW_RAD, min(MAX_YAW_RAD, yaw))
        self._publish(yaw)
        return True

    def centre(self) -> bool:
        return self.look_at(0.0)

    def sweep(self, on_capture: Optional[Callable[[float], None]] = None,
              sleep: Optional[Callable[[float], None]] = None) -> bool:
        """Centre, left, hold, right, hold, centre.

        on_capture is called once at each hold, with the yaw it was taken
        at. The head returns to centre on every exit path, including an
        exception out of on_capture -- a head left 15 degrees off-centre is
        how the next session starts by looking at a wall.
        """
        if not self._enabled:
            return False
        rest = sleep if sleep is not None else time.sleep
        try:
            previous_t = 0.0
            for step in sweep_waypoints(rate_hz=self._rate_hz):
                self.look_at(step.yaw)
                rest(max(0.0, step.t_s - previous_t))
                previous_t = step.t_s
                if step.capture and on_capture is not None:
                    on_capture(step.yaw)
            return True
        finally:
            self._force_centre()

    def destroy(self) -> None:
        self._force_centre()
        self._node.destroy_subscription(self._state_sub)
        self._node.destroy_publisher(self._pub)

    # -- internals --------------------------------------------------------

    def _force_centre(self) -> None:
        # Deliberately not look_at(): centring must happen even on the exit
        # path of a head that was disabled mid-flight.
        if self._enabled:
            self._publish(0.0)

    def _publish(self, yaw: float) -> None:
        yaw_entry = JointCommand()
        yaw_entry.name = HEAD_YAW_JOINT
        yaw_entry.position = float(yaw)
        pitch_entry = JointCommand()
        pitch_entry.name = HEAD_PITCH_JOINT
        pitch_entry.position = 0.0      # this phase moves yaw only

        msg = JointCommandArray()
        msg.joints = [yaw_entry, pitch_entry]
        self._pub.publish(msg)
        self.current_yaw = float(yaw)
        self.commands_sent += 1

    def _on_state(self, msg) -> None:
        for entry in getattr(msg, 'joints', ()):
            if entry.name == HEAD_YAW_JOINT:
                self.measured_yaw = float(entry.position)
                return
```

- [ ] **Step 5: Run the container suite**

Run: `docker/run_tests.sh`
Expected: PASS (16 new tests)

- [ ] **Step 6: Prove the centre-on-exit guarantee is load-bearing**

Delete the `finally:` block in `sweep()` (leaving the body inline after the loop) and re-run.
Expected: `test_a_capture_callback_that_raises_still_leaves_the_head_centred` FAILS.
Revert immediately. If it passes without the `finally`, the test is not testing what it says.

- [ ] **Step 7: Commit**

```bash
git add x2_greeter_ws/src/x2_greeter/x2_greeter/ros/head.py \
        x2_greeter_ws/src/x2_greeter/test/ros/test_head.py
git commit -m "feat(ros): head yaw adapter, clamped and disabled by default"
```

---

## Task 13: The shipped configuration

**Files:**
- Create: `x2_greeter_ws/src/x2_greeter/config/conversation.yaml`
- Modify: `x2_greeter_ws/src/x2_greeter/test/test_shipped_config.py`
- Modify: `x2_greeter_ws/src/x2_greeter/setup.py` (install `config/venues/` alongside `config/`)

**Interfaces:**
- Consumes: `core.gestures.CATALOGUE`, `core.faces.CATALOGUE`, `core.language.ALLOWED_LANGUAGES`, `core.gaze.MAX_YAW_RAD`.
- Produces: `config/conversation.yaml`, the file the launch file loads and the only place any of these numbers exists at runtime.

**Every hardware-gated feature ships `false`.** Face, head, sweep, gaze-follow and the environment camera have never run on this robot under our code. The user's instruction was 都做，在现场再做调整 — build all of it, tune it on site — so all of it is here, wired, tested, and off. Turning one on is a one-line edit and a relaunch, which is what a hardware window is for.

**`test_shipped_config.py` already exists and already carries the reason it exists**, verbatim: *"This file is the only thing standing between a one-line edit to the shipped YAML and a disabled safety interlock reaching real hardware unnoticed."* Extend that file. Do not start a second one.

- [ ] **Step 1: Write `config/conversation.yaml`**

```yaml
# The conversational interaction node.
#
# Every hardware-gated feature below ships disabled. None of them has run on
# this robot under our code, and each one is a one-line edit away from being
# on. Tune on site, with a human within reach of the stop control.
#
# Anything with a test in test/test_shipped_config.py is pinned deliberately.
# Widening one of those means changing the test too, which means somebody
# reads the reason before the robot does.
/**:
  ros__parameters:

    venue:
      # A file under config/venues/, without the .yaml.
      profile: "clothing_store"

    presence:
      confidence_min: 0.5
      # Anyone inside this is addressed individually; a room where everyone
      # is further away is addressed as a group. Locked at session start.
      individual_max_m: 2.0
      # How far out the robot reads the room. Deliberately wider than the
      # 3.0 m Phase 1 greeting trigger: seeing the six people behind the one
      # in front is the point.
      scene_max_m: 5.0
      child_stature_max_m: 1.35

    conversation:
      max_turns: 8
      silence_timeout_s: 8.0
      # The promise that the robot lets go, not a resource limit.
      session_max_s: 180.0
      cooldown_s: 20.0
      backend_failures_max: 2
      history_max: 12
      reply_max_chars: 200

    language:
      # English default, following whoever is speaking. Malay is next phase.
      default: "en"
      switch_confidence: 0.7
      allowed: ["en", "zh"]

    hearing:
      enabled: true
      topic: "/aima/interaction/audio/processed"
      max_utterance_s: 15.0
      # faster-whisper, local on PC2. No new vendor, no audio leaving the robot.
      model_size: "small"
      device: "auto"
      compute_type: "int8"

    dialogue:
      model: "claude-opus-5"
      effort: "low"
      # Must stay under conversation.silence_timeout_s: a backend still
      # thinking when the silence timer fires would close its own session.
      timeout_s: 6.0

    speech:
      service: "/aima/interaction/tts/play"
      timeout_s: 10.0

    gestures:
      enabled:
        - "wave"
        - "hello"
        - "bow"
        - "nod"
        - "clap"
        - "thumbs_up"
        - "blow_kiss"
        - "heart"
        - "shake_hand"
        - "salute"
        - "point_forward"
      hand_preference: "right"

    face:
      # OFF. Never run on hardware.
      enabled: false
      service: "/aima/interaction/emoji/play"
      enabled_expressions:
        - "thinking"
        - "calm"
        - "blink"
        - "happy"
        - "very_happy"
        - "cute"
        - "confused"
        - "sympathy"

    head:
      # OFF. Never run on hardware. Yaw only, and clamped to 0.262 rad in
      # two places before it reaches a motor.
      enabled: false
      sweep_on_start: false
      gaze_follow: false
      command_topic: "/aima/hal/joint/head/command"
      state_topic: "/aima/hal/joint/head/state"
      rate_hz: 20.0

    base_frame:
      # OFF. The wide 156-degree view of the room. Until this is on, the
      # base frame is a head-camera frame captured at session start, which
      # works and simply sees less.
      use_env_camera: false
      # Discover the real front-stereo topic on site:
      #   ros2 topic list | grep -i -E 'stereo|front|fisheye'
      # and put it here before flipping the flag above.
      rgb_topic: "/aima/sensor/camera/head/color/image_raw"
      rotate_180: false

    safety:
      require_stand_default: true
      gesture_min_distance_m: 1.0
      stale_distance_s: 1.0

    mc_input:
      name: "x2_greeter"
      # Inside the documented SDK band 20-39. The remote controller stays at
      # 80 and keeps its override.
      priority: 30
```

- [ ] **Step 2: Write the failing tests**

Append to `test/test_shipped_config.py`. Reuse the module's existing `_params` helper pattern — read the file first and follow whatever it does — adding a second path constant beside the Phase 1 one:

```python
CONVERSATION_PATH = Path(__file__).resolve().parents[1] / 'config' / 'conversation.yaml'
VENUES_DIR = Path(__file__).resolve().parents[1] / 'config' / 'venues'


def _conversation_params():
    with CONVERSATION_PATH.open(encoding='utf-8') as handle:
        return yaml.safe_load(handle)['/**']['ros__parameters']


# --- the hardware gates ---------------------------------------------------

@pytest.mark.parametrize('path', [
    ('face', 'enabled'),
    ('head', 'enabled'),
    ('head', 'sweep_on_start'),
    ('head', 'gaze_follow'),
    ('base_frame', 'use_env_camera'),
])
def test_every_hardware_gated_feature_ships_off(path):
    # None of these has run on this robot under our code. Turning one on is
    # a deliberate act performed on site, with a human at the stop control.
    section, key = path
    assert _conversation_params()[section][key] is False


def test_hearing_ships_on_because_a_robot_that_cannot_hear_has_no_phase_two():
    assert _conversation_params()['hearing']['enabled'] is True


# --- the safety interlocks ------------------------------------------------

def test_the_stand_default_interlock_is_on():
    assert _conversation_params()['safety']['require_stand_default'] is True


def test_the_arms_reach_interlock_is_one_metre():
    assert _conversation_params()['safety']['gesture_min_distance_m'] == 1.0


def test_the_mc_input_priority_stays_in_the_documented_band():
    priority = _conversation_params()['mc_input']['priority']
    assert 20 <= priority <= 39, (
        'the remote controller sits at 80 and must keep its override')


# --- the numbers that have to agree with each other -----------------------

def test_the_backend_gives_up_before_the_silence_timer_fires():
    params = _conversation_params()
    assert (params['dialogue']['timeout_s']
            < params['conversation']['silence_timeout_s']), (
        'a backend still thinking when the silence timer fires would close '
        'the session it is answering')


def test_the_session_cap_is_not_quietly_widened():
    assert _conversation_params()['conversation']['session_max_s'] <= 300.0


def test_the_turn_cap_is_not_quietly_widened():
    assert _conversation_params()['conversation']['max_turns'] <= 12


def test_individual_addressing_stops_where_the_spec_says():
    assert _conversation_params()['presence']['individual_max_m'] == 2.0


def test_the_scene_horizon_is_wider_than_the_addressing_distance():
    presence = _conversation_params()['presence']
    assert presence['scene_max_m'] > presence['individual_max_m']


# --- the names must exist -------------------------------------------------

def test_every_enabled_gesture_is_in_the_catalogue():
    from x2_greeter.core.gestures import CATALOGUE

    unknown = [name for name in _conversation_params()['gestures']['enabled']
               if name not in CATALOGUE]
    assert unknown == [], f'no such gesture: {unknown}'


def test_every_enabled_expression_is_in_the_catalogue():
    from x2_greeter.core.faces import CATALOGUE

    unknown = [name for name in
               _conversation_params()['face']['enabled_expressions']
               if name not in CATALOGUE]
    assert unknown == [], f'no such expression: {unknown}'


def test_the_thinking_expression_is_enabled():
    # It is the entire latency mitigation: shown at t=0.05 s while the
    # reply is still two seconds away.
    assert 'thinking' in _conversation_params()['face']['enabled_expressions']


@pytest.mark.parametrize('forbidden', ['angry', 'extreme_angry'])
def test_no_angry_expression_is_ever_enabled(forbidden):
    assert forbidden not in _conversation_params()['face']['enabled_expressions']


def test_the_configured_venue_profile_exists():
    profile = _conversation_params()['venue']['profile']
    assert (VENUES_DIR / f'{profile}.yaml').is_file()


def test_every_venue_profile_on_disk_parses():
    from x2_greeter.core.venue import load_venue

    profiles = sorted(VENUES_DIR.glob('*.yaml'))
    assert profiles, 'the venue directory is not empty'
    for path in profiles:
        load_venue(path)


# --- language -------------------------------------------------------------

def test_the_allowed_languages_are_exactly_this_phases_two():
    from x2_greeter.core.language import ALLOWED_LANGUAGES

    assert (tuple(_conversation_params()['language']['allowed'])
            == ALLOWED_LANGUAGES), (
        'Malay is next phase; adding it here without a TTS voice produces a '
        'robot that answers in a language it cannot pronounce')


def test_the_default_language_is_english():
    assert _conversation_params()['language']['default'] == 'en'


# --- the model ------------------------------------------------------------

def test_no_api_key_is_anywhere_in_the_shipped_config():
    text = CONVERSATION_PATH.read_text(encoding='utf-8')
    assert 'sk-ant' not in text
    assert 'ANTHROPIC_API_KEY' not in text, (
        'the key comes from the environment and is never written down')
```

- [ ] **Step 3: Run and watch the new tests fail**

Run: `python -m pytest x2_greeter_ws/src/x2_greeter/test/test_shipped_config.py -v`
Expected: FAIL — the file does not exist yet if Step 1 has not been done, or the Phase 1 tests pass and the new ones error on a missing path.

- [ ] **Step 4: Make them pass**

If Step 1 is done, they should already pass. Any failure here is the config disagreeing with the plan — fix the config, not the test, unless the test is wrong about the spec.

- [ ] **Step 5: Install the venue directory**

`setup.py` already installs `config/*.yaml` via `data_files`. `config/venues/*.yaml` is a subdirectory and will not be picked up by that glob. Add a second entry:

```python
(os.path.join('share', package_name, 'config', 'venues'),
 glob('config/venues/*.yaml')),
```

A venue file that exists in the source tree but not in the install space produces a node that starts, reads its parameters, and then dies looking for a profile — on hardware, in front of people.

- [ ] **Step 6: Verify the install space actually contains them**

Run, on PC2:

```bash
cd ~/x2_greeter_ws && colcon build --packages-select x2_greeter --symlink-install
ls install/x2_greeter/share/x2_greeter/config/venues/
```

Expected: `clothing_store.yaml  mall_atrium.yaml`

- [ ] **Step 7: Run the host suite and commit**

Run: `python -m pytest`
Expected: PASS

```bash
git add x2_greeter_ws/src/x2_greeter/config/conversation.yaml \
        x2_greeter_ws/src/x2_greeter/test/test_shipped_config.py \
        x2_greeter_ws/src/x2_greeter/setup.py
git commit -m "feat(config): shipped conversation config, every hardware gate off"
```

---

## Task 14: The node

**Files:**
- Create: `x2_greeter_ws/src/x2_greeter/x2_greeter/ros/conversation_node.py`
- Create: `x2_greeter_ws/src/x2_greeter/launch/conversation.launch.py`
- Modify: `x2_greeter_ws/src/x2_greeter/setup.py` (entry point + launch install)
- Test: `x2_greeter_ws/src/x2_greeter/test/ros/test_conversation_node.py`

**Interfaces:**
- Consumes: everything built in Tasks 1–13, plus the Phase 1 adapters.
- Produces: `ros.conversation_node.ConversationNode(**overrides)` and `ros.conversation_node.main(args=None)`; console script `x2_conversation`.

### Reuse the Phase 1 adapters — do not write second copies

Before writing a line, list what already exists:

```bash
ls x2_greeter_ws/src/x2_greeter/x2_greeter/ros/
grep -rn "^class " x2_greeter_ws/src/x2_greeter/x2_greeter/ros/
```

Phase 1 already ships adapters for TTS (`PlayTts`), preset gestures, the MC input registration, the motion-mode guard, and `FrameSource`. Use those classes by their real names. The node below refers to them as `Speech`, `Gestures`, `SafetyGate` and `FrameSource`; substitute whatever they are actually called. A second TTS client in the same process is two audio-focus requests fighting over one speaker.

### The threading model, and why it is the shape it is

`rclpy` callbacks must not block. Transcription takes 200–600 ms and the backend call takes 1.5–3 s, so both run on **one worker thread**, fed by a single-slot queue. Everything else — frames, audio segments, the tick timer — stays on the executor.

- A `MultiThreadedExecutor` with a `ReentrantCallbackGroup` for the service clients, so a service call made from the worker can complete while a sensor callback runs.
- One `threading.Lock` around every `Conversation` mutation. The state machine is not thread-safe and should not be: it is a pure object, and making it thread-aware would put locking into the one module that is entirely testable without a robot.
- A single-slot queue, discarding the older item. If a second utterance arrives while the first is still being transcribed, the newer one is what the person actually just said.

### The order of operations in a turn, and the one line that carries §13

```
audio END        t=0.00   utterance handed to the worker
face.show_thinking()      t=0.05   <-- the entire latency mitigation
transcribe               t=0.05 .. 0.5
conversation.heard()     t=0.50
frame captured fresh     t=0.50   every turn, per the locked decision
backend.respond()        t=0.50 .. 3.0
conversation.replied()   t=3.00
gesture + face + speak   t=3.00
conversation.finished_speaking()
```

`show_thinking()` is called **before** transcription starts, not after. Moving it later gives back the two seconds of motionless silence it exists to remove.

- [ ] **Step 1: Write the failing test**

Create `test/ros/test_conversation_node.py`. This is a wiring test: it builds the node with every adapter replaced by a fake and drives one whole turn through it.

```python
"""The node: one turn, end to end, with everything faked below it.

This is a wiring test. The behaviour it checks is ordering and delegation --
who gets called, with what, in which order. The rules themselves are tested
in core/ where they live.
"""
import time

import pytest

pytestmark = pytest.mark.ros

import rclpy  # noqa: E402

from x2_greeter.cognition.dialogue import Turn  # noqa: E402
from x2_greeter.cognition.transcriber import Utterance  # noqa: E402
from x2_greeter.core.conversation import SessionState  # noqa: E402
from x2_greeter.core.scene import AddressingMode, Person, SceneSnapshot  # noqa: E402
from x2_greeter.ros.conversation_node import ConversationNode  # noqa: E402


@pytest.fixture(scope='module', autouse=True)
def ros():
    rclpy.init()
    yield
    rclpy.shutdown()


class _Recorder:
    """Records the order every fake adapter was called in."""

    def __init__(self):
        self.calls = []

    def note(self, what, *args):
        self.calls.append((what, args))

    def names(self):
        return [name for name, _ in self.calls]


class _FakeFace:
    def __init__(self, log):
        self.log = log
        self.shown = []

    def show_thinking(self):
        self.log.note('face.thinking')
        return True

    def show(self, name, mode=1):
        self.log.note('face.show', name)
        self.shown.append(name)
        return True

    def clear(self):
        self.log.note('face.clear')
        return True


class _FakeSpeech:
    def __init__(self, log):
        self.log = log
        self.said = []

    def say(self, text, language='en'):
        self.log.note('speech.say', text, language)
        self.said.append((text, language))
        return True


class _FakeGestures:
    def __init__(self, log, allowed=True):
        self.log = log
        self.allowed = allowed
        self.played = []

    def play(self, name=None):
        self.log.note('gesture.play', name)
        if not self.allowed:
            return False
        self.played.append(name)
        return True


class _FakeHead:
    def __init__(self, log):
        self.log = log
        self.yaws = []

    def look_at(self, yaw):
        self.yaws.append(yaw)
        return True

    def centre(self):
        self.log.note('head.centre')
        return True

    def sweep(self, on_capture=None, sleep=None):
        self.log.note('head.sweep')
        return True

    def destroy(self):
        pass


class _FakeTranscriber:
    name = 'fake'

    def __init__(self, log, utterance=None):
        self.log = log
        self.utterance = utterance or Utterance('hello there', 'en', 0.95)

    def transcribe(self, pcm, sample_rate=16000):
        self.log.note('transcribe', len(pcm))
        return self.utterance


class _FakeBackend:
    def __init__(self, log, turn=None, raises=None):
        self.log = log
        self.turn = turn or Turn('Nice to meet you.', 'en', 'wave', 'happy',
                                 False)
        self.raises = raises
        self.last = None

    def respond(self, base_frame, frame, scene, venue, history, utterance,
                language, child):
        self.log.note('backend.respond')
        self.last = dict(base_frame=base_frame, frame=frame, scene=scene,
                         history=tuple(history), utterance=utterance,
                         language=language, child=child)
        if self.raises is not None:
            raise self.raises
        return self.turn


def _person(distance=1.4, child=False):
    return Person(bbox=(0, 0, 10, 10), confidence=0.9, distance_m=distance,
                  center_offset=0.0, stature_m=1.0 if child else 1.7,
                  likely_child=child)


def _scene(*people, mode=AddressingMode.INDIVIDUAL):
    people = people or (_person(),)
    return SceneSnapshot(people=people,
                         subject=people[0] if mode is AddressingMode.INDIVIDUAL
                         else None, mode=mode, at_s=0.0)


@pytest.fixture
def node():
    log = _Recorder()
    node = ConversationNode(
        face=_FakeFace(log), speech=_FakeSpeech(log),
        gestures=_FakeGestures(log), head=_FakeHead(log),
        transcriber=_FakeTranscriber(log), backend=_FakeBackend(log),
        audio_source=None, frame_source=None, env_camera=None,
        agent_mode=None, safety_gate=None, start_worker=False)
    node.log = log
    yield node
    node.destroy_node()


def _drive_turn(node, pcm=b'\x00' * 3200):
    """Push one utterance through the worker body synchronously."""
    node._handle_utterance(pcm, at_s=1.0)


def test_a_session_starts_when_somebody_is_seen(node):
    node._on_scene(_scene(), at_s=0.0)
    assert node.conversation.state is SessionState.GREETING
    assert node.speech.said[0][0] != ''


def test_the_opening_line_comes_from_the_venue(node):
    node._on_scene(_scene(), at_s=0.0)
    assert node.speech.said[0][0] == node.venue.opening_for('en')


def test_nothing_starts_while_the_cooldown_is_running(node):
    node._on_scene(_scene(), at_s=0.0)
    node.conversation.close(node.conversation.close_reason
                            or _close_reason(), at_s=1.0)
    said = len(node.speech.said)
    node._on_scene(_scene(), at_s=2.0)
    assert len(node.speech.said) == said


def _close_reason():
    from x2_greeter.core.conversation import CloseReason
    return CloseReason.SILENCE


def test_the_thinking_face_comes_before_transcription(node):
    node._on_scene(_scene(), at_s=0.0)
    node._greeting_finished(at_s=0.5)
    _drive_turn(node)
    names = node.log.names()
    assert names.index('face.thinking') < names.index('transcribe'), (
        'shown at t=0.05 s: it is the whole of the latency mitigation, and '
        'putting it after transcription gives back the silence it removes')


def test_one_turn_walks_transcribe_respond_gesture_speak(node):
    node._on_scene(_scene(), at_s=0.0)
    node._greeting_finished(at_s=0.5)
    _drive_turn(node)
    names = [n for n in node.log.names()
             if n in ('transcribe', 'backend.respond', 'gesture.play',
                      'speech.say')]
    assert names == ['transcribe', 'backend.respond', 'gesture.play',
                     'speech.say']


def test_the_backend_gets_a_fresh_frame_every_turn(node):
    node._on_scene(_scene(), at_s=0.0)
    node._greeting_finished(at_s=0.5)
    node._on_frame(object(), None, 0.6)
    first = object()
    node._on_frame(first, None, 0.9)
    _drive_turn(node)
    assert node.backend.last['frame'] is first, (
        '每轮都看 -- the per-turn frame is the newest one, not the one from '
        'the start of the session')


def test_the_base_frame_is_captured_once_and_reused(node):
    node._on_scene(_scene(), at_s=0.0)
    node._greeting_finished(at_s=0.5)
    base = object()
    node._on_base_frame(base, 0.1)
    _drive_turn(node)
    first_base = node.backend.last['base_frame']
    _drive_turn(node)
    assert node.backend.last['base_frame'] is first_base is base


def test_child_mode_reaches_the_backend(node):
    node._on_scene(_scene(_person(distance=1.2, child=True)), at_s=0.0)
    node._greeting_finished(at_s=0.5)
    _drive_turn(node)
    assert node.backend.last['child'] is True


def test_an_empty_transcription_does_not_call_the_backend(node):
    node.transcriber.utterance = Utterance('', 'en', 0.0)
    node._on_scene(_scene(), at_s=0.0)
    node._greeting_finished(at_s=0.5)
    _drive_turn(node)
    assert 'backend.respond' not in node.log.names()


def test_a_backend_failure_still_produces_speech(node):
    from x2_greeter.cognition.dialogue import BackendUnavailable

    node.backend.raises = BackendUnavailable('offline')
    node._on_scene(_scene(), at_s=0.0)
    node._greeting_finished(at_s=0.5)
    _drive_turn(node)
    assert node.speech.said[-1][0] != '', (
        'the robot speaks in every failure case; silence is the one '
        'behaviour that reads as broken')


def test_a_refused_gesture_does_not_swallow_the_words(node):
    node.gestures.allowed = False
    node._on_scene(_scene(), at_s=0.0)
    node._greeting_finished(at_s=0.5)
    _drive_turn(node)
    assert node.speech.said[-1][0] == 'Nice to meet you.'


def test_the_head_is_centred_when_the_node_shuts_down(node):
    node._on_scene(_scene(), at_s=0.0)
    node.shutdown()
    assert 'head.centre' in node.log.names()


def test_the_face_is_cleared_when_the_session_closes(node):
    from x2_greeter.core.conversation import CloseReason

    node._on_scene(_scene(), at_s=0.0)
    node._greeting_finished(at_s=0.5)
    node._close(CloseReason.SILENCE, at_s=20.0)
    assert 'face.clear' in node.log.names()


def test_a_disabled_head_is_never_asked_to_sweep():
    log = _Recorder()
    node = ConversationNode(face=_FakeFace(log), speech=_FakeSpeech(log),
                            gestures=_FakeGestures(log), head=_FakeHead(log),
                            transcriber=_FakeTranscriber(log),
                            backend=_FakeBackend(log), audio_source=None,
                            frame_source=None, env_camera=None,
                            agent_mode=None, safety_gate=None,
                            start_worker=False,
                            overrides={'head.sweep_on_start': False})
    node.log = log
    node._on_scene(_scene(), at_s=0.0)
    assert 'head.sweep' not in log.names()
    node.destroy_node()
```

- [ ] **Step 2: Run it and watch it fail**

Run: `docker/run_tests.sh`
Expected: FAIL — `ModuleNotFoundError: No module named 'x2_greeter.ros.conversation_node'`

- [ ] **Step 3: Write `ros/conversation_node.py`**

Substitute the real Phase 1 adapter class names where marked.

```python
"""The conversational interaction node: wiring, and nothing else.

Every rule about what the robot says and when lives in core/. Every vendor
detail lives in the adapters. What is left here is the order things happen
in, which thread they happen on, and the parameter plumbing.

Threading: rclpy callbacks must not block, and transcription plus the
backend call is 2-4 seconds. Both run on one worker thread fed by a
single-slot queue; everything else stays on the executor. One lock guards
every Conversation mutation -- the state machine is a pure object and stays
that way, because thread-awareness in it would cost the only module that is
fully testable without a robot.
"""
from __future__ import annotations

import os
import queue
import threading
from typing import Optional

import rclpy
from ament_index_python.packages import get_package_share_directory
from rclpy.callback_groups import MutuallyExclusiveCallbackGroup, ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node

from x2_greeter.cognition.claude import ClaudeDialogueBackend
from x2_greeter.cognition.dialogue import BackendUnavailable, TurnLimits
from x2_greeter.cognition.transcriber import (
    SAMPLE_RATE_HZ, FasterWhisperTranscriber, TranscriptionUnavailable)
from x2_greeter.core import gaze
from x2_greeter.core.conversation import (
    CloseReason, Conversation, ConversationLimits, SessionState)
from x2_greeter.core.faces import MODE_ONCE
from x2_greeter.core.language import LanguagePolicy
from x2_greeter.core.scene import SceneConfig, observe
from x2_greeter.core.venue import load_venue
from x2_greeter.ros.agent_mode import AgentMode
from x2_greeter.ros.audio_source import VendorAudioSource
from x2_greeter.ros.env_camera import EnvCamera
from x2_greeter.ros.face import Face
from x2_greeter.ros.frame_source import FrameSource
from x2_greeter.ros.head import Head
# Phase 1 adapters -- substitute the real class names (see Step 0 of this task).
from x2_greeter.ros.speech import Speech
from x2_greeter.ros.gesture import Gestures
from x2_greeter.ros.safety import SafetyGate

FALLBACK_LINES = {
    'en': "Sorry, I didn't catch that.",
    'zh': '抱歉，我没有听清。',
}


class ConversationNode(Node):
    def __init__(self, face=None, speech=None, gestures=None, head=None,
                 transcriber=None, backend=None, audio_source=None,
                 frame_source=None, env_camera=None, agent_mode=None,
                 safety_gate=None, start_worker: bool = True,
                 overrides: Optional[dict] = None) -> None:
        super().__init__('x2_conversation')
        self._declare_parameters(overrides or {})

        self._sensor_group = MutuallyExclusiveCallbackGroup()
        self._service_group = ReentrantCallbackGroup()

        self.venue = load_venue(self._venue_path())
        self.conversation = Conversation(
            venue=self.venue,
            limits=self._conversation_limits(),
            language_policy=LanguagePolicy(
                default=self._p('language.default'),
                switch_confidence=self._p('language.switch_confidence')))
        self._scene_config = SceneConfig(
            confidence_min=self._p('presence.confidence_min'),
            individual_max_m=self._p('presence.individual_max_m'),
            scene_max_m=self._p('presence.scene_max_m'),
            child_stature_max_m=self._p('presence.child_stature_max_m'))

        self._lock = threading.Lock()
        self._work: queue.Queue = queue.Queue(maxsize=1)
        self._stop = threading.Event()

        self._scene = None
        self._frame = None
        self._base_frame = None
        self._child = False

        self.face = face if face is not None else self._build_face()
        self.speech = speech if speech is not None else self._build_speech()
        self.gestures = (gestures if gestures is not None
                         else self._build_gestures())
        self.head = head if head is not None else self._build_head()
        self.transcriber = (transcriber if transcriber is not None
                            else self._build_transcriber())
        self.backend = backend if backend is not None else self._build_backend()
        self.safety = safety_gate if safety_gate is not None else SafetyGate(self)

        self.audio = audio_source
        if self.audio is None and self._p('hearing.enabled'):
            self.audio = VendorAudioSource(
                node=self, on_utterance=self._enqueue,
                topic=self._p('hearing.topic'),
                max_utterance_s=self._p('hearing.max_utterance_s'),
                callback_group=self._sensor_group)
            self.audio.start()

        self.frames = frame_source
        self.env_camera = env_camera
        if self.env_camera is None and self._p('base_frame.use_env_camera'):
            self.env_camera = EnvCamera(
                node=self, topic=self._p('base_frame.rgb_topic'),
                on_frame=self._on_base_frame,
                rotate_180=self._p('base_frame.rotate_180'),
                callback_group=self._sensor_group)

        self.agent_mode = agent_mode
        if self.agent_mode is None:
            self.agent_mode = AgentMode(self, callback_group=self._service_group)
        if not self.agent_mode.set_only_voice():
            self.get_logger().error(
                'only_voice is NOT confirmed. There is no service to read it '
                'back; listen for the vendor agent answering over us.')

        self._timer = self.create_timer(0.1, self._on_tick,
                                        callback_group=self._sensor_group)
        self._worker = None
        if start_worker:
            self._worker = threading.Thread(target=self._worker_loop,
                                            daemon=True)
            self._worker.start()

    # -- sensors ----------------------------------------------------------

    def _on_frame(self, rgb, depth, at_s: float) -> None:
        """A head-camera frame. Kept as the newest, used on the next turn."""
        self._frame = rgb
        if self._base_frame is None and not self._p('base_frame.use_env_camera'):
            self._base_frame = rgb
        snapshot = observe(self._detections(rgb), rgb.shape, depth,
                           self._depth_scale(), self._scene_config, at_s)
        self._on_scene(snapshot, at_s)

    def _on_base_frame(self, rgb, at_s: float) -> None:
        if self._base_frame is None:
            self._base_frame = rgb

    def _on_scene(self, snapshot, at_s: float) -> None:
        self._scene = snapshot
        with self._lock:
            state = self.conversation.state
            if state is SessionState.IDLE:
                if (snapshot.person_count == 0
                        or self.conversation.cooldown_active(at_s)):
                    return
                self._child = snapshot.has_child
                opening = self.conversation.start(snapshot, at_s)
                language = self.conversation.language
            else:
                self.conversation.observed(snapshot, at_s)
                return
        # Outside the lock: everything below talks to hardware.
        if self._p('head.sweep_on_start'):
            self.head.sweep()
        self.speech.say(opening, language=language)
        self._greeting_finished(self._now())

    def _greeting_finished(self, at_s: float) -> None:
        with self._lock:
            self.conversation.greeted(at_s)

    def _on_tick(self) -> None:
        at_s = self._now()
        with self._lock:
            before = self.conversation.state
            self.conversation.tick(at_s)
            closed = (before is not SessionState.COOLDOWN
                      and self.conversation.state is SessionState.COOLDOWN)
        if closed:
            self._after_close()

    # -- the turn ---------------------------------------------------------

    def _enqueue(self, pcm: bytes, at_s: float) -> None:
        # A single slot, newest wins: if a second utterance arrives while the
        # first is still being transcribed, the newer one is what the person
        # actually just said.
        try:
            self._work.put_nowait((pcm, at_s))
        except queue.Full:
            try:
                self._work.get_nowait()
            except queue.Empty:
                pass
            try:
                self._work.put_nowait((pcm, at_s))
            except queue.Full:
                pass

    def _worker_loop(self) -> None:
        while not self._stop.is_set():
            try:
                pcm, at_s = self._work.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                self._handle_utterance(pcm, at_s)
            except Exception as exc:    # noqa: BLE001
                self.get_logger().error(
                    f'turn failed: {type(exc).__name__}')

    def _handle_utterance(self, pcm: bytes, at_s: float) -> None:
        with self._lock:
            if self.conversation.state is not SessionState.LISTENING:
                return
        # Before transcription, not after. This is the latency mitigation.
        self.face.show_thinking()

        try:
            utterance = self.transcriber.transcribe(pcm, SAMPLE_RATE_HZ)
        except TranscriptionUnavailable:
            self._say_fallback()
            return
        if utterance.is_empty:
            return

        with self._lock:
            self.conversation.heard(utterance, at_s)
            if self.conversation.state is not SessionState.THINKING:
                return
            history = self.conversation.history
            language = self.conversation.language
        scene = self._scene

        try:
            turn = self.backend.respond(
                base_frame=self._base_frame, frame=self._frame, scene=scene,
                venue=self.venue, history=history, utterance=utterance,
                language=language, child=self._child)
        except BackendUnavailable:
            with self._lock:
                fatal = self.conversation.backend_failed(self._now())
            self._say_fallback()
            if fatal:
                self._after_close()
            return

        with self._lock:
            self.conversation.replied(turn, self._now())

        if turn.gesture:
            self.gestures.play(turn.gesture)
        if turn.emoji:
            self.face.show(turn.emoji, MODE_ONCE)
        self.speech.say(turn.reply, language=turn.language)

        with self._lock:
            self.conversation.finished_speaking(self._now())
            closed = self.conversation.state is SessionState.COOLDOWN
        if closed:
            self._after_close()

    def _say_fallback(self) -> None:
        with self._lock:
            language = self.conversation.language
        self.speech.say(FALLBACK_LINES.get(language, FALLBACK_LINES['en']),
                        language=language)

    def _close(self, reason: CloseReason, at_s: float) -> None:
        with self._lock:
            self.conversation.close(reason, at_s)
        self._after_close()

    def _after_close(self) -> None:
        self.face.clear()
        self.head.centre()
        self._base_frame = None
        self._child = False
        with self._lock:
            reason = self.conversation.close_reason
        self.get_logger().info(
            f'session closed: {reason.value if reason else "unknown"}')

    # -- lifecycle --------------------------------------------------------

    def shutdown(self) -> None:
        self._stop.set()
        if self._worker is not None:
            self._worker.join(timeout=2.0)
        if self.audio is not None:
            self.audio.stop()
        self.head.centre()
        self.head.destroy()

    def destroy_node(self):
        try:
            self.shutdown()
        finally:
            return super().destroy_node()


def main(args=None) -> None:
    rclpy.init(args=args)
    node = ConversationNode()
    executor = MultiThreadedExecutor()
    executor.add_node(node)
    try:
        executor.spin()
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()
```

The private helpers left implicit above — `_declare_parameters`, `_p`, `_venue_path`, `_conversation_limits`, `_build_*`, `_detections`, `_depth_scale`, `_now` — are mechanical: `_p(name)` is `self.get_parameter(name).value` with the `overrides` dict consulted first; `_venue_path` is
`Path(get_package_share_directory('x2_greeter')) / 'config' / 'venues' / f"{self._p('venue.profile')}.yaml"`;
`_detections` and `_depth_scale` are exactly what the Phase 1 node already does with `FrameSource` — copy them from there rather than inventing a second way to reach the detector. Declare every parameter in `config/conversation.yaml` with a matching default so the node runs without a YAML file at all.

- [ ] **Step 4: Write `launch/conversation.launch.py`**

```python
"""Launch the conversational interaction node on PC2."""
import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    share = get_package_share_directory('x2_greeter')
    default_config = os.path.join(share, 'config', 'conversation.yaml')

    return LaunchDescription([
        DeclareLaunchArgument('config', default_value=default_config),
        DeclareLaunchArgument('venue', default_value='clothing_store'),
        Node(
            package='x2_greeter',
            executable='x2_conversation',
            name='x2_conversation',
            output='screen',
            emulate_tty=True,
            parameters=[
                LaunchConfiguration('config'),
                {'venue.profile': LaunchConfiguration('venue')},
            ],
        ),
    ])
```

The `venue` launch argument is what makes moving the robot from a shop to a mall atrium a launch-line change rather than an edit: `ros2 launch x2_greeter conversation.launch.py venue:=mall_atrium`.

- [ ] **Step 5: Add the entry point and install the launch file**

In `setup.py`:

```python
'console_scripts': [
    # ... the Phase 1 entry point stays
    'x2_conversation = x2_greeter.ros.conversation_node:main',
],
```

and, if `launch/` is not already installed by a `data_files` glob, add:

```python
(os.path.join('share', package_name, 'launch'), glob('launch/*.launch.py')),
```

- [ ] **Step 6: Run both suites**

Run: `docker/run_tests.sh`
Expected: PASS (16 new tests)

Run: `python -m pytest`
Expected: PASS — `test_layering.py` in particular. The node is the one place allowed to import both `ros/` and `core/`.

- [ ] **Step 7: Build on PC2 and confirm the node starts**

```bash
cd ~/x2_greeter_ws && colcon build --packages-select x2_greeter --symlink-install
source install/setup.bash
ros2 launch x2_greeter conversation.launch.py
```

Expected: the node comes up, logs whether `only_voice` was accepted, and waits. **Nothing here should produce motion** — gestures need `STAND_DEFAULT` and a person, face and head ship off. If the robot moves at this step, stop and find out why before going further.

- [ ] **Step 8: Commit**

```bash
git add x2_greeter_ws/src/x2_greeter/x2_greeter/ros/conversation_node.py \
        x2_greeter_ws/src/x2_greeter/launch/conversation.launch.py \
        x2_greeter_ws/src/x2_greeter/setup.py \
        x2_greeter_ws/src/x2_greeter/test/ros/test_conversation_node.py
git commit -m "feat(ros): the conversation node, launch file and entry point"
```

---

## Task 15: The simulator, the end-to-end test, and the deployment guide

**Files:**
- Modify: `x2_greeter_ws/src/x2_greeter/x2_greeter/sim/fake_robot.py`
- Create: `x2_greeter_ws/src/x2_greeter/test/test_conversation_end_to_end.py`
- Modify: `docs/DEPLOYMENT.md`
- Create: `docs/HARDWARE_BRINGUP.md`

**Interfaces:**
- Consumes: everything above.
- Produces: `sim.fake_robot.FakeRobot` extended with `.conversation` fields (`spoken`, `expressions`, `gestures`, `head_yaws`, `utterances`); `sim.fake_robot.run_scripted_conversation(script, venue, ...) -> ConversationTranscript`.

**The end-to-end test runs on the host, with no ROS, no network, no model and no robot.** It wires the real `Conversation`, the real `validate_turn`, the real venue loader and the real catalogues to scripted fakes and drives whole conversations through them. It is the only test that can answer "does a child in a mall atrium get a conversation that never asks their name" — because that answer is not in any one module.

- [ ] **Step 1: Write the failing end-to-end test**

Create `test/test_conversation_end_to_end.py`:

```python
"""Whole conversations, on the host, in milliseconds.

No ROS, no network, no Whisper model, no robot. The real state machine, the
real validation, the real venue files and the real catalogues, driven by
scripted fakes. This is the only place that can answer questions no single
module can -- 'does a child in a mall atrium get through a whole
conversation without being asked their name', for one.
"""
from pathlib import Path

import pytest

from x2_greeter.cognition.dialogue import (
    BackendUnavailable, Turn, TurnLimits, validate_turn)
from x2_greeter.cognition.transcriber import Utterance
from x2_greeter.core.conversation import (
    CloseReason, Conversation, ConversationLimits, SessionState)
from x2_greeter.core.faces import CATALOGUE as FACES
from x2_greeter.core.gestures import CATALOGUE as GESTURES
from x2_greeter.core.scene import AddressingMode, Person, SceneSnapshot
from x2_greeter.core.venue import load_venue

VENUES = Path(__file__).resolve().parents[1] / 'config' / 'venues'
ENABLED_GESTURES = ('wave', 'nod', 'heart')
ENABLED_EMOJI = ('thinking', 'happy', 'confused')


def _venue(name='clothing_store'):
    return load_venue(VENUES / f'{name}.yaml')


def _person(distance=1.4, child=False):
    return Person(bbox=(0, 0, 10, 10), confidence=0.9, distance_m=distance,
                  center_offset=0.0, stature_m=1.0 if child else 1.7,
                  likely_child=child)


def _scene(*people, mode=AddressingMode.INDIVIDUAL):
    people = people or (_person(),)
    return SceneSnapshot(
        people=people,
        subject=people[0] if mode is AddressingMode.INDIVIDUAL else None,
        mode=mode, at_s=0.0)


class _Driver:
    """Runs a whole conversation against scripted model output.

    `script` is a list of (heard_text, heard_language, raw_model_doc). The
    raw doc goes through the real validate_turn, so a script can express
    'the model invented a gesture' the same way the model would.
    """

    def __init__(self, venue, script, limits=None, scene=None):
        self.venue = venue
        self.script = list(script)
        self.convo = Conversation(venue=venue,
                                  limits=limits or ConversationLimits())
        self.scene = scene if scene is not None else _scene()
        self.spoken = []
        self.gestures = []
        self.expressions = []
        self.failures = 0

    def run(self, t0=0.0):
        self.spoken.append(self.convo.start(self.scene, at_s=t0))
        self.convo.greeted(at_s=t0 + 0.5)
        t = t0 + 1.0
        for heard, language, doc in self.script:
            if self.convo.state is not SessionState.LISTENING:
                break
            self.convo.heard(Utterance(heard, language, 0.95), at_s=t)
            self.expressions.append('thinking')
            if doc is None:
                self.failures += 1
                self.convo.backend_failed(at_s=t + 1.0)
                self.spoken.append("Sorry, I didn't catch that.")
                t += 2.0
                continue
            turn = validate_turn(doc, ENABLED_GESTURES, ENABLED_EMOJI,
                                 TurnLimits(), self.convo.language)
            self.convo.replied(turn, at_s=t + 1.5)
            if turn.gesture:
                self.gestures.append(turn.gesture)
            if turn.emoji:
                self.expressions.append(turn.emoji)
            self.spoken.append(turn.reply)
            self.convo.finished_speaking(at_s=t + 3.0)
            t += 4.0
        self.last_t = t
        return self


def _doc(reply, language='en', gesture=None, emoji=None, end=False):
    return {'reply': reply, 'language': language, 'gesture': gesture,
            'emoji': emoji, 'end': end}


# --- the happy path -------------------------------------------------------

def test_a_three_turn_conversation_opens_replies_and_closes():
    driver = _Driver(_venue(), [
        ('hi there', 'en', _doc('Hello! Looking for anything in particular?',
                                gesture='wave', emoji='happy')),
        ('just browsing', 'en', _doc('Take your time.', emoji='happy')),
        ('thanks, bye', 'en', _doc('See you!', gesture='wave', end=True)),
    ]).run()
    assert driver.spoken[0] == driver.venue.opening_for('en')
    assert len(driver.spoken) == 4
    assert driver.convo.close_reason is CloseReason.MODEL_ENDED
    assert driver.gestures == ['wave', 'wave']


def test_every_gesture_and_expression_the_model_asked_for_is_real():
    driver = _Driver(_venue(), [
        ('hi', 'en', _doc('Hi!', gesture='wave', emoji='happy')),
    ]).run()
    assert all(name in GESTURES for name in driver.gestures)
    assert all(name in FACES for name in driver.expressions)


def test_the_thinking_expression_precedes_every_reply():
    driver = _Driver(_venue(), [
        ('hi', 'en', _doc('Hi!', emoji='happy')),
        ('nice', 'en', _doc('Thanks!', emoji='happy')),
    ]).run()
    assert driver.expressions[0] == 'thinking'
    assert driver.expressions.count('thinking') == 2


# --- what the model gets wrong -------------------------------------------

def test_an_invented_gesture_never_reaches_the_robot():
    driver = _Driver(_venue(), [
        ('hi', 'en', _doc('Watch this.', gesture='backflip')),
    ]).run()
    assert driver.gestures == []
    assert driver.spoken[-1] == 'Watch this.', 'the words still happen'


def test_a_malay_reply_is_relabelled_not_spoken_as_malay():
    # Malay is next phase; there is no voice for it.
    driver = _Driver(_venue(), [
        ('hi', 'en', _doc('Selamat datang!', language='ms')),
    ]).run()
    assert driver.convo.language in ('en', 'zh')


def test_a_monologue_is_cut_to_something_a_robot_can_say_out_loud():
    driver = _Driver(_venue(), [
        ('tell me everything', 'en', _doc('This is a sentence. ' * 40)),
    ]).run()
    assert len(driver.spoken[-1]) <= 200
    assert driver.spoken[-1].endswith('.')


# --- language follows the person -----------------------------------------

def test_switching_to_chinese_mid_conversation_sticks():
    driver = _Driver(_venue(), [
        ('hello', 'en', _doc('Hi there!')),
        ('你们有帽子吗', 'zh', _doc('有的，在那边。', language='zh')),
        ('谢谢', 'zh', _doc('不客气！', language='zh', end=True)),
    ]).run()
    assert driver.convo.language == 'zh'
    assert driver.spoken[0] == driver.venue.opening_for('en'), (
        'the opening is in the venue default; the switch happens after '
        'somebody speaks')


# --- failure ---------------------------------------------------------------

def test_the_robot_speaks_on_every_backend_failure():
    driver = _Driver(_venue(), [
        ('hi', 'en', None),
        ('anyone there', 'en', None),
    ], limits=ConversationLimits(backend_failures_max=2)).run()
    assert driver.failures == 2
    assert driver.convo.close_reason is CloseReason.BACKEND_FAILED
    assert driver.spoken.count("Sorry, I didn't catch that.") == 2, (
        'the robot speaks in every failure case')


def test_one_failure_does_not_end_the_conversation():
    driver = _Driver(_venue(), [
        ('hi', 'en', None),
        ('hello again', 'en', _doc('Sorry about that -- hello!')),
        ('no worries', 'en', _doc('Thanks!', end=True)),
    ]).run()
    assert driver.convo.close_reason is CloseReason.MODEL_ENDED
    assert driver.convo.turns_taken == 2


# --- child mode -----------------------------------------------------------

def test_a_child_in_the_atrium_is_recognised_from_the_scene():
    scene = _scene(_person(distance=1.1, child=True))
    driver = _Driver(_venue('mall_atrium'), [
        ('hi robot', 'en', _doc('Hello! What are you playing?', emoji='happy')),
    ], scene=scene).run()
    assert scene.has_child is True
    assert driver.convo.mode is AddressingMode.INDIVIDUAL


def test_the_child_rules_are_present_in_the_prompt_the_backend_would_get():
    # The rules themselves are enforced in the prompt; this checks the
    # wording exists and says the thing that matters.
    from x2_greeter.cognition.claude import CHILD_RULES

    lowered = CHILD_RULES.lower()
    assert 'closer' in lowered
    assert 'promise' in lowered
    assert 'name' in lowered


# --- addressing -----------------------------------------------------------

def test_a_distant_crowd_gets_group_addressing_and_keeps_it():
    scene = _scene(*[_person(3.4)] * 5, mode=AddressingMode.GROUP)
    driver = _Driver(_venue('mall_atrium'), [
        ('hello', 'en', _doc('Hello everyone!')),
    ], scene=scene).run()
    assert driver.convo.mode is AddressingMode.GROUP
    driver.convo.observed(_scene(_person(1.0)), at_s=100.0)
    assert driver.convo.mode is AddressingMode.GROUP


# --- the limits hold on a real conversation -------------------------------

def test_the_turn_cap_stops_a_conversation_that_would_never_stop_itself():
    script = [(f'q{i}', 'en', _doc(f'a{i}')) for i in range(20)]
    driver = _Driver(_venue(), script,
                     limits=ConversationLimits(max_turns=8)).run()
    assert driver.convo.turns_taken == 8
    assert driver.convo.close_reason is CloseReason.MAX_TURNS


def test_silence_after_the_last_reply_ends_the_session():
    driver = _Driver(_venue(), [('hi', 'en', _doc('Hello!'))],
                     limits=ConversationLimits(silence_timeout_s=8.0)).run()
    driver.convo.tick(at_s=driver.last_t + 20.0)
    assert driver.convo.close_reason is CloseReason.SILENCE


def test_a_session_that_talks_forever_still_ends_inside_the_cap():
    limits = ConversationLimits(max_turns=99, silence_timeout_s=999.0,
                                session_max_s=180.0)
    script = [(f'q{i}', 'en', _doc(f'a{i}')) for i in range(60)]
    driver = _Driver(_venue(), script, limits=limits).run()
    assert driver.convo.close_reason is CloseReason.SESSION_TIMEOUT


@pytest.mark.parametrize('name', ['clothing_store', 'mall_atrium'])
def test_every_shipped_venue_can_hold_a_conversation(name):
    driver = _Driver(_venue(name), [
        ('hi', 'en', _doc('Hello!')),
        ('bye', 'en', _doc('See you!', end=True)),
    ]).run()
    assert driver.spoken[0]
    assert driver.convo.close_reason is CloseReason.MODEL_ENDED
```

- [ ] **Step 2: Run it and watch it fail**

Run: `python -m pytest x2_greeter_ws/src/x2_greeter/test/test_conversation_end_to_end.py -v`
Expected: FAIL until Tasks 1–8 and 13 are all in. If those are done, most of these should pass immediately — this test is composition, not new behaviour. Fix whatever it exposes at the source, not in the test.

- [ ] **Step 3: Extend `sim/fake_robot.py`**

Read the existing file first; Phase 1's `FakeRobot` already records gestures and speech. Add the conversation-era surfaces to the same object rather than creating a second fake:

```python
    # -- conversation surfaces (Phase 2) --------------------------------

    def show_emoji(self, name, mode=1):
        self.expressions.append((name, mode))
        return True

    def look_at(self, yaw_rad):
        self.head_yaws.append(float(yaw_rad))
        return True

    def hear(self, text, language='en', confidence=0.95):
        """Feed an utterance in as though the microphone had produced it."""
        from x2_greeter.cognition.transcriber import Utterance
        utterance = Utterance(text, language, confidence)
        self.utterances.append(utterance)
        return utterance
```

initialising `self.expressions = []`, `self.head_yaws = []` and `self.utterances = []` in `__init__` alongside the Phase 1 lists.

- [ ] **Step 4: Run the full host suite**

Run: `python -m pytest`
Expected: PASS

- [ ] **Step 5: Update `docs/DEPLOYMENT.md`**

This is the file the Ubuntu-laptop Claude follows to get code onto the robot. Add a Phase 2 section covering what actually differs:

1. **New Python dependency, on PC2 only.** `faster-whisper` and its `ctranslate2` backend. PC2 is the only host with internet, and PC1 is off limits for builds:
   ```bash
   python3 -m pip install --user faster-whisper
   ```
   The `small` model downloads on first use (~460 MB) into the HuggingFace cache. **Never point that cache under `$HOME/aimdk*`** — the SDK README reserves those paths for the system. Pre-warm it before the demo, not during:
   ```bash
   python3 -c "from faster_whisper import WhisperModel; WhisperModel('small', compute_type='int8')"
   ```
2. **`ANTHROPIC_API_KEY`** in the environment of the launching shell, exactly as Phase 1. Never in the YAML, never in git.
3. **Launching:**
   ```bash
   ros2 launch x2_greeter conversation.launch.py venue:=clothing_store
   ```
   The Phase 1 greeter and this node must not run at the same time — two nodes registered as MC input sources at priority 30, both trying to gesture, and both holding audio focus.
4. **What ships off:** face, head, sweep, gaze-follow, environment camera. Point at `docs/HARDWARE_BRINGUP.md` for the order to turn them on in.
5. **The `only_voice` check is a human one.** There is no service to read the mode back. Say something to the robot and listen: if a second voice answers, the vendor agent is still running its own dialogue and `only_voice` did not take.

- [ ] **Step 6: Write `docs/HARDWARE_BRINGUP.md`**

The order matters, and the reason each step comes where it does matters more than the step:

```markdown
# Phase 2 hardware bring-up

Everything below ships disabled. Turn one thing on at a time, in this
order, with a human within reach of the stop control for every step that
can produce motion. If a step misbehaves, turn that one flag back off
before moving on -- the point of the order is that you always know which
change caused what.

## 0. Before anything moves

- Robot in `STAND_DEFAULT` (200). The node never changes the motion mode
  and refuses to gesture in any other one.
- Remote controller powered on and in the operator's hands. It sits at
  priority 80 and overrides us at 30.
- `ros2 topic hz /aima/interaction/audio/processed` shows traffic when
  somebody speaks. If it does not, nothing downstream will work and no
  amount of config will fix it.

## 1. Conversation with no motion (default config)

Launch as shipped. Speak to the robot. You are checking three things:
- it answers, in the language you spoke;
- **no second voice answers** -- if one does, `only_voice` did not take,
  and the startup log will say so;
- it stops. Walk away mid-conversation and confirm the session closes
  rather than continuing to talk to an empty floor.

## 2. Gestures

Already proven in Phase 1, but re-check `heart` specifically: it is now
motion 1007 with area 3 (two-handed chest heart), and the Phase 1 refusal
was observed at 1007 with area 2. Stand beyond 1.0 m -- inside that, the
arm's-reach interlock refuses the gesture and the robot speaks anyway,
which is correct behaviour and looks like a bug if you are not expecting it.

## 3. The face — `face.enabled: true`

Start with the thinking expression alone: speak, and watch for it to
appear roughly a twentieth of a second later, well before the reply. If the
face does nothing, check the service path from `ros2 service list | grep -i
emoji` against `face.service` in the config before assuming the ids are wrong.

The ids themselves are pinned against `aimdk_msgs/srv/PlayEmoji` by
`test/ros/test_face_catalogue_ids.py`, so a wrong id fails in the container,
not on the robot.

## 4. The head — `head.enabled: true`

**This is the one to be careful with.** Yaw only, clamped to 0.262 rad in
two places. Before enabling it in a session with a person present:

- `ros2 topic echo /aima/hal/joint/head/state --once` -- confirm feedback.
- Enable `head.enabled` with `sweep_on_start` and `gaze_follow` still
  false. Confirm the head is commanded to centre at startup and does not
  drift.
- Then `sweep_on_start: true`. Watch one sweep with nobody in front. It
  must end centred.
- Then `gaze_follow: true`.

If the head ends a session anywhere but centred, turn it off and say so --
`sweep()` centres in a `finally` and `destroy()` centres, so an off-centre
head means one of those paths is not running.

## 5. The wide base frame — `base_frame.use_env_camera: true`

Find the real topic first:

```bash
ros2 topic list | grep -i -E 'stereo|front|fisheye'
ros2 topic hz <candidate>
```

Put it in `base_frame.rgb_topic`, then flip the flag. The check is
qualitative: ask the robot about something behind you. With the head camera
alone it cannot see it; with the 156-degree frame it can.

## 6. Venue tuning

`config/venues/*.yaml` is where the robot's knowledge of a place lives.
Facts it may state, topics to encourage, topics never to touch, and the
line it uses to hand a question to a human. Editing these needs no rebuild
with `--symlink-install`, only a relaunch.
```

- [ ] **Step 7: Commit**

```bash
git add x2_greeter_ws/src/x2_greeter/x2_greeter/sim/fake_robot.py \
        x2_greeter_ws/src/x2_greeter/test/test_conversation_end_to_end.py \
        docs/DEPLOYMENT.md docs/HARDWARE_BRINGUP.md
git commit -m "feat(sim): end-to-end conversation test, and the hardware bring-up order"
```

---

## Deferred, deliberately

These are named here so nobody has to guess whether they were forgotten.

| Item | Why it is not in this plan |
|---|---|
| Malay | 这期只做英中，马来语下期. Adding it to `ALLOWED_LANGUAGES` without a TTS voice produces a robot that answers in a language it cannot pronounce. `test_the_allowed_languages_are_exactly_this_phases_two` fails if somebody tries. |
| Raw audio, our own VAD/AEC/denoise | 先跑 only_voice 和唤醒，成熟了再来做. The `AudioSource` port exists precisely so this lands as one new adapter. |
| Locomotion of any kind | Out of scope by decision. The node never issues a locomotion command and never changes the motion mode. |
| Phase 1 gesture bench-check (spec §18 item 10) | Scheduled for the next hardware window, starting with `heart` at 1007/area 3. |
| Memory across sessions | Nothing is persisted, by design — no transcript, no image, no audio. A robot that remembers the person from last Tuesday is a different design conversation. |

## Self-review notes

- **Spec coverage.** §1–§6 land in Tasks 1–8; §7 (hearing) in Tasks 5 and 9; §8 (dialogue) in Tasks 6–7; §9 (faces) in Tasks 3 and 11; §10 (gaze/head) in Tasks 2 and 12; §11 (turn contract) in Task 6; §12 (session limits) in Task 8; §13 (latency) in Tasks 11 and 14; §14 (venues) in Task 1; §15 (config) in Task 13; §16 (node) in Task 14; §17 (testing) in Task 15; §18 (deferred) above.
- **One spec correction is carried in this plan and must be read before executing.** §9.3 states that `emotion_id` is a bare `uint8` with no enum message anywhere in the SDK, and that the ids exist only in a documentation table. That is wrong: `PlayEmoji.srv` declares the whole enum as service constants. Task 3 pins `core/faces.py` against `aimdk_msgs.srv.PlayEmoji`, and a conservative eight-expression set ships enabled rather than the empty list §9.3 proposes — because with an empty list the thinking expression is dead code, and §13's only latency mitigation goes with it.
